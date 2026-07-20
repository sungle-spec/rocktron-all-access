# Hardware Test Plan

**Part 1 (RE disambiguation) is done** — the 2026-07-20 T7 session resolved
6 of 7 items outright (bank size location, switch-type table, PER-PR
channel byte, filter-grid model, song/set OFF markers, plus most of the
enum spot-checks) with zero contradictions against the historical T2/T5/T6
data. Full write-up: [REVERSE_ENGINEERING.md §7](REVERSE_ENGINEERING.md).
What's left: **Part 1.5 (T8)** — 8 small follow-up captures — and **Part 2
(write-path validation)**, which is blocked on the Write All issue below.

---

## ⚠️ Known issue — Write All drops the last preset (unresolved)

**Symptom (reported 2026-07-21, reproducible):** after a full Write All
restore, the **last preset (PR120) is blank** and **some global options are
corrupt**. Confirmed reproducible across multiple restores and even when the
source is a known-good second All Access unit. A repeat Write All fails
identically (not random jitter).

**Not the editor:** the server log confirms Write All streams all 145 frames
/ 43,647 bytes at the spec'd chunk=1 / 15 ms-per-byte pacing; the last
observed transfer completed 145/145 frames in 790 s (~18 ms/byte actual —
slightly *slower* than 15 ms, so overflow-from-too-fast is not the cause).
The baseline dump is complete (PR120 = a real preset, not blank).

**Leading hypothesis:** the device commits each preset frame only when the
*next* frame begins arriving. That works for PR1–PR119 (each is followed by
another frame) but leaves **PR120 in an uncommitted buffer** — what follows
it is a globals frame, not another preset — so it is never flushed. The same
preset→globals boundary is where the corrupt options cluster. One mechanism,
both symptoms.

**To resolve (next session):** capture a **post-restore dump** (Write All the
baseline, then Read All → Save) and diff it against baseline with
`tools/analyze_captures.py baseline.syx post_restore.syx`. That shows exactly
whether PR120's frame comes back all-zeros vs partially-written, and which
option bytes moved — which picks between the candidate fixes:
- **Append a trailing "flush" frame** after the last preset (e.g. a duplicate
  PR120 frame, or a harmless throwaway) so the real PR120 commits.
- **Re-send just PR120** after the bulk dump (single-frame Write Preset).
- **A boundary-only settle delay** at the preset→globals transition (note the
  existing warning that a *general* inter-frame pause makes things worse —
  see the pacing comment in `app.py::dump_send`).

Any fix must be validated on hardware (Part 2, W-series) before it ships.
Until then, the reliable restore path is the user's **second unit**.

---

## 0. Ground rules (do not skip)

1. **Baseline first.** Read All → Save .syx → keep the file safe. Every diff
   is against this file, and it is your restore point.
2. **One edit → CTR STORE where needed → dump → diff.** The T2/T3 sessions
   lost several edits (bank size 10, PED1 PER-PR, SET1) almost certainly
   because CTR STORE wasn't pressed. Never batch RE-capture edits.
3. **Diff as you go:** `python tools/analyze_captures.py baseline.syx new.syx`
   prints the annotated per-frame diff and re-asserts every known anchor. If
   an anchor FAILs, stop and investigate.
4. **⚠️ Never press CTR STORE on SETUP P8 (Preset Reinit) or P9 (Memory
   Reinit).** P9 code 230 = full factory reset.
5. **Confirm the LCD shows the intended preset immediately before CTR STORE**
   — a "recall preset" step can silently fail (T2.D landed on PR1 not PR3).
6. **Power-cycle after any Write All** — names/globals are RAM-cached and only
   refresh on boot.
7. **All page numbers below are manual-verified** (SETUP P1-P10, MIDI P1-P7,
   SONG/SET P1-P3, CUSTOM P1-P2). Channel fields display the 4-char channel
   *name*, not `CH<n>`.

---

## Part 1 — RE disambiguation captures ✅ done 2026-07-20

