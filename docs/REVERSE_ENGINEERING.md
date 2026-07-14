# Rocktron All Access — SysEx Reverse Engineering

Everything below was derived from a single bulk dump (`rocktron.syx`, 43 647 bytes, 145 frames) plus a 1 895-row MIDI monitor capture. The official 77-page user manual documents features but **never** publishes the sysex byte layout — every offset below came from inspecting the dump, not the manual.

## 1. Sysex framing

Every frame starts `F0 00 00 29 08` (F0 + Rocktron MMA ID + device ID `08`), then a command byte, then the payload, then `F7`.

```
F0 00 00 29 08 <CMD> <payload…> F7
```

| CMD  | Count in dump | Meaning (so far)              |
| ---- | ------------- | ----------------------------- |
| 0x2A | 1             | First frame of a bulk dump (preset 1) — "start of dump" |
| 0x2B | 144           | Every other frame in the dump |

`0x2B` is used for presets 2..120, every global block, and the tail. The `0x2A` vs `0x2B` distinction may actually be "first frame of bulk" vs "continuation" at the dump-envelope level, not a frame-type discriminator.

## 2. Frame size histogram

```
Size  Count  Inferred role
───   ───    ─────────────────────────────────────────
309   120    Preset data frames (one per PR1..PR120)
457    10    Songs / sets (10 blocks of 4-char slots)
263     1    Sequential-ID table (probably PC-map: 128 slots → 120 presets)
279     1    Name block: 16 channel + 2 pedal + 15 switch 4-char names
223     1    Bitmap (per-switch enabled / switch-type table?)
139     1    All 01/00 pairs — possibly per-channel filter mask
107    10    Song payloads (10 × 107 B — 1 per set?)
 23     1    Tail / end-of-dump + checksum-ish (`… 66 01 F7`)
```

## 3. 7-bit ASCII-pair encoding

Wherever the dump stores a string, it uses **two bytes per character**: low byte = ASCII (`0x20..0x7F`), high byte = `0x00`. This is standard 7-bit sysex packing — the MSB of each ASCII char becomes the next byte.

```python
def decode_ascii_pairs(buf, off, n_chars):
    out = bytearray()
    for k in range(n_chars):
        lo = buf[off + 2*k]
        if lo == 0:
            break                                   # null terminator
        # The All Access has no real space char in its A-Z 0-9 set,
        # so the firmware uses '.' (0x2E) as the inter-word separator
        # which the LCD then renders as space-like padding.
        if lo == 0x2E:
            out.append(0x20)
        elif 0x20 <= (lo & 0x7F) <= 0x7E:
            out.append(lo & 0x7F)
        else:
            break                                   # non-printable, stop
    return out.decode('ascii', errors='replace').rstrip()
```

**`0x2E` ↔ space.** Confirmed by the user against the on-device LCD: `MAN.OF    1` in the dump renders as `MAN OF 1` on the All Access display. Names that look like `BLISS.1`, `SDD.300`, etc. all read as plain spaces on hardware.

## 4. Preset frame (309 B)

Updated 2026-04-26 from `rev_T1_presets.syx`, `rev_T2_globals.syx`, `rev_T3_songs.syx` diff against `baseline-pre-RE.syx`. Offsets relative to the leading `F0`.

