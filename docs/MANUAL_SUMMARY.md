# Rocktron All Access - Editor-Relevant Findings

Source: 77-page User's Manual, Model "All Access", Date December 2, 1994, Version 1.00, Rocktron Corporation.

> IMPORTANT: The manual is an end-user operations guide. It does **not** publish a sysex protocol specification (no command byte table, no checksum algorithm, no identity reply, no per-message byte layout). Everything below is either explicitly documented in the manual or marked "not documented".

---

## 1. Sysex Protocol

| Item | Value |
|---|---|
| Manufacturer ID | **Not documented** in manual. (Rocktron's assigned MMA ID is `0x00 0x00 0x29` but the manual does not state this.) |
| Device ID | Not documented |
| Command bytes (dump/load/request) | Not documented - manual only describes front-panel "SYSX -> Bulk Dump/Load" page with a DUMP/LOAD selector |
| Preset-request / global-request commands | Not documented - no byte-level message format given |
| Identity / handshake | Not documented |
| Checksum | Not documented (no mention of a checksum byte or algorithm) |
| Exact byte sequences quoted | **None.** The manual contains zero hex byte dumps. The string `F0 00 00 29 08 2A ...` does not appear anywhere in the manual. |

### What the manual DOES say about bulk transfer (page 66):

- SYSX -> Page 1 of 3: "Bulk Dump/Load" with `DUMP` or `LOAD` selection. Initiating requires front-panel `CTR STORE` press.
- To receive a bulk load, the unit must be on the Bulk Dump/Load page with `LOAD` selected.
- Timing constraint (verbatim): *"The All Access can receive a data dump at about 65Hz (or about 1 byte every 15 milliseconds)."* Faster transfer produces a "Buffer Overflow" error.
- When using an Alesis Data Disk, dump in "sequence mode" (not "sysx mode") so playback preserves original speed.
- No block size, no preset boundary markers, no address fields disclosed.

### Per-preset sysex (distinct from bulk dump)

- SYSX -> Page 2 of 3: each preset has a system-exclusive string which can be toggled `ON` / `OFF` (sent / not sent on recall).
- SYSX -> Page 3 of 3: each preset's sysex string is up to **30 bytes**, `BYTE1..BYTE30`, each byte 0-127 or `EOX` (End-of-Exclusive terminator). An EOX byte ends the string; later bytes cannot be programmed.
- Manual does NOT clarify whether user must supply the leading `F0` or whether unit adds it - it says "byte value 0-127, EOX".

---

## 2. Preset Structure

Per the manual, the All Access stores **120 presets** (`PR1-120`). Each preset contains:

| Field | Range / Values | Notes |
|---|---|---|
| Preset title | up to **13 characters**, `A-Z`, `0-9` | TITLES program |
| Programmable patch (PC) changes | up to **16** - one per MIDI channel (CH1-CH16) | MIDI P1; value `1-128` or `0-127` (depends on Starting Preset Number per channel, see Sec 3), or `OFF` |
| Instant-access switch on/off status | 1 bit per instant-access switch (SW1-SW15 that aren't preset switches) | Stored via CTR STORE per preset |
| Per-preset control info | If SETUP P4 = `PER PR`: each instant-access switch and each pedal stores MIDI channel, control number, ON value, OFF value | When `GLOBAL`, these come from the global table instead |
| Custom MIDI string | up to **5 MIDI commands** (CMD1-CMD5) sent when preset is recalled | any MIDI message type (see list below) |
| System Exclusive string | up to **30 bytes** (`BYTE1-30`, each 0-127 or EOX) plus ON/OFF toggle | See Sec 1 above |

Ranges for instant-access fields (per preset when PER PR, else global):
- MIDI channel: `CH1-16` (or assigned name)
- Control number: `0-120` or `OFF`
- ON value: `0-127` or `OFF`
- OFF value: `0-127` or `OFF`
- Switch type: `LATCHING`, `MOMENTARY`, `HOLD` - **global only (not per preset)**, applies to instant-access switches

Delay / tempo / relay fields per preset: **Not documented** - manual makes no mention of delay or tempo storage, and the device has no relay outputs.

### Custom MIDI command types (CUSTOM P1 list, from manual)

`NOFF>` (Note Off), `N ON>` (Note On), `KPRS>` (Key Pressure), `C CH>` (Control Change), `P CH>` (Program Change), `CPRS>` (Channel Pressure), `PBEN>` (Pitch Bend), `T CLK` (Timing Clock), `START`, `CONTU` (Continue), `STOP`, `ACTSN` (Active Sensing), `SYSRS` (System Reset), `M T C>` (MIDI Time Code), `SGPP>` (Song Position Pointer), `SGSL>` (Song Select), `T REQ` (Tune Request), `NONE`. Commands ending in `>` require additional data (CUSTOM P2) - typically channel, data1, data2 fields.

---

## 3. Global / System Config (SETUP program, 10 pages)

| Page | Setting | Values | Notes |
|---|---|---|---|
| P1 | Operating Mode | `BANK`, `SONG`, `REMOTE` | See Sec 8 |
| P2 | Bank Size (Bank By) | `1`, `5`, `10`, `15` | Determines # preset switches vs instant-access switches |
| P3 | Bank Style | `FIRST`, `CURNT`, `NONE` | Recall behavior on bank up/down |
| P4 | Instant-access operating status | per switch/pedal: `GLOBAL` or `PER PR` | Applies to SW1-SW15 and PED1-PED2 individually |
| P5 | Naming MIDI channels & switches | 4 chars each, `A-Z 1-9`, for `CH1-16`, `SW1-15`, `PED1`, `PED2` | Custom abbreviations |
| P6 | Starting Preset Number | `0` or `1` | Per-channel (CH1-CH16) |
| P7 | MIDI Filtering | per message-type and per channel: `BLOC` or `MERG` | Applies to all MIDI commands and CH1-16 |
| P8 | Preset Reinitialization | Selected preset `PR1-120` | Front-panel only action |
| P9 | Memory Reinitialization | Code `0-255`. `230` = full factory reset; `231` = reset only controller info for instant-access switches/pedals | |
| P10 | Remote Title Number | `0-255` | For REMOTE mode linking to other Rocktron racks |

Other global-ish settings (from MIDI program):

| MIDI Page | Setting | Values |
|---|---|---|
| P4 | Switch Type (per instant-access switch, global) | `LATCHING`, `MOMENTARY`, `HOLD` |
| P5 | Program Change Status (receive behavior) | `OFF`, `ON`, `MAP` |
| P6 | Program Mapping | `PC1-128` -> `PR1-120` or `OFF` |
| P7 | MIDI Receive Channel | `1-16` or `OMNI` |

Display brightness, pedal calibration, auto-load, foot-switch-to-MIDI auto-assignment beyond above: **Not documented**.

---

## 4. Foot-Switch Layout

- **18 switches total** (introduction page).
- **15 numbered switches (SW1-SW15)** arranged as 3 rows of 5, usable as preset switches and/or instant-access switches depending on Bank Size.
- **Bank UP** + **Bank DOWN** switches (2) - always dedicated to bank navigation, select banks 0-12.
- **2ND** switch - toggles secondary functions across all other switches (edit-mode access).
- Role mapping driven by Bank Size (SETUP P2):
  - `1` -> 0 preset switches, 15 instant-access
  - `5` -> SW1-SW5 preset, SW6-SW15 instant-access
  - `10` -> SW1-SW10 preset, SW11-SW15 instant-access
  - `15` -> all preset, 0 instant-access
- Instant-access switches individually configurable (per MIDI pages 2/3/4): channel, CC number, ON value, OFF value, switch type.

---

## 5. Pedal Inputs

- **2 expression pedal ports** ("PEDAL 1", "PEDAL 2"), 1/4" mono jacks on rear panel.
- Each pedal (PED1, PED2) is configurable like an instant-access slot:
  - GLOBAL or PER PR (SETUP P4)
  - MIDI channel: `CH1-16`
  - Control number: `0-120` or `OFF`
  - ON value (toe / max): `0-127` or `OFF`
  - OFF value (heel / min): `0-127` or `OFF` - can be inverted by swapping ON/OFF
- Continuous CC output. Live pedal movement shows transmitted value on display while the Control Value page is open.

---

## 6. MIDI Channels

- **16 independent channels** (`CH1-16`). The unit can transmit a program change on each of the 16 channels for a given preset (so one preset can trigger up to 16 devices).
- Each channel can receive a custom 4-character name (SETUP P5, `A-Z`, `1-9`).
- Each channel can have its own "Starting Preset Number" offset (`0` or `1`) (SETUP P6).
- MIDI Receive channel (for incoming PC to the All Access): 1-16 or OMNI (MIDI P7).
- Transmit: 1 (basic), 1-16 per assignment. Receive: 1-16 / OMNI.

Control numbers per MIDI channel / switch assignment are free-form; manual example assigns the same CC# to multiple channels to control multiple devices simultaneously.

---

## 7. Relay Outputs

**None.** The All Access has no relay or switch outputs. Rear-panel I/O is: MIDI In (7-pin DIN, phantom), MIDI Out (7-pin DIN, phantom), 2x Pedal (1/4" TRS-style), 2.5mm DC power. No control-voltage or relay jacks are documented.

---

## 8. Bank Modes / Bank Styles

**Operating Modes** (SETUP P1):
| Mode | Meaning |
|---|---|
| `BANK` | Bank-and-preset navigation; UP/DOWN select bank (first two digits, 0-12), preset switches pick within bank. Responds to incoming PC. |
| `SONG` | Preset switches mapped per-song. Songs arranged into Sets. |
| `REMOTE` | Unit acts as remote for compatible Rocktron rack; titles/LEDs mirror rack state; PC not sent from preset switches (rack drives state). |

**Bank Styles** (SETUP P3):
| Style | Meaning |
|---|---|
| `FIRST` | Bank UP/DOWN recalls first preset of new bank |
| `CURNT` | Recalls same position (last preset switch pressed) in new bank |
| `NONE` | Does not recall a preset until a preset switch is pressed |

**Bank Sizes** (SETUP P2): `1`, `5`, `10`, `15` presets per bank.

- SONG structure: 1 Song = 1 bank worth of presets (size depends on Bank Size). Total **150 songs** (`SONG1-150`).
- SET structure: 1 Set = 50 banks/songs. Total **10 sets** (`SET1-10`). Total 50 banks referenced as `BNK1-50`.

---

## 9. Preset Program-Change Authoring

- Each preset has **16 PC slots**, one per MIDI channel CH1-CH16.
- Editing: MIDI P1 - pick preset (PR1-120) -> pick channel (CH1-16 or name) -> pick program (1-128 or 0-127 or OFF).
- Per-channel numbering base (0 or 1) set in SETUP P6. Transmitted raw value is always 0-127 (from MIDI Implementation footnote).
- CC authoring is NOT part of the preset's PC block - it comes from the instant-access switch/pedal state via CTR STORE (saves current ON/OFF pattern of instant-access switches for the preset). When the preset is recalled, each instant-access switch's stored ON/OFF value is transmitted on its assigned channel+CC.
- Custom MIDI string (CUSTOM, 5 commands/preset) adds any additional MIDI messages sent on recall.
- Sysex string (SYSX, 30 bytes/preset) optionally sent on recall (per-preset on/off toggle).
- Delay / tempo: not authored per preset in the manual.

---

## 10. Sysex Dump Format Hints

Documented in the manual:
- Bulk dump transmits the "entire All Access programmable memory" as a single stream.
- Max receive rate ~65 Hz (~15 ms/byte).
- Must be received with unit on SYSX Page 1 set to LOAD.
- No block structure, no preset-boundary markers, no address fields, no checksum, no manufacturer ID disclosed.

**Not in manual but commonly referenced:**
- The prefix `F0 00 00 29 08 2A ...` is not in this document. `00 00 29` is Rocktron's MMA manufacturer ID; the `08 2A` pair and anything following would be model/command-specific bytes whose meaning must be reverse-engineered from actual device dumps - the manual provides no key.
- Recommend capturing a full bulk dump from a live unit and reverse-engineering: 120 presets * (title 13B + 16 PC slots + instant-access state + custom 5-cmd block + 30B sysex + flags) plus global config. Expected rough preset size: ~120-200 bytes/preset -> ~15-25 KB total dump. Manual does not confirm.

---

## Companion TSV Evidence (user's own data, from sibling files)

User-supplied sheets (`sheet_*.tsv` in same folder) give a concrete real-world example config but are NOT from the manual:
- `sheet_GLOBAL_CONFIG.tsv`: Bank Mode 5, Bank Style 1 (=FIRST?), Preset Start 1, MIDI Receive Ch 1.
- `sheet_CHANNELS.tsv`: 16 channel names assigned to various rack gear (LOOP, HEAD, DIEZ, WAM1, TC22, etc.).
- `sheet_CONTROL_NUMBERS.tsv`: per-switch assignments (switch# -> channel-name -> CC#).
- `sheet_PRESETS.tsv`: 120 presets with names and per-pedal notes.
- `sheet_MIDI_MONITOR.tsv`: captured on-the-wire MIDI showing PC and CC output per preset recall - useful for reverse-engineering the sysex block.

These confirm the manual's field model (16 channels, 15 switches, 2 pedals, 120 presets, PC per channel, CC per switch) but provide no protocol bytes.