Results in [REVERSE_ENGINEERING.md §7](REVERSE_ENGINEERING.md). Bank size →
tail `0x0C`; switch-type table `0x46-(SW#-1)*2`, `LATCH/MOM/HOLD=0/1/2`;
PER-PR channel byte = slot `+0` (0-based); filter frame = flat 18-type +
16-channel list at `0x06+idx*2`; PC status + `KPRS>` confirmed; song/set have
no OFF state (manual-confirmed). Quirk: **REMOTE mode destabilizes the CUSTOM
MIDI page** (device crash, power-cycle needed) — revert to BANK before any
CUSTOM editing.

## Part 1.5 — T8 capture list (8 dumps, ~30 min)

Device starts at freshly-restored baseline (**BANK** mode, Bank Size 5).
Save each as `t8_<id>.syx`. `2ND` activates the secondary button row;
`→`/`←` move between pages; Left/Center/Right select the display fields via
the `INC`/`DEC` pair under each. **REMOTE (T8 "2b") is already captured —
reuse T7's `5a.syx`** (an isolated BANK→REMOTE dump; only tail `0x0A` moved,
`00→02`).

**Phase 1 — REMOTE-mode bug verification (BANK mode).** The headline check.
| id | Steps | Confirms |
|----|-------|----------|
| 1a | Mode = BANK. `2ND → CUSTOM` (P1: `PR<n> CMD<n> <type>`). On a scratch preset set CMD1 → `N ON>` (`→` to P2, pick channel/note/vel), CMD2 → `C CH>`, CMD3 → `P CH>` — the same three-edit sequence that crashed in REMOTE. Does it stay stable? `2ND` off, dump. | If stable → the crash is REMOTE-specific (device is a rack slave in REMOTE, per the manual), not an editor/firmware fault. Diff also yields 3 more CMD-type anchors (`P CH>`=0x04 etc.). `→` only activates on `>`-types — pick the type first. |

**Phase 2 — Operating Mode byte.** Resolves frame-122 `0x08` vs tail `0x0A`.
Baseline = the BANK point; `5a.syx` = the REMOTE point; only SONG is missing.
| id | Steps | Confirms |
|----|-------|----------|
| 2a | `SETUP` P1 (`mode`) → Right → `SONG`. Dump. Then Right → `BANK` (restore, no dump needed). | Which byte tracks BANK→SONG. BANK (baseline) + SONG (2a) + REMOTE (`5a`) then fully map the field. |

**Phase 3 — Bank Style byte.** `SETUP` P3; values are FIRST / CURNT / **OFF**.
| id | Steps | Confirms |
|----|-------|----------|
| 3a | `SETUP` → `→→` to P3 (`bnk style`) → Right → `CURNT`. Dump. | Bank Style's real byte + CURNT code (recall `0x46` is SW1's switch type, so this is genuinely unknown). |
| 3b | P3 → Right → `OFF`. Dump. | OFF code. |
| 3c | P3 → Right → `FIRST`. Dump. | FIRST code + restore. |

**Phase 4 — remaining RE gaps** (each independent; skip any freely).
| id | Steps | Confirms |
|----|-------|----------|
| 4a | `SETUP` → `→`×6 to P7 (MIDI filtering, two fields: `<msg/ch> <bloc/merg>`) → Left → `CHANNEL 1`, Right → `BLOC`. Dump. **Do not advance to P8/P9.** | The 16 per-channel filter cells: predicts offset `0x06 + 18×2 = 0x2A`. |
| 4b | `SETUP` → `→`×5 to P6 (`<ch> start<n>`) → Left → `CH 2`, Right → toggle its value (0↔1). Dump. | Per-channel Starting Preset offsets (only CH1 at `0x6A` known). |
| 4c | `SETUP` P4 → Left → `PED2`, Right → `PER PR`. `2ND` off, recall a scratch preset (check LCD). `2ND → MIDI` → P2 (`<sw> <ch> cn<n>`): Left `PED2`, Center `CH 8`, Right `cn 30`; `→` P3 (`<sw> ON<v> OFF<v>`): Left `PED2`, Center `100`, Right `40`. CTR STORE. Dump. | chan-id 16 = PED2 (only PED1=17 confirmed). |

