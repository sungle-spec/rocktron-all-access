# Editor user manual

Start with [Getting Started](GETTING_STARTED.md). This editor supports local file
inspection and editing, live MIDI monitoring, and a virtual controller. Hardware
restore is experimental. See the [audit](AUDIT_REPORT.md) for evidence and limits.

## Load, capture, and save

**Load .syx** opens a file picker. The server receives uploaded bytes; it cannot
open an arbitrary path on your computer. Standard MIDI files are not supported.
**Save .syx** downloads the current stream, including saved edits. It never
replaces the original file automatically.

For **Read All**, select a MIDI input, click Read All, then on the All Access press
`2ND`, then `SYSX`. On page 1 choose `DUMP`, then `CTR STORE`. Capture completes
when the tail arrives; it can still be incomplete if earlier frames were lost.
Check completeness and save the untouched capture separately before editing.
There is no documented remote dump-request message.

Reloading or reconnecting restores the document currently held by the server.
All browser clients share that document; this is not a multi-user editor. A
new capture replaces current unsaved edits. Stopping the process loses unsaved
state. A saved dump is valuable evidence but does not guarantee recovery.

## Presets

Select a preset to edit:

- **Name:** up to 13 characters. The editor uppercases text and writes spaces as
  `0x2E`, matching an observed device convention. Existing bytes are preserved
  unless that field is saved.
- **PC slots:** 16 channels, stored values `0..127`, plus active/OFF status.
  The corrected slot base is `0x20`. Previous versions used `0x24`, skipped CH1/2,
  and could overwrite PED1 while editing CH15/16. The relationship to the device's
  per-channel displayed numbering base still needs controlled verification.
- **IA bitmap:** 15 stored switch states. Only the device's currently available
  IA switches act as IA switches in live operation.
- **Custom MIDI:** five command slots. Four codes have reported or tracked
  capture anchors; other type codes follow the manual's list order and remain
  inferred. Unknown codes are preserved. Some command types do not use all fields.
- **SysEx string:** up to 30 values with companion flags. An empty field represents
  EOX (`null` in the API); zero is a data value. Saving unchanged EOX fields
  preserves their original payload. The manual says values after EOX cannot be
  programmed; device behavior still needs boundary tests.

The PER-PR controller table is decoded for inspection but has no editing UI.
Its slot interpretation and GLOBAL/PER-PR interaction need further evidence.

## Globals

**Save to dump** edits only supported fields. Operating Mode and Bank Style
controls are disabled because their byte locations remain unresolved; the API
rejects attempts to write them. Raw Operating Mode candidates are not treated as
confirmed values. Bank Size uses the reported tail-frame mapping. Receive channel,
PC status, and remote title have candidate or capture-backed mappings described
in the [byte map](REVERSE_ENGINEERING.md).

The editor currently supports remote-title values only through 127; the manual
allows 255, and the upper range's encoding remains unverified. Starting Preset in
this panel is a UI preference, not a per-channel device edit.

**Save (UI only)** saves runtime UI preferences in the server. These are shared
and reset when the process stops; they are not written to the hardware dump.

## Channels, switches, songs, sets, and PC map

Channel/switch/pedal names have four characters. Save them separately from
switch types. Switch-type edits have their own save button; unknown values are
shown as unknown and preserved until explicitly replaced.

Songs contain 15 preset slots (SW1–SW15). The active count depends on Bank Size;
all 15 are stored, and inactive rows are dimmed. Sets contain 50 bank-to-song
assignments. Neither song slots nor set banks have an on-device OFF option:
SONG/SET P2 offers PR1–120; P3 offers SONG1–150 (manual pp.56–58).
Unknown assignments have a disabled placeholder; leaving one unchanged preserves
its bytes.

PC map has 128 incoming-program assignments. It supports OFF, unlike songs/sets.
Saving a partial list via the API preserves omitted entries.

## Virtual controller and MIDI monitor

Select an output before sending live PC, CC, or note messages. Clicking the virtual
controller, moving expression sliders, or using Quick Send can affect connected
gear immediately. The virtual controller is an approximation, not a verified
emulation of every operating mode, switch type, or Custom MIDI chain. IA routing
settings in this view are browser preferences and do not configure hardware.

The MIDI monitor records host events and received bytes. A logged transmit means
a host API call returned; it is not device acknowledgement. An unavailable output
or a failed send produces an error. Port changes and live output are refused while
an experimental dump worker owns the output.

## Experimental writes and restore symptoms

Bulk and standalone-preset sends are disabled by default. They require the
explicit server setting `AA_ENABLE_EXPERIMENTAL_WRITE=1`; opt-in does not establish
physical pacing or device acceptance. Standalone-preset addressing is unresolved.
Write progress describes host output only. Completion requires device readback
and byte comparison, not a check mark in the browser.

Blank PR120 and altered globals were reported after bulk restore. Similar symptoms
were reported during device-to-device restore. This does not establish a firmware
cause or exclude editor/transport issues. One factory-reset-then-restore success
was reported; factory reset is destructive and is not a guaranteed recovery step.
Follow the [hardware evidence plan](HARDWARE_TEST_PLAN.md) when investigating.
Do not repeat failed transfers or change pacing based on an unverified diagnosis.

CUSTOM editing became unresponsive during a reported REMOTE-mode session. Whether
REMOTE caused it remains unproven; the manual's intended mode behavior does not
prove a crash mechanism. Preserve the failing edit sequence before controlled tests.

## Troubleshooting

- **No ports:** work with files offline, or inspect server errors and reconnect
  the interface. The server can run with `AA_DISABLE_MIDI=1`.
- **Load rejected:** use raw All Access `.syx` data, not a Standard MIDI File;
  malformed streams and unrelated messages are rejected.
- **Partial capture:** save it as evidence, then inspect the source/connection.
  Editing does not promote it into a complete dump.
- **Hardware write unavailable:** writes default disabled, incomplete streams are
  refused, and busy/unavailable outputs cannot send.
- **A field reverted:** field saves update the in-memory stream; Save .syx downloads
  it. Read All replaces it with a new device capture.
- **Unexpected display values:** do not normalize unknown bytes or infer protocol
  values from a plausible UI display. Save the original and use the diff tools.

## Documentation

- [Architecture](ARCHITECTURE.md): responsibilities, state, validation, and tests.
- [Audit report](AUDIT_REPORT.md): findings, fixes, verification, and open risks.
- [Protocol evidence map](REVERSE_ENGINEERING.md): field layouts and provenance.
- [Hardware plan](HARDWARE_TEST_PLAN.md): remaining captures and acceptance criteria.
- [Manual summary](MANUAL_SUMMARY.md), [MIDI chart](MIDI_SPEC.md): reference material.
- Private `docs/archive/`: historical notes, superseded rather than current procedures.