| Range           | Size | Field                                     | Status |
|-----------------|-----:|-------------------------------------------|--------|
| `0x00..0x04`    | 5    | Sysex header `F0 00 00 29 08`             | ✅ constant |
| `0x05`          | 1    | Command byte (`2A` first preset frame, `2B` rest) | ✅ |
| `0x06..0x1E`    | 26   | **Preset name** — 13 × (ASCII, 0x00) pairs starting at `0x06` | ✅ read/write — see below |
| `0x20`          | 1    | Title metadata / "title last byte marker" — flips when title is overwritten | ⚠️ resets to `0x00` after rename, not a visible char |
| `0x21..0x23`    | 3    | Preset header bytes — IA bitmap candidate | ⚠️ partial |
| `0x24..0x43`    | 32   | **16 × PC slots** — `(value, status)` pairs, CH1..CH16 | ✅ |
| `0x70..0x77`    | 8    | **Per-preset PER-PR override** for one IA slot — `(?, type, channel, ?, CC#, ?, ON, ?, OFF)` pattern | ✅ partial |
| `0xCA..0xD5`    | 12   | **Custom MIDI CMD1** — type byte (`0xCA`) overlaps IA bitmap low byte. **Write guard: read-only.** | ⚠️ collision |
| `0xD6..0xED`    | 24   | **Custom MIDI CMD2, CMD3** — clean 12-byte stride. **Write guard: writable.** | ✅ |
| `0xEE..0x121`   | 52   | **Custom MIDI CMD4, CMD5** — CMD4 `data2` lands at `0xF6` (= SysEx ON/OFF toggle); CMD5 runs into SysEx string at `0xF8`. **Write guard: read-only.** | ⚠️ collision |
| `0xF8..0x133`   | 60   | **SysEx string** — 30 × `(byte, 0x00)` pairs | ✅ |
| `0x134..0x136`  | 3    | Tail / unknown (final `F7` is at 0x137 = 309) | ❓ |

### Preset name (offsets 0x06..0x1E)