**After the session:** hand me the `t8_*.syx` files; I diff each with
`analyze_captures.py`, confirm/refute the predictions, then update
`encode_globals`/`decode_globals` (Operating Mode, Bank Style relocation),
the filter/starting-preset/PED2 findings, docs, and tests — same loop as T7.

## Part 2 — Write-path validation (editor → device) — BLOCKED

**Do not run until the "Write All drops the last preset" issue above is
fixed and confirmed** — otherwise every W-row is confounded by the boundary
corruption. Once fixed, run these (mode = BANK, never REMOTE):

| # | Test | Steps | Pass criteria |
|---|------|-------|---------------|
| W1 | **Custom MIDI full-slot write** | Scratch preset: CMD1=`C CH>` ch3 cc21 v101, CMD4=`P CH>` ch6 d1=42, CMD5=`C CH>` ch14 cc99 v7. Save, Write All, power-cycle. | CUSTOM pages show all three verbatim; Read All matches. |
| W2 | **SysEx-toggle regression** | Set that preset's SysEx ON before W1's Write All. After power-cycle check SYSX P2. | Still ON — CMD writes at 0xCE..0xF5 must not disturb `0xF6`. |
| W3 | **Space = 0x2E round-trip** | Rename a preset `A B C`, Write All, power-cycle. | LCD shows `A B C`; Read All shows `0x2E` at the spaces. |
| W4 | **PC-map clear-to-OFF** | Clear a mapped PC slot, Write All, set MIDI P5 = MAP. | Device MIDI P6 shows OFF; Read All shows `0x78`. |
| W5 | **Switch Type write** | Set SW1 = HOLD in the editor, Write All, power-cycle. | MIDI P4 shows SW1 = HOLD; Read All `0x46 = 0x02`. |
| W6 | **Bank Size write** | Set Bank Size = 10, Write All, power-cycle. | SETUP P2 = 10; layout 10-preset/5-IA; Read All tail `0x0C = 0x02`. |
| W7 | **Song slot 11-15** | At Bank Size 15, set a song's SW11-15 to distinct presets, Write All, power-cycle. | Device SONG page shows them; Read All matches (exercises the Part-A 15-slot fix). |
| W8 | **Last-preset integrity** | After any Write All, verify **PR120** specifically is intact (not blank). | Directly checks the known issue's fix. |
| W9 | **Write All timing** | Time W1. | ~11-13 min, no Buffer Overflow, all 145 frames, functional after boot. |
| W10 | **Restore** | Write All baseline back, power-cycle. | Rig identical to session start (spot-check 3 presets + a song + PC map + Bank Size). |

## Part 3 — Live-editor sign-off matrix (browser + device)

One pass in a fresh browser session:

| Flow | Check |
|------|-------|
| Port hot-plug | Unplug/replug interface → dropdowns repopulate ~4 s, LEDs green after reselect. |
| Read All | 145 frames, no partial-dump banner, all tabs populated. |
| Preset edit → Write Preset | Rename + PC-slot change via single-preset write; device shows it without a full Write All. |
| Virtual Foot Controller | Preset click recalls on device; IA click toggles the CC; expression sliders move the target; quick send works. |
| MIDI monitor | Front-panel PC/CC appear as RX rows. |
| Save/Load | Save .syx after edits; reload; byte-identical re-save. |

## Recording results

- Keep every capture: `t8_<id>_<desc>.syx` alongside the baseline.
- Log outcomes (byte offsets, pass/fail) into REVERSE_ENGINEERING.md §7 and
  here — replace each row's question with the answer and mark ✅.
- Re-run `tools/uat_headless.py` and `tools/analyze_captures.py` after any
  code change; both must stay green.
