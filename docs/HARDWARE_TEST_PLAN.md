# Hardware evidence and acceptance plan

This is pending work, not hardware sign-off. The byte map combines tracked T0–T6
captures and T7 session reports whose raw files/photo are missing here. No
complete reverse-engineering or universal restore claim is supported.

## Reported restore behavior and limits

The 2026-07-21 session report describes blank PR120 and altered globals after
bulk restores. A second unit reportedly produced a similar device-to-device
symptom. That excludes this app as the sole explanation, but does not establish
firmware causation, rule out an additional app defect, or measure editor delivery.
One factory-reset-then-restore recovery was reported. It is not a universal path.
No stale-memory, flush, commit, or next-frame mechanism is established.

Host send counts/logs only account for attempted API output. They are not a
recording of MIDI wire traffic or acknowledgement of storage on the device.
The legacy one-byte message chunking may be unsupported/backend-dependent.
Bulk and single-preset sends default disabled; `AA_ENABLE_EXPERIMENTAL_WRITE=1`
is an explicit experimental opt-in, not evidence of transport correctness.
Standalone-preset receive, target addressing, and envelope behavior are unknown.

## Before experiments

Keep untouched baseline and every subsequent capture. Record device identity,
firmware, interface/backend/OS, exact settings, panel actions, power cycles,
transport parameters, expected bytes, hashes, and observed outcomes in a manifest.
Never delete dumps for export preparation. Confirm the LCD preset before STORE.
A saved baseline is not guaranteed rollback. Factory reset (SETUP P9 code 230)
is destructive; do not include it as a routine precondition. Preserve evidence
before any separately planned reset comparison.

## Evidence recovery and protocol work

| Priority | Evidence / experiment | What it can establish |
|---|---|---|
| 1 | Recover T7 1a–1c, 2a–2c, 3, 4a–4c, 5a–5c, 6 and source photo; identify baseline and hashes | Reproduce reported anchors; filenames alone are insufficient |
| 1 | Record actual MIDI output using appropriate capture equipment and compare input buffer, wire stream, and device readback | Transport delivery and acceptance, separately from attempted counts |
| 1 | Capture post-normal-restore readback and, only if separately undertaken, post-reset-restore readback with identical source | Exact differences; does not alone reveal an internal firmware mechanism |
| 1 | PC isolation CH1/2/15/16 versus PED1 and per-channel Starting Preset 0/1, with output capture | Extend T1 `0x20..0x3F` mapping and determine numbering semantics |
| 2 | Operating Mode BANK/SONG/REMOTE isolated captures, restoring baseline between comparisons | Frame-223 `0x08` vs tail `0x0A` candidates; no writes until resolved |
| 2 | Bank Style FIRST/CURNT/OFF isolated captures | Establish real byte location; former SW1 attribution is retracted |
| 2 | All 18 MIDI message-type filter cells individually | Complete cell coverage, not extrapolation from three spot-checks |
| 2 | Channel 1–16 filter and Starting Preset edits separately | Identify two raw 16-cell regions; currently unknown, not a confirmed grid/list map |
| 2 | PER-PR `+0` channel across switches and PED1/PED2, global/PER-PR comparison | Strengthen role interpretation and independently isolate PED2 |
| 2 | Remaining Custom MIDI type codes and EOX numeric/null boundary edits | Verify enum assumptions and device interpretation of companion flags |
| 2 | Repeated BANK/REMOTE CUSTOM comparisons with identical edit sequence | Investigate reported correlation; one stable BANK run does not prove causality |
| 3 | Controlled trailer analysis across changes and devices | Test possible scope/semantics of observed constant `66 01`; constancy alone excludes nothing |

The former T8 list is an outline of proposed edits, not an executed capture set.
Do not assume `5a.syx` can be reused until its source is recovered and identified.

## Acceptance after transport is established

For each case preserve input, output recording, immediate and post-boot readback,
and front-panel results. Separate file correctness from device behavior.

- Names and PC slots: CH1/CH16; PED1 unchanged; numbering-base behavior.
- Custom MIDI CMD1–CMD5 including CMD4/5 and SysEx-toggle isolation.
- SysEx EOX companion flags and preservation of untouched payload bytes.
- Switch types including unknown-value preservation, Bank Size, PC-map OFF.
- Song slots 11–15 and final song; all set banks and unaffected frame boundaries.
- PR120 and every global after restore; compare the entire supported dump.
- Standalone preset send only after receive/addressing semantics are understood.
- Browser port hot-plug, monitor, file load/save, partial status, defaults and write gate.

Do not mark write-path validation passed from elapsed time, host frame counts,
a few spot-checks, or a single reset recovery. Record failures and uncertainty
in the [byte map](REVERSE_ENGINEERING.md) and [audit](AUDIT_REPORT.md).

## Capture worksheet for the next bench session

This is a proposed procedure, not an instruction to reset or restore a device.
Use a test unit and preserve its starting state. Parameter page edits and an IA
on/off-pattern STORE are different operations: do not assume every parameter
requires CTR STORE. Never press STORE on SETUP P8/P9 during ordinary captures.

