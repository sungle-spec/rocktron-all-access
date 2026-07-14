# Rocktron All Access - MIDI Implementation Chart (verbatim from manual page 73)

**Model:** All Access
**Date:** December 2, 1994
**Version:** 1.00

| FUNCTION | TRANSMITTED | RECOGNIZED | REMARKS |
|---|---|---|---|
| **BASIC CHANNEL** - Default | 1 | NONE | MAY BE SAVED IN NONVOLATILE RAM |
| **BASIC CHANNEL** - Changed | 1-16 | 1-16, OMNI | |
| **MODE** - Default | NONE | X | |
| **MODE** - Messages | X | X | |
| **MODE** - Altered | X | X | |
| **NOTE NUMBER** | O | X | |
| **NOTE NUMBER** - True Voice | O-127 | X | |
| **VELOCITY** - Note On | O | X | |
| **VELOCITY** - Note Off | O | X | |
| **AFTER TOUCH** - Key's | O | X | |
| **AFTER TOUCH** - Channel | O | X | |
| **PITCH BEND** | O | X | |
| **CONTROL CHANGE** * | O | O | * Each instant access switch may be assigned a control number that will be used for transmitting and receiving. Control numbers may be assigned globally for all presets, or differently for each preset. |
| **PROGRAM CHANGE** ** | O | O | ** A program change may be assigned to all 16 channels for each preset. Program change numbers may start at "0" or "1" for each channel. Actual program value sent is 0-127. |
| **PROGRAM CHANGE** - True Number | O | O | |
| **SYSTEM EXCLUSIVE** | O | O | |
| **SYSTEM COMMON** - Song Position | O | X | |
| **SYSTEM COMMON** - Song Select | O | X | |
| **SYSTEM COMMON** - Tune Request | O | X | |
| **SYSTEM REAL TIME** - Clocks | O | X | |
| **SYSTEM REAL TIME** - Commands | O | X | |
| **AUX MESSAGES** - Local On/Off | O | X | |
| **AUX MESSAGES** - All Notes Off | O | X | |
| **AUX MESSAGES** - Active Sensing | O | X | |
| **AUX MESSAGES** - System Reset | O | X | |

**Legend:** O = Yes / supported. X = No / not supported.

**Notes on chart footnotes (verbatim):**

> *Each instant access switch may be assigned a control number that will be used for transmitting and receiving. Control numbers may be assigned globally for all presets, or differently for each preset.*

> **A program change may be assigned to all 16 channels for each preset. Program change numbers may start at "0" or "1" for each channel. Actual program value sent is 0-127.*

---

## Reconstruction note

The original source document lays this out as a two-column chart ("TRANSMITTED" / "RECOGNIZED") with a single "REMARKS" column on the right; the source text extractor flattened it into a single column. The table above reassembles the pairings in the canonical order they appear in the manual. Every cell value (`O`, `X`, numeric ranges) is taken directly from the text; no values were inferred.
