# Hardware Test Plan

**Part 1 (RE disambiguation) is done** — the 2026-07-20 T7 session resolved
6 of 7 items outright (bank size location, switch-type table, PER-PR
channel byte, filter-grid model, song/set OFF markers, plus most of the
enum spot-checks) with zero contradictions against the historical T2/T5/T6
data. Full write-up: [REVERSE_ENGINEERING.md §7](REVERSE_ENGINEERING.md).
What's left: **Part 1.5 (T8)**, three small follow-up captures, and **Part 2
(write-path validation)**, which has never been run against real hardware.

Budget: one sitting, roughly 1–2 hours including one full Write All cycle
(~11 min). Everything below is ordered so a partial session still produces
usable results.

---

## 0. Ground rules (do not skip)

1. **Baseline first.** Read All → Save .syx → keep the file somewhere safe.
   Every later diff is against this file, and it is your restore point.
2. **One edit → CTR STORE → dump → diff.** The T2/T3 sessions lost several
   edits (bank size 10, PED1 PER-PR, SET1) almost certainly because CTR
   STORE wasn't pressed between edits. Never batch RE-capture edits.
3. **Diff as you go:** after each capture,
   `python tools/analyze_captures.py baseline.syx new.syx` prints the
   annotated per-frame diff and re-asserts every known layout anchor. If an
   anchor FAILs, stop and investigate before continuing.
4. **Power-cycle after any Write All** — names and globals are RAM-cached on
   this firmware and only refresh on boot.

---

## Part 1 — RE disambiguation captures ✅ done 2026-07-20

Results in [REVERSE_ENGINEERING.md §7](REVERSE_ENGINEERING.md). Summary:
bank size relocated to tail `0x0C`; switch-type table fully resolved
(linear, `0x46-(SW#-1)*2`, `LATCH/MOM/HOLD=0/1/2`); PER-PR channel byte
resolved (slot `+0`, 0-based); filter frame resolved (no channel field —
flat 18-type + 16-channel list, `0x06+idx*2`); PC status and one CMD type
(`KPRS>`) confirmed; song/set OFF markers found to not exist on the device.
One quirk found: **REMOTE mode destabilizes the CUSTOM MIDI page** — a
device crash requiring a power cycle. Revert to BANK mode before any
CUSTOM MIDI editing.

## Part 1.5 — T8 follow-up (small, ~15 min)

Three loose ends Part 1 didn't fully close:

| # | Question | Device steps | What it settles |
|---|----------|--------------|------------------|
| T8.1 | Operating Mode's real byte(s) | Confirm mode is **BANK**. `SETUP` P1 → Right → `SONG`. Dump. `SETUP` P1 → Right → `REMOTE`. Dump. Then back to `BANK`. Dump. | Does frame-122 `0x08` ever move? T7 found REMOTE changes tail `0x0A` instead — this checks whether SONG behaves the same way, and restores BANK before T8.2/T8.3. |
| T8.2 | Bank Style's real location | `SETUP` P3 (`bnk style`) → cycle FIRST→CURNT→NONE, one dump per value (3 dumps). | `0x46` is proven to be SW1's switch type, so Bank Style's byte is unknown — this finds it. |
| T8.3 | Write-path validation (see Part 2, W-series) | — | First hardware test of any of these write paths — everything in Part 1/T8.1/T8.2 was Read-All-only. |

## Part 2 — Write-path validation (editor → device)

Never run against real hardware — every write path below has only been
checked with headless byte-splice tests. Do this after T8.1/T8.2 so any
operating-mode/bank-style fix lands first.

| # | Test | Steps | Pass criteria |
|---|------|-------|---------------|
| W1 | **Custom MIDI full-slot write** | In the editor (mode = BANK, not REMOTE — see the quirk above): on a scratch preset set CMD1=`C CH>` ch3 cc21 v101, CMD4=`P CH>` ch6 d1=42, CMD5=`C CH>` ch14 cc99 v7. Save, **Write All**, power-cycle. | Device CUSTOM pages show all three commands verbatim; recalling the preset transmits them (watch the editor's MIDI monitor); Read All → bytes match what was written. |
| W2 | **SysEx-toggle regression on hardware** | Before W1's Write All, set the same preset's SysEx string ON. After power-cycle, check SYSX P2. | Still ON — CMD writes at the confirmed offsets (0xCE..0xF5) must not disturb `0xF6` on the real unit. |
| W3 | **Space = 0x2E name round-trip** | Rename a preset to `A B C`, Write All (same cycle as W1), power-cycle. | LCD shows `A B C` with spaces; Read All returns `0x2E` at the space positions. |
| W4 | **PC-map clear-to-OFF** | Clear one previously-mapped PC slot in the editor, Write All (same cycle), set MIDI P5 = MAP. | Device MIDI P6 shows that PC = `OFF`; sending that PC does nothing; Read All shows `0x78`. |
| W5 | **Switch Type write** | In the editor's Channels & Switches tab, set SW1 = HOLD, Write All (same cycle), power-cycle. | Device MIDI P4 shows SW1 = HOLD; Read All confirms `0x46 = 0x02`. |
| W6 | **Bank Size write** | Set Bank Size = 10 in the editor, Write All (same cycle), power-cycle. | Device SETUP P2 shows `10`; footswitch layout changes to 10 preset / 5 IA; Read All confirms tail `0x0C = 0x02`. |
| W7 | **Write All timing sanity** | Time the W1 Write All. | Completes ~11 min, no `Buffer Overflow`, all 145 frames accepted, device fully functional after power-cycle. |
| W8 | **Restore** | Write All the Part-0 baseline back. Power-cycle. | Rig identical to the start of the session (spot-check 3 presets + a song + the PC map + Bank Size back to original). |

## Part 3 — Live-editor sign-off matrix (browser + device)

The quick end-to-end sweep that has never been formally signed off with
hardware attached. One pass, ticking each row in a fresh browser session:

| Flow | Check |
|------|-------|
| Port hot-plug | Unplug/replug the MIDI interface → dropdowns repopulate within ~4 s, LEDs green after reselect. |
| Read All | 145 frames captured, partial-dump banner absent, all tabs populated. |
| Preset edit → Write Preset | Rename + PC-slot change on one preset via the single-preset write; device shows it without a full Write All. |
| Virtual Foot Controller | Preset switch click recalls on the device (PC chain fires); IA click toggles the mapped CC; expression sliders move the target device; quick send works. |
| MIDI monitor | Incoming PC/CC from the device's front panel appear as RX rows. |
| Save/Load | Save .syx after edits; reload the file; byte-identical re-save. |

## Recording results

- Keep every capture file: `t7_<item>_<desc>.syx` alongside the baseline.
- Log outcomes (byte offsets found, pass/fail per W/Part-3 row) directly
  into REVERSE_ENGINEERING.md §7 and this file — replace each table row's
  question with the answer and mark it ✅.
- Re-run `tools/uat_headless.py` and `tools/analyze_captures.py` after any
  code change the results trigger; both must stay green.
