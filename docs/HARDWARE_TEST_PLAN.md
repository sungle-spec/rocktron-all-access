# Hardware Test Plan — the one remaining device session

Everything decodable from existing captures has been decoded (see
[REVERSE_ENGINEERING.md](REVERSE_ENGINEERING.md)); the 31-check headless
harness covers every byte-splice path with no hardware. What's left is the
work that **only a real All Access can settle**: a handful of
reverse-engineering disambiguation captures, and on-device validation of the
write paths that changed since the last device session.

Budget: one sitting, roughly 2–3 hours including two full Write All cycles
(~11 min each). Everything below is ordered so a partial session still
produces usable results.

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

## Part 1 — RE disambiguation captures (device → editor)

Read-only for the device's own data (front-panel edits + dumps; no Write
All needed). Each row = one capture cycle: make the edit, `SYSX → DUMP →
CTR STORE` with the editor's **Read All** armed, Save .syx. Edits are
**cumulative** — diff each capture against the **previous** one, not the
baseline.

**Two different "P4" pages — don't mix them up:**
- `SETUP P4` = IA operating status per switch — `GLOBAL` / `PER PR` only.
- `MIDI P4` = **Switch Type** — `LATCHING` / `MOMENTARY` / `HOLD`.
Both pages (and MIDI P2/P3) only list switches that are **currently IA**,
i.e. switch# > Bank Size. At Bank Size 15 no switches are IA, so MIDI P4
shows nothing — the switch-type captures below therefore run at **Bank
Size 1** (all 15 switches IA), after the bank-size captures are done.

Also remember: channel fields display the **4-char channel NAME** (from
SETUP P5), not `CH<n>` — "set Center to CH 6" means "set it to whatever
CH6 is named on your rig".

Run in this order:

