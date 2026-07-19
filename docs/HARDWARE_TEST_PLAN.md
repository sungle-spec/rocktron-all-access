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
All needed). Each row = one capture cycle: make the edit, CTR STORE, `SYSX →
DUMP → CTR STORE` with the editor's **Read All** armed, Save .syx, diff.

| # | Question | Device steps | The diff settles |
|---|----------|--------------|------------------|
| 1a | Where does Bank Size live? | SETUP P2: 5 → **10**. Dump. | If frame-122 `0x44` → `0x04`, bank size confirmed at `0x44`; if another byte moves, the current `encode_globals` target is wrong (see the 0x44 caution in RE.md). |
| 1b | Bank Size 15 code | SETUP P2: 10 → **15**. Dump. | Confirms/refutes `0x06` for 15. |
| 2a | Switch-type cell map (1/3) | SETUP P4: SW1 → MOM. Dump. | Single-cell diff in `0x28..0x48`. |
| 2b | Switch-type cell map (2/3) | SW5 → HOLD. Dump. | Second point → offset rule. |
| 2c | Switch-type cell map (3/3) | SW15 → MOM. Dump. | Third point confirms the rule; also resolves whether T5's `0x44/0x46` were types or bank size/style (cross-checks #1a). |
| 3 | PER-PR header semantics | On one preset: PER-PR SW2 = CH6/CC10/ON127/OFF0, CTR STORE, **then** PER-PR SW9 = CH12/CC20/ON100/OFF50, CTR STORE. Dump once. | If preset-frame `0x40/0x42` hold only CH12/SW9, it's a most-recently-configured record; if two channel bytes appear, channels are per-slot. |
| 4a | Filter grid (channel stride) | MIDI P4: set `C CH` filter → BLOC for CH1. Dump. | First cell. |
| 4b | Filter grid | Same message type → BLOC for CH3. Dump. | Channel stride (combined with T3's type stride of 4). |
| 5 | Enum stragglers | SETUP P1 → REMOTE. Dump. Then MIDI P5 → ON. Dump. | Confirms `REMOTE = 0x02` (frame-122 `0x08`) and `PC status ON = 0x01` (tail `0x08`) — both currently best-guess. |
| 6 | CMD type-enum spot check | On a scratch preset: CMD2 → `KPRS>` ch2 d1=10 d2=20; CTR STORE. Dump. | Expected: `0xD6 = 0x02`. One hit validates the inferred manual-order codes between the three observed anchors. |
| 7 | Song/set OFF markers | Set SONG150 slot 1 → OFF; SET10 bank 50 → OFF. CTR STORE. Dump. | Whether songs/sets use the PC-map convention (value = count: 120/150) for OFF. |

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