**Name is 13 characters**, NOT 12 as a previous draft said. Confirmed from `rev_T1_presets.syx` where setting PR1 to `AAAAAAAAAAAAA` (13 A's) flipped offsets `0x06, 0x08, 0x0A, 0x0C, 0x0E, 0x10, 0x12, 0x14, 0x16, 0x18, 0x1A, 0x1C, 0x1E` all to `0x41`. Encoding is the standard 7-bit ASCII pair (`<low>, 0x00`) — low byte at even offset, `0x00` at odd offset.

| Offset | Char# | Notes |
|--------|------:|-------|
| `0x06` | 1 | Was `0x20` (space) in PR1 baseline — the leading char |
| `0x08` | 2 | First "M" of `MAN OF 1` |
| `0x0A` | 3 | "A" |
| `0x0C` | 4 | "N" |
| `0x0E` | 5 | `0x2E` (`.`) = space character on this device |
| `0x10` | 6 | "O" |
| `0x12` | 7 | "F" |
| `0x14` | 8 | space |
| `0x16` | 9 | space |
| `0x18` | 10 | space |
| `0x1A` | 11 | space |
| `0x1C` | 12 | "1" |
| `0x1E` | 13 | space |

Byte `0x20` carries metadata that resets to `0x00` when the title is overwritten (was `0x63` ('c') in baseline, became `0x00` after PR1 rename). Not a visible char.

### Per-channel PC slots (offsets 0x24..0x43)

Each preset stores **16 program-change slots** at offsets `0x24..0x43`, one per MIDI channel CH1..CH16. Each slot is **2 bytes**: `(value, status)`.

```
0x24-0x25 = CH1   (LOOP)
0x26-0x27 = CH2   (HEAD)
0x28-0x29 = CH3   (DIEZ)
0x2A-0x2B = CH4   (WAM1)
…
0x42-0x43 = CH16
```

- **value byte** (even offset): the displayed PC number (0-127 if Starting Preset = 0; 1-128 if Starting Preset = 1).
- **status byte** (odd offset): `0x00` = active (PC sent on recall); `0x01` = inactive (`OFF` on the device).

The PC value is stored as the **displayed** value, not the wire value (so display "PC 1" is byte `0x01`, regardless of starting-preset offset — wire value = display - starting_preset).

Confirmed by:
- PR1 set to ascending CH1=PC1..CH16=PC16 → offsets 0x24, 0x26, … 0x42 contain values 0x02, 0x03, …  (off-by-one from user input — likely user typed PC2..PC17 or stored = display + 1 for one of the channel groups).
- PR2 set to all PC127 → 0x24=0x7F, status 0x25=0x00. PR2 inverse pattern matches.
- "PC slot leakage" in PR3..PR16 (user accidentally edited one PC slot per preset during T1.B navigation) confirms the 2-byte stride and slot layout independently.

### IA slot table (offsets 0x40..0xC7) — 17 slots × 8 bytes ✅

The region between the PC slots and the custom MIDI block is a **17-slot table** holding per-IA-slot config (channel + CC# + ON + OFF values) for each of the 15 IA switches + 2 pedals. Slots are stored in **REVERSED slot-index order** (slot for chan-id 17 first, descending to chan-id 1 at 0xC0) — same reversal pattern as the switch names in frame 123.

PR1 baseline default state shows the layout cleanly:

```
0x40: 00 00 11 00 7F 00 00 00   ← slot for chan-id 17 (PED2?)
0x48: 00 00 10 00 7F 00 00 00   ← slot for chan-id 16
0x50: 00 00 0F 00 7F 00 00 00   ← chan-id 15
0x58: 00 00 0E 00 7F 00 00 00   ← chan-id 14
…
0x70: 00 00 0B 00 7F 00 00 00   ← chan-id 11 (SW11)
…
0xC0: 00 00 01 00 7F 00 00 00   ← slot for chan-id 1 (SW1)
```

Each 8-byte slot when at default (`GLOBAL` mode):

| Offset within slot | Value | Meaning |
|---|---|---|
| +0 | 0x00 | flag byte (0x00 = default; non-zero when configured) |
| +1 | 0x00 | padding |
| +2 | chan-id (1-17) | slot identifier — points to which SW/PED this slot represents |
| +3 | 0x00 | padding |
| +4 | 0x7F | default ON value (127) |
| +5 | 0x00 | padding |
| +6 | 0x00 | default OFF value (0) |
| +7 | 0x00 | padding |

When a user configures the slot (via SETUP P4 = `PER PR` and MIDI P2/P3), the slot's bytes change to:

```
0x70: 02 00 2A 00 64 00 14 00   ← SW11 configured: CC#=42, ON=100, OFF=20
```

| Offset within slot | Value | Meaning |
|---|---|---|
| +0 | flag (0x02 = configured) | NOT chan-id — the slot's identity is implicit from its position in the table |
| +2 | CC# | (0x2A = 42) |
| +4 | ON value | (0x64 = 100) |
| +6 | OFF value | (0x14 = 20) |
| where is channel? | TBD | possibly in another byte we haven't isolated |

**Note on slot indexing:** the 17 slots map to 15 IA switches + 2 pedals, but the exact mapping order (which chan-id = SW1 vs PED1) needs one more focused dump to confirm. Likely chan-id 1 = SW1 and chan-id 16/17 = PED1/PED2, given the descending storage matches the SETUP P5 name-block reversal.

**Channel field:** the IA slot's "channel to send CC on" isn't yet pinned to a specific byte. Test T2 set SW11 to channel=CH3 but no byte in the slot showed the value `0x03`. Channel may be encoded elsewhere (per-preset table) or piggybacked into the flag byte. Needs follow-up.

### Custom MIDI block (offsets 0xCA..~0x121, ~12 B per CMD slot)

PR1 CMD1 set to `N ON> ch1 note 60 vel 100` flipped these bytes:

```
0xCA = type byte    (was 0x10, became 0x1F)   → 0x1F encodes "N ON>"
0xCC = type echo?   (was 0x00, became 0x1F)   → mirror of 0xCA
0xCE = channel      (was 0x11, became 0x01)   → 0x01 = CH1
0xD0 = ?            (no change)
0xD2 = data1        (was 0x00, became 0x3C = 60)   → note number
0xD4 = data2        (was 0x00, became 0x64 = 100)  → velocity
```

PR5 had CMD2 channel byte at `0xDE` flip from `0x11` to `0x01` (user's accidental edit during T1.C diagnostic), confirming **CMD slot stride = 16 bytes** (`0xDE - 0xCE = 0x10`):

```
CMD1: 0xCA..0xD5
CMD2: 0xDA..0xE5
CMD3: 0xEA..0xF5
CMD4: 0xFA..0x105   (overlaps with sysex region — needs confirmation)
CMD5: 0x10A..0x115
```

Note: with stride 16, CMD4 at `0xFA` would overlap with the sysex string starting at `0xF8`. Either CMD4/5 use a different region, or the CMD slots are shorter than 16 bytes. Need a follow-up dump to disambiguate.

### SysEx string (offsets 0xF8..0x133)

PR1 SYSX bytes set to BYTE1=0x01, BYTE2=0x02, BYTE3=0x03, BYTE4=0x04 produced:

```
0xF8 = 0x01 (BYTE1)   0xF9 = 0x00
0xFA = 0x02 (BYTE2)   0xFB = 0x00
0xFC = 0x03 (BYTE3)   0xFD = 0x00
0xFE = 0x04 (BYTE4)   0xFF = 0x00
```

30 bytes × 2-byte pair encoding = 60 bytes from `0xF8..0x133`. Default (unset) value = `0x01 0x00` per pair (visible as the stretch of `01 00 01 00 …` in baseline dumps).

SysEx ON/OFF toggle byte location: still TBD — probably one of the bytes near 0x21..0x23 in the preset header.

### IA on/off bitmap ✅ (fully decoded — 3 bytes for SW1-15)

Located at **`0xC8`, `0xCA`, `0xCC`** in each preset frame — 3 bytes, 5 bits per byte, with **REVERSED bit ordering** within each byte (same reversal pattern as the SETUP P5 name block).

```
0xC8 = SW1..SW5 bitmap     (bit 4 = SW1, bit 0 = SW5)
0xCA = SW6..SW10 bitmap    (bit 4 = SW6, bit 0 = SW10)
0xCC = SW11..SW15 bitmap   (bit 4 = SW11, bit 0 = SW15)
```

Confirmed by T5 — Bank Size = 1 makes all 15 SWs IA-eligible. Pattern `_ _ ON _ ON  _ _ ON ON ON  ON _ ON ON _` produced exactly the predicted bytes:

- `0xC8 = 0x05` (binary `00101` — SW3 + SW5)
- `0xCA = 0x07` (binary `00111` — SW8 + SW9 + SW10)
- `0xCC = 0x16` (binary `10110` — SW11 + SW13 + SW14)

The earlier ambiguity about whether `0xCA` was shared with CMD1's type byte is now resolved: T5 changed the IA pattern *without* touching CMD1 and `0xCA` flipped exactly as predicted from IA bits alone. CMD1 type byte must live elsewhere (location now confirmed via T4 to be `0xCA` overlap is coincidental — see Custom MIDI section).

### SysEx ON/OFF toggle ✅

Per-preset SysEx ON/OFF flag at **`0xF6`** in each preset frame. T5 set PR2 SysEx → ON and the only byte that changed in PR2's frame was `0xF6: 0x00 → 0x01`.

```
0xF6 = 0x00 — sysex string suppressed when preset recalls (SYSX P2 = OFF)
0xF6 = 0x01 — sysex string sent when preset recalls   (SYSX P2 = ON)
```

### Custom MIDI block — refined ✅

The CMD region starts immediately after the IA bitmap at `0xCA` and runs through `0xF7` (just before the SysEx region). **CMD slot stride = 12 bytes** (confirmed by CMD1 at `0xCA`, CMD2 type byte at `0xD6` — `0xD6 - 0xCA = 12`).

CMD layout per slot (12 bytes):

| Offset within slot | Field | Notes |
|---|---|---|
| +0 | Type byte | `0x00` = NONE, `0x03` = `C CH>`, `0x1F` = `N ON>` (more types TBD) |
| +1 | padding | always `0x00` |
| +2 | ?? | varies — possibly type-secondary data or shared with IA bitmap for CMD1 |
| +4 | Channel / data depending on type | for `N ON>`: `0x01` = CH1; for `C CH>` slot byte was `0x04` for set CH5 (encoding may differ per type) |
| +6 | data1 | for `N ON>` = note number; for `C CH>` = CC# |
| +8 | data2 | for `N ON>` = velocity; for `C CH>` = value |
| +10 | padding | always `0x00` |

5 CMD slots × 12 bytes = 60 bytes from `0xCA` to `0x105`. **However** SysEx starts at `0xF8`, which would overlap with CMD5. Current best guess: only 4 CMD slots fit cleanly (`0xCA..0xF5`), and CMD5 is stored elsewhere — OR the slot is 8 bytes not 12, and what looks like CMD2 fields at offsets `0xD6..0xDC` is actually one CMD slot's data spanning a smaller range. Needs one more focused dump to disambiguate (e.g., set CMD3 with distinct values and see where they land).

Confirmed encodings:
- `N ON>` → type byte `0x1F`
- `C CH>` → type byte `0x03`
- `NONE`  → type byte `0x00`

#### ⚠️ CMD layout — confirmed collisions and write guard

With the inferred 12-byte stride and `data2 = +8`:

| Slot | Type-byte offset | data2 offset | Collides with |
|------|------------------|--------------|---------------|
| CMD1 | `0xCA` | `0xD2` | **IA bitmap low byte at 0xCA** — confirmed by T5 |
| CMD2 | `0xD6` | `0xDE` | clean |
| CMD3 | `0xE2` | `0xEA` | clean |
| CMD4 | `0xEE` | **`0xF6`** | **SysEx ON/OFF toggle (0xF6)** — confirmed by `tools/uat_headless.py` regression |
| CMD5 | `0xFA` | `0x102` | runs through the SysEx string region (starts 0xF8) |

**Reproduced corruption:** set a preset's SysEx flag ON (`0xF6 = 0x01`), then save CMD4 with `data2 = 127`. `0xF6` silently became `0x7F` because the same byte was written by both the SysEx toggle and CMD4's data2. Every Custom MIDI save on any preset was flipping the SysEx-on-recall flag.

**Code-side guard (applied):** `rebuild_preset_frame` now only writes CMD slots `i ∈ {1, 2}` (CMD2 and CMD3). CMD1, CMD4, CMD5 are read-only until a focused hardware capture pins the true offsets. The headless UAT (`tools/uat_headless.py`) has a regression check ("CMD4 save preserves SysEx ON/OFF flag") to ensure this can't silently regress.

**To resolve:** on a scratch preset, set CMD2 to `C CH> ch5 cc#42 val100` and CMD3 to `N ON> ch7 note60 vel110`, Write All, Read All, diff against baseline (`tools/diff_dumps.py`). The offsets that change pin the true CMD stride and field positions — at which point the write guard in `rebuild_preset_frame` can be widened.

## 5. Global blocks

### Frame 123 (279 B) — Name block ✅

Layout (payload starts at offset 6 inside the frame, each 4-char slot = 8 bytes):

| Slot index | Content |
|-----------|---------|
| 0..15     | 16 × channel name (CH1..CH16, 4 chars each) |
| 16..17    | PED1, PED2 names |
| 18..32    | 15 × switch name — **stored REVERSED** (slot 18 = SW15, slot 32 = SW1) |

Confirmed against the user's MUSE live-rig dump:

- Channel names (slots 0..15): `LOOP HEAD DIEZ WAM1 TC22 SSUL REC  WAM2 KAOS VOCL SANS NORD ACCO LINE KAWA CH16`
- Pedal names (slots 16..17): `PED1 PED2`
- Switch names (slots 18..32 **reversed**, so 32→SW1, 18→SW15):
  ```
  SW1=SW1   SW2=SW2   SW3=SW3   SW4=SW4   SW5=SW5      ← preset switches, generic
  SW6=VOX1  SW7=DIEZ  SW8=MARS  SW9=FUN   SW10=SW10
  SW11=PHSD SW12=COMP SW13=FUZZ SW14=TOPB SW15=DLAY    ← user's IA assignments
  ```

The reversal was not obvious at first read; verified by matching slot 27 (`VOX1`) against the user's known SW6 = VOX assignment, then walking back.

### Frame 121 (263 B) — PC-map ✅

The MIDI P6 program-mapping table — incoming PC `1..128` → preset `1..120` (or `OFF`). Confirmed by `rev_T2_globals.syx`: setting PC1→PR50, PC2→PR60, PC3→PR70 produced byte changes:

```
0x06 = 0x31 (49 = PR50 - 1)
0x08 = 0x3B (59 = PR60 - 1)
0x0A = 0x45 (69 = PR70 - 1)
```

Each PC slot is **2 bytes** at offsets `0x06, 0x08, 0x0A, …` — value byte holds preset index `0..119` (preset number minus 1), high byte still TBD (may carry the `OFF` flag). 128 slots × 2 bytes + 6 header = ~262 bytes. ✓

### Frame 122 (223 B) — global config + state ✅ revised after T5

Confirmed byte locations from `rev_T5_final.syx` (the earlier T2 attributions for `0x30`/`0x32` were wrong — those were operational side-effects, not bank size/style):

```
0x08 = Operating Mode  (0x00 = BANK, 0x01 = SONG, 0x02 = REMOTE)
0x44 = Bank Size       (0x00 = 5, 0x02 = 1, 0x04 = 10?, 0x06 = 15? — 10/15 best guess)
0x46 = Bank Style      (0x00 = FIRST, 0x01 = CURNT, 0x02 = NONE)
0x2A = ? (PER-PR-mode-related flag, changed when SW11 toggled to PER PR)
```

Several other bytes (`0x76, 0x78, 0xB0, 0xB8, 0xC0, 0xC8, 0xCE, 0xD0`) change between dumps even with no user edits — operational state the device updates on every dump (last preset / last bank / dump counter). Ignored when modeling user state.

The 17-slot pattern at `0x4E..0xCF` (chan-id values 0x0B, 0x12, 0x0F, …) appears static across dumps and likely caches the most recent IA slot config — not a primary editable surface.

### Frame 123 (139 B) — MIDI Filter mask + Starting Preset ✅ partial

This is NOT all `01 00` pairs as a previous draft thought — it carries SETUP P6 (Starting Preset per channel) and SETUP P7 (MIDI Filter mask).

Confirmed bytes:

```
0x6A = Starting Preset for CH1  (0x01 default, 0x00 after toggle)
0x08, 0x0C = MIDI Filter bits  (0x01 default = MERG, 0x00 = BLOC)
```

T3 set CC on CH2 = BLOC and Note-On on CH5 = BLOC, and frame 123 had two new bit flips at `0x08` and `0x0C` (4-byte stride). The full filter map is presumably (16 channels + 1 globals row) × N message types arranged in this 139-byte block.

### Frames 124..133 (10 × 457 B) — SONGS ✅ (correcting earlier guess)

Earlier guess had the song/set assignment swapped. **The 457 B frames hold SONGS, the 107 B frames hold SETS** (confirmed by T4 — see frame 134..143 below).

Each 457 B frame holds **15 songs**, each song = **30 bytes** (= 10 preset slots × 2 bytes value + 10 bytes overhead). Confirmed from `rev_T3_songs.syx`:

Frame 125, SONG1 set to `[PR50, PR60, PR70, PR80, PR90]`:

```
0x06 = 0x31 (49 = PR50-1)   slot 1
0x08 = 0x3B (59 = PR60-1)   slot 2
0x0A = 0x45 (69 = PR70-1)   slot 3
0x0C = 0x4F (79 = PR80-1)   slot 4
0x0E = 0x59 (89 = PR90-1)   slot 5
```

SONG2 set to `[PR1, PR2, PR3, PR4, PR5]` starts at offset `0x24` (= `0x06 + 30`):

```
0x24 = 0x00 (PR1-1)   slot 1
0x26 = 0x01 (PR2-1)   slot 2
0x28 = 0x02 (PR3-1)   slot 3
0x2A = 0x03 (PR4-1)   slot 4
```

So SONG stride = 30 bytes; presets stored as `(preset_index, ?)` pairs at 2-byte stride. 15 songs × 30 bytes = 450 bytes data + 7 bytes header/padding = 457 ✓. 10 frames × 15 songs = **150 songs** total ✓ (matches manual).

### Frames 134..143 (10 × 107 B) — SETS ✅

**One frame per set, 10 sets total** (`SET1..SET10`). Each set assigns **50 banks → song IDs** at 2-byte stride starting at offset `0x06`.

T4 confirmed by setting SET1: `BK1=SG3, BK2=SG4, BK3=SG5` → frame 135 (= SET1, the 12th frame after the songs):

```
0x06 = 0x02 (= SG3 - 1)   bank 1
0x08 = 0x03 (= SG4 - 1)   bank 2
0x0A = 0x04 (= SG5 - 1)   bank 3
```

Stride per bank slot = 2 bytes. 50 banks × 2 = 100 bytes payload + 6-byte header + 1-byte tail = 107 ✓.

Frame 134 = SET1, Frame 135 = SET2 (so the user's "SET1 edit" actually landed in frame 135, suggesting frames are indexed `134 + (set_index - 0)` — TBD whether 0-indexed or 1-indexed; the frame-index numbering in the dump may not align cleanly).

Wait — frame indexing: if "frame 135" in the diff is the 135th frame counting from 1 (PR1 = frame 1), then frame 134 = first set, frame 135 = second set. T4 set SET1 — but the diff hit frame 135 not 134. Possible explanation: there's a leading "set table header" frame at 134, and SET1..SET10 start at 135. Or frame 124 isn't actually the first song frame. Needs one more cross-check, but the layout (2-byte bank slots starting at offset 0x06 in a 107 B frame) is solid.

### Frame 144 (23 B) — tail / global config block ✅ revised after T6

Originally suspected to be a checksum + dump-type. Actually carries **more globals**. All major fields now decoded:

```
0x06 = ? operational flag (flips between dumps without edits)
0x08 = Program Change Status  (0x00 = OFF, 0x01 = ON, 0x02 = MAP)        ✅
0x0A = ?  (paired with PC status — operational accumulator)
0x0C = ?  (paired with PC status — operational accumulator)
0x0E = ?  (paired with PC status — operational accumulator)
0x10 = Remote Title Number  (0..127)                                      ✅
0x12 = MIDI Receive Channel  (0..15 = ch 1-16, 0x10 (=16) = OMNI)         ✅
0x14 = 0x66                   ← constant across all 6 captured dumps
0x15 = 0x01                   ← constant
0x16 = F7
```

`66 01` before `F7` confirmed constant across all 6 dumps — not a checksum, just an end-of-dump marker.

### Switch Type table (per-IA-switch LATCH/MOM/HOLD) ⚠️ partial

Switch types are stored in **frame 122 starting around `0x40`**, in 2-byte slots. Encoding confirmed: **`0x00` = LATCH, `0x01` = HOLD, `0x02` = MOMENTARY**.

Observed cumulative changes:

| Test | User action | Resulting bytes |
|---|---|---|
| T5 | SW1 → MOM, SW2 → HOLD | `0x44 = 0x02, 0x46 = 0x01` |
| T6 | SW3 → MOM, SW4 → HOLD | `0x40 = 0x02, 0x42 = 0x01` (NEW) |

The mapping from SW number → byte offset isn't a simple linear table — T6's SW3/SW4 changes landed at *lower* offsets than T5's SW1/SW2. Likely a **stack-style structure** where new modifications prepend (most-recently-modified switch at `0x40`). Without the indexing scheme, the editor can't reliably write switch types yet — would need one more focused test setting types on switches *individually* and dumping after each to nail the order.

For now: the editor can READ all 4 type bytes but the SW-to-slot mapping is approximate.

## 6. Hardware quirks observed during testing

These came up while wiring the editor, useful to know when interpreting MIDI traffic:

- **Recall echo.** When the All Access receives a Program Change on its MIDI Receive Channel (`MIDI P7`, default ch1) and `Program Change Status` is `ON`, it recalls the matching preset and immediately broadcasts **the entire preset's PC + CC chain** on its MIDI Out. So sending `PC 0 ch1` to recall `MAN OF 1` causes the device to echo back ~10 events (PC on the device's other channels + CCs for every IA switch the preset toggles). The editor's IA LEDs auto-light from this echo, no need to read the dump's IA bitmap.
- **MR9 program byte on ch1.** PC traffic on ch1 is **not** a preset-recall signal — it's the device's outgoing program change for the MR9 loop controller (which happens to live on ch1 in the user's rig). For example, PR1 (`MAN OF 1`) sends PC 99 to the MR9. The editor explicitly does not interpret incoming ch1 PC as a preset selector; the foot-controller LCD is driven by virtual-switch clicks only.
- **No remote dump request.** The All Access has no documented MIDI message that asks it to send a bulk dump. The only way to capture a dump is to initiate it from the front panel (`SYSX → DUMP → CTR STORE`). The editor's "Read All" therefore arms a passive capture and waits.

## 7. Dumps still needed

The original list in this section bundled "one dump per field". That has been superseded by an orthogonal-bundling plan that gets the same coverage in **3 destructive dumps + 1 optional idempotency dump**.

Summary of the plan:

| Dump | File | What it pins down |
|------|------|-------------------|
| T0 (optional) | `rev_T0_idempotent.syx` | Whether tail byte `66` is a stable checksum or a timestamp |
| T1 | `rev_T1_presets.syx` | Title, all 16 PC slots, IA bitmap, custom MIDI block, sysex string + toggle (PR1 = max state, PR2 = inverse) |
| T2 | `rev_T2_globals.syx` | Operating mode, bank size/style, IA mode bitmap, starting preset, remote title, switch types, PC-map, MIDI receive ch + PR3 PER-PR override layout |
| T3 | `rev_T3_songs.syx` | Song frame layout (107 B), set frame layout (457 B), MIDI filter mask (139 B) |

Method for each: take a **Save .syx** baseline first, change only the target fields on the device, capture with **Read All → Save .syx**, then diff the two files with `tools/diff_dumps.py`. Keep the baseline as your restore point.

## 8. Timing for writing back

Manual: *"about 65 Hz (or about 1 byte every 15 milliseconds). Faster transfer will cause a "Buffer Overflow" error on All Access."*

`app.py` throttles between frames with `time.sleep(max(0.03, n * 0.015))` — at least 30 ms gap, longer for big frames. Conservative but reliable.

## 9. Save / Load round-trip

The editor's **Save .syx** button (`/api/dump/save`) streams the current `STATE['dump_bytes']` to the browser as a download. After a fresh **Load** with no edits, the saved file is byte-identical to the original (verified with `cmp`). After a preset rename, only the 24 name bytes inside the affected 309-byte frame change — every other byte (header, payload, tail) is preserved.

This is the safest possible writeback path: the editor never reconstructs a frame from decoded fields, only splices new bytes into the original raw frame at known offsets.