For every named capture: arm **Read All**, press `2ND` then `SYSX`, select
`DUMP` on page 1, and press `CTR STORE`. Save the result under the name shown.
Record the actual LCD fields before leaving the edited page. Use source-device
and firmware notes plus SHA-256 hashes to distinguish files with similar names.
Check the expected isolated changes immediately. Do not advance after an
unexpected diff; keep it as evidence. Exit secondary mode deliberately and verify
mode/preset on the LCD rather than relying on a fixed number of button presses.

### A. Baseline and mode/style candidates

1. Capture `00-start.syx` without editing. Capture `01-repeat.syx` with no
   intervening edits. Differences reveal background/current-state drift and do
   not automatically indicate a checksum or corruption.
2. At `2ND → SETUP` P1, record current mode. If already BANK, use the first
   baseline as the BANK point. Set SONG using the field under that value;
   capture `02-mode-song.syx`. Return to BANK and capture
   `03-mode-bank-return.syx`. Compare both directions, especially state-frame
   `0x08` and tail `0x0A`; treat other changes as evidence, not noise to discard.
3. With BANK confirmed, `2ND → SETUP → → →` reaches P3 (two page advances).
   Record Bank Style. Capture one changed value at a time for FIRST, CURNT,
   and OFF, skipping any redundant first capture if the baseline already has
   that value. Name files `04-style-first.syx`, `05-style-curnt.syx`,
   `06-style-off.syx`. Return to the original value and capture the reversal.
4. Reuse the reported T7 REMOTE capture only after recovering its bytes and
   identifying the corresponding baseline. If unavailable and a new REMOTE
   point is needed, do it at the end of the session: SETUP P1 → REMOTE,
   capture, immediately return to BANK and capture again. Do not combine this
   with CUSTOM editing. A paired crash investigation is optional and separate;
   do not intentionally reproduce the lockup just to complete the byte map.

### B. PER-PR and controller mapping

Use a bank size that exposes the chosen IA switch: at Bank Size 5, use SW11
or SW15; Bank Size 15 exposes no IA switches. If changing Bank Size, capture
that preparation separately. Select the scratch preset through normal live
navigation and confirm its LCD number; choosing a preset in one edit page has
not been proven to set the context of another page.

1. `2ND → SETUP`, three advances to P4. Left field selects SW11; right field
   chooses PER PR. Capture `10-sw11-per-pr.syx` before changing routing.
2. Enter `MIDI`, one advance to P2. Left selects SW11; center selects a known
   channel (shown by its name); right selects CC42. Capture
   `11-sw11-routing.syx`. The candidate slot is `0x70`, with channel at `+0`
   (zero-based) and CC at `+2`. Treat this routing pair as a documented combined
   edit, not proof that each byte was independently isolated.
3. On MIDI P3 (two advances from P1), select SW11; set ON100/OFF20.
   Capture `12-sw11-values.syx`. Compare only this operation with the preceding
   capture. Restore the original routing/values afterward and record reversal.
4. Repeat with PED2, selected by name in the left field, using a different
   channel/CC pair and values. Keep GLOBAL→PER PR, routing, values and OFF
   sentinel experiments in separate files. Candidate PED2 base `0x48` needs
   independent evidence. Do not normalize a displayed OFF into numeric zero.
5. Capture the same configuration on a second verified preset to establish
   per-preset scope. Return status to GLOBAL and capture separately to distinguish
   the selection table from the actual global controller data.

### C. Filtering and Starting Preset

SETUP P7 is reached by six advances from SETUP P1. The left field cycles
message types and then channels; the right field selects BLOC/MERG. There is
no independent center channel field on this page.

- For each unanchored message type, record its current value, change only that
  entry, and save `20-filter-TYPE.syx`. Restore it before moving to the next
  entry so comparisons have an identified parent. Use the 18-entry order in the
  byte map, not the different CUSTOM command list. Existing tracked T3 anchors
  can be reused; reported T7 anchors require their raw files for replay.
- Select Channel 1, then separately Channel 2 and Channel 16. Capture each
  BLOC/MERG change and its reversal. These distinguish the two unidentified
  16-cell regions; do not assume a region just from matching the count 16.
  If claiming all channel cells individually verified, extend to Channels 3–15.
- SETUP P6 (five advances from P1) is a separate page. Left selects the MIDI
  channel name; the value field sets its Starting Preset to 0 or 1. Capture
  Channel 2 and Channel 16 changes separately. For display-base semantics,
  also record MIDI P1's displayed PC and actual outgoing PC bytes before/after.

### D. Close remaining range and acceptance gaps

Remote Title is SETUP P10 (nine advances from P1). Pass P8/P9 without pressing
STORE. If testing the upper range, capture values 127, 128, and 255 separately
and return to the original value. This tests companion-byte handling, not just
the low byte. No upper-range writes from the editor are currently supported.

Song/set OFF needs no further capture: the manual's ranges exclude it.
The tracked T1 capture already resolves PC-slot base `0x20`; further PC tests
should target displayed-number semantics and physical acceptance rather than
repeat an already decisive offset experiment. `66 01` has observed constancy;
repeating a dump alone cannot establish its meaning.

Stop after evidence captures if transport is still unresolved. Device write
acceptance belongs to the separate controlled tests above; it must include full
post-write readback and PR120/globals comparison.