| # | Question | Device steps (`2ND` on first; `→`/`←` = page, Left/Center/Right = fields) | The diff settles |
|---|----------|--------------|------------------|
| 1a | Where does Bank Size live? | `SETUP` → `→` to P2 (`bnk size`) → Right INC to **10**. Dump. | If frame-122 `0x44` → `0x04`, bank size confirmed at `0x44`; if another byte moves, `encode_globals` is writing the wrong byte (see the 0x44 caution in RE.md). |
| 1b | Bank Size 15 code | `SETUP` P2 → **15**. Dump. | Confirms/refutes `0x06` for 15. |
| 1c | Bank Size 1 code — the 0x44 tiebreaker | `SETUP` P2 → **1**. Dump. | T5 saw `0x44=0x02` after changing bank size **and** SW1/SW2 types together. This capture changes bank size to 1 **alone** — if `0x44` → `0x02` here, T5's attribution stands; if not, `0x44` belongs to switch types. Also leaves all 15 switches IA for the next rows. |
| 2a | Switch-type cell map (1/3) | `MIDI` → `→`×3 to P4 (`<sw> <type>`) → Left `SW1`, Right `MOMENTARY`. Dump. | Single-cell diff in frame-122 `0x28..0x48`. |
| 2b | Switch-type cell map (2/3) | MIDI P4 → Left `SW5`, Right `HOLD`. Dump. | Second point → the SW#→offset rule. |
| 2c | Switch-type cell map (3/3) | MIDI P4 → Left `SW15`, Right `MOMENTARY`. Dump. | Third point confirms the rule and cross-checks 1c. |
| 3 | PER-PR header semantics | `SETUP` → `→`×3 to P4 → Left `SW2`, Right `PER PR`; Left `SW9`, Right `PER PR`. `2ND` off, recall a scratch preset. `2ND` → `MIDI` → `→` to P2 (`<sw> <ch> cn<n>`): Left `SW2`, Center `CH 6`, Right `cn 10`; `→` to P3 (`<sw> ON<v> OFF<v>`): Left `SW2`, Center `127`, Right `0`. Back to P2: Left `SW9`, Center `CH 12`, Right `cn 20`; P3: Left `SW9`, Center `100`, Right `50`. CTR STORE the preset. Dump. | If preset-frame `0x40/0x42` hold only the last pair (CH12/SW9), it's a most-recently-configured record; if two channel bytes appear, channels are stored per-slot. |
| 4a | Filter grid (channel stride) | `SETUP` → `→`×6 to P7 (MIDI filtering, `<msg> <ch> <bloc/merg>`): Left `CTR CHANGE`, Center `CH 1`, Right `BLOC`. Dump. | First cell of the CC row. |
| 4b | Filter grid | SETUP P7: Left `CTR CHANGE`, Center `CH 3`, Right `BLOC`. Dump. | Channel stride (combined with T3's `CH2`/`CH5` cells at `0x08`/`0x0C`). |
| 5a | REMOTE enum | `SETUP` P1 (`mode`) → Right → `REMOTE`. Dump. | Confirms `REMOTE = 0x02` at frame-122 `0x08` (currently best-guess). |
| 5b | PC-status ON enum | `MIDI` → `→`×4 to P5 (`prog change`) → Right → `ON`. Dump. | Confirms `ON = 0x01` at tail `0x08`. |
| 6 | CMD type-enum spot check | `2ND` → `CUSTOM` → P1 (`PR<n> CMD<n> <type>`): Left → a scratch preset, Center → `CMD2`, Right → cycle to `KPRS>`. (`→` only activates on `>`-types — pick the type first.) `→` to P2: Left → CH2's name, Center → `10`, Right → `20`. Dump. | Expected `0xD6 = 0x02`. Note: the front-panel cycle order starts at `NONE` — the **stored** byte enum is the manual's CUSTOM P1 list order (`NOFF>`=0 … `NONE`=17), so don't infer codes from the display cycle. |
| 7 | Song/set OFF markers | `2ND` → `SONG/SET`: SONG150 slot 1 → `OFF`; SET10 bank 50 → `OFF`. CTR STORE. Dump. | Whether songs/sets use the PC-map OFF convention (value = count: 120/150). |

**After Part 1:** update REVERSE_ENGINEERING.md with each answer, adjust
`app.py` (`BANK_SIZE_BYTES`, switch-type table, filter map, song/set OFF)
and extend `tools/uat_headless.py` accordingly. If #1a/2c contradict the
current `encode_globals` offsets, fix `encode_globals` **before** doing any
Part-2 Write All that includes global edits.

## Part 2 — Write-path validation (editor → device)

These exercise the code paths that changed since the last device session.
Run after Part 1 (so any `0x44` surprise is known first).

| # | Test | Steps | Pass criteria |
|---|------|-------|---------------|
| W1 | **Custom MIDI full-slot write** | In the editor: on a scratch preset set CMD1=`C CH>` ch3 cc21 v101, CMD4=`P CH>` ch6 d1=42, CMD5=`C CH>` ch14 cc99 v7. Save, **Write All**, power-cycle. | Device CUSTOM pages show all three commands verbatim; recalling the preset transmits them (watch the editor's MIDI monitor); Read All → bytes match what was written. |
| W2 | **SysEx-toggle regression on hardware** | Before W1's Write All, set the same preset's SysEx string ON. After power-cycle, check SYSX P2. | Still ON — CMD writes at the new offsets must not disturb `0xF6` on the real unit. |
| W3 | **Space = 0x2E name round-trip** | Rename a preset to `A B C`, Write All (same cycle as W1), power-cycle. | LCD shows `A B C` with spaces; Read All returns `0x2E` at the space positions. |
| W4 | **PC-map clear-to-OFF** | Clear one previously-mapped PC slot in the editor, Write All (same cycle), set MIDI P5 = MAP. | Device MIDI P6 shows that PC = `OFF`; sending that PC does nothing; Read All shows `0x78`. |
| W5 | **Write All timing sanity** | Time the W1 Write All. | Completes ~11 min, no `Buffer Overflow`, all 145 frames accepted, device fully functional after power-cycle. |
| W6 | **Restore** | Write All the Part-0 baseline back. Power-cycle. | Rig identical to the start of the session (spot-check 3 presets + a song + the PC map). |

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
