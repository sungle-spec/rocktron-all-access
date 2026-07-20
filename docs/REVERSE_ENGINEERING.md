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
| `0x40..0xC7`    | 136  | **IA slot table (PER-PR overrides)** — 17 slots × 8 bytes; first slot doubles as channel + switch-id record (see below) | ✅ partial |
| `0xC8..0xCD`    | 6    | **IA on/off bitmap** — 3 × (bitmap, 0x00) pairs, SW1-15 | ✅ |
| `0xCE..0xF5`    | 40   | **Custom MIDI CMD1-5** — 5 slots × 8 bytes, `[type, chan, data1, data2]` pairs. No collisions; all 5 writable. | ✅ |
| `0xF6`          | 1    | **SysEx ON/OFF toggle** | ✅ |
| `0xF8..0x133`   | 60   | **SysEx string** — 30 × `(byte, 0x00)` pairs | ✅ |
| `0x134..0x136`  | 3    | Tail / unknown (final `F7` is at 0x137 = 309) | ❓ |

The only bytes in the whole 309-byte frame still unexplained are `0x20..0x23`
(title metadata) and the 3-byte tail — confirmed by an exhaustive census of
all 120 baseline presets (`tools/analyze_captures.py`, Hunt 6).

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

**Channel field — located (offline re-analysis, 2026-07-19).** The full-frame
diff of T2 (SW11 configured to CH3 / CC42 / ON 100 / OFF 20) shows exactly
two extra bytes changing alongside the `0x70` slot:

```
0x40: 0x00 → 0x03   ← the channel (CH3)
0x42: 0x11 → 0x0B   ← the switch id (11 = SW11)
```

i.e. the **first table entry (base `0x40`) doubles as a channel + switch-id
record**. What's not yet disambiguated with a single observation: whether
`0x40/0x42` is a per-slot header rewritten per configured switch, or a
"most-recently-configured" record with per-slot channels elsewhere. One T7
capture (configure PER-PR on *two* switches with different channels, dump,
diff) settles it. Note T2 also nominally configured PED1 (ch4/cc11/127/0)
but no PED1 slot bytes changed — that edit likely never persisted
(CTR STORE not pressed), same failure mode as T3's lost SET1 edit.

### Custom MIDI block ✅ (offsets 0xCE..0xF5 — 5 slots × 8 bytes)

**Resolved offline** (2026-07-19) by re-mining the existing captures with
`tools/analyze_captures.py`. The block is **five 8-byte slots** based at
`0xCE` — `0xCE, 0xD6, 0xDE, 0xE6, 0xEE` — each laid out as four
`(value, 0x00)` pairs:

| Offset within slot | Field | Notes |
|---|---|---|
| +0 | **Type byte** | index into the manual's CUSTOM P1 list (see enum below); unset slot = `NONE` = `0x11` |
| +2 | **Channel** | **0-based** (`0x00` = CH1 … `0x0F` = CH16) |
| +4 | **data1** | note# / CC# / program# depending on type |
| +6 | **data2** | velocity / CC value (types without data leave it 0) |

**Type enum = the manual's CUSTOM P1 list order:**

```
0x00 NOFF>   0x01 N ON>   0x02 KPRS>   0x03 C CH>   0x04 P CH>   0x05 CPRS>
0x06 PBEN>   0x07 T CLK   0x08 START   0x09 CONTU   0x0A STOP    0x0B ACTSN
0x0C SYSRS   0x0D M T C>  0x0E SGPP>   0x0F SGSL>   0x10 T REQ   0x11 NONE
```

`N ON>`=0x01, `C CH>`=0x03 and `NONE`=0x11 are hardware-observed; the other
15 codes are inferred from the list order (three anchors land exactly on
their list positions, including NONE at 17 — the `0x11` "default marker"
seen at every unset slot).

**Evidence (all three lines of it agree):**

1. **T1 write capture** — CMD1 programmed to `N ON> ch1 note60 vel100`:
   `0xCE: 0x11→0x01` (type N ON>), `0xD2→0x3C` (60), `0xD4→0x64` (100).
   The channel byte `0xD0` stayed `0x00` = CH1 0-based — already at target.
2. **T4 write capture** — CMD2 programmed to `C CH> ch5 cc20 val30`:
   `0xD6: 0x11→0x03` (C CH>), `0xD8→0x04` (CH5 0-based), `0xDA→0x14` (20),
   `0xDC→0x1E` (30). A perfect 4-field hit on the slot-2 base `0xD6` = `0xCE+8`.
3. **Corpus** — all 120 baseline presets decode **600/600 slots** cleanly
   under this layout (type ≤ 17, channel ≤ 15, data ≤ 127, high bytes 0x00),
   yielding 32 musically-plausible real commands (e.g. `C CH> ch4 cc11` with
   values 0/127 — expression pedal heel/toe; `C CH> ch9 cc94`; PR106 carries
   a command in **slot 5 at `0xEE`**, proving CMD5 exists in-frame).

**Consequences:** the region ends at `0xF5` — it does **not** touch the IA
bitmap (`0xC8/0xCA/0xCC`), the SysEx toggle (`0xF6`) or the SysEx string
(`0xF8+`). All five CMD slots are safely writable; the old CMD2/CMD3-only
write guard is removed. The headless UAT asserts a CMD5 write leaves `0xF6`
untouched.

### SysEx string (offsets 0xF8..0x133)

PR1 SYSX bytes set to BYTE1=0x01, BYTE2=0x02, BYTE3=0x03, BYTE4=0x04 produced:

```
0xF8 = 0x01 (BYTE1)   0xF9 = 0x00
0xFA = 0x02 (BYTE2)   0xFB = 0x00
0xFC = 0x03 (BYTE3)   0xFD = 0x00
0xFE = 0x04 (BYTE4)   0xFF = 0x00
```

30 bytes × 2-byte pair encoding = 60 bytes from `0xF8..0x133`. Default (unset) value = `0x01 0x00` per pair (visible as the stretch of `01 00 01 00 …` in baseline dumps).

SysEx ON/OFF toggle: at `0xF6` — see the dedicated section below (T5-confirmed).

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

The earlier ambiguity about whether `0xCA` was shared with CMD1's type byte is resolved: T5 changed the IA pattern *without* touching CMD1 and `0xCA` flipped exactly as predicted from IA bits alone. CMD1's type byte lives at `0xCE` — the CMD block starts *after* the bitmap (see the Custom MIDI section).

### SysEx ON/OFF toggle ✅

Per-preset SysEx ON/OFF flag at **`0xF6`** in each preset frame. T5 set PR2 SysEx → ON and the only byte that changed in PR2's frame was `0xF6: 0x00 → 0x01`.

```
0xF6 = 0x00 — sysex string suppressed when preset recalls (SYSX P2 = OFF)
0xF6 = 0x01 — sysex string sent when preset recalls   (SYSX P2 = ON)
```

### Custom MIDI block — superseded readings (kept for the record)

Two earlier layouts were documented and both are now known to be wrong:

- **"16-byte stride, type at 0xCA"** — built on T1's `0xCA: 0x10→0x1F` flip,
  read as "type byte = 0x1F = N ON>". In fact T1's PR1 "max state" also
  turned **all IA switches on**, and with Bank Size 5 that sets the SW6-10
  bitmap byte `0xCA` to `0x1F` (five bits set). The "type byte" was the IA
  bitmap all along; `0x1F` as an N ON> encoding was a coincidence.
- **"12-byte stride, type-first at 0xCA, data at +6/+8"** (the layout app.py
  implemented until 2026-07-19) — anchored on `0xD6 - 0xCA = 12`. But `0xD6`
  is CMD2's slot base in the true 8-byte grid (`0xCE + 8`), and `0xCA` isn't
  a CMD byte at all. Under the 12-byte reading only ~1% of the 600 corpus
  slots decoded to plausible values; under the 8-byte layout, 100% do.

This also retires the "CMD4 data2 = 0xF6" collision: the historical
corruption (saving CMD4 flipped the SysEx toggle) was real, but it was
caused by the *editor writing to the wrong offset* under the 12-byte model —
not by the device overlapping the fields. The device's own layout has no
collision.

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

Each PC slot is **2 bytes** at offsets `0x06, 0x08, 0x0A, …` — value byte holds preset index `0..119` (preset number minus 1). 128 slots × 2 bytes + 6 header = ~262 bytes. ✓

**`OFF` encoding = value byte `0x78` (120)** — resolved offline: the baseline
dump's own PC map contains exactly 8 unmapped slots and every one stores low
byte `120` with high byte `0x00`; the high byte is `0x00` across all 128
slots (`tools/analyze_captures.py`, Hunt 2). The editor now writes `0x78` to
clear a mapping (previously it could only overwrite, never clear).

### Frame 122 (223 B) — global config + state ✅ revised after T5

Confirmed byte locations from `rev_T5_final.syx` (the earlier T2 attributions for `0x30`/`0x32` were wrong — those were operational side-effects, not bank size/style):

```
0x08 = Operating Mode  (0x00 = BANK, 0x01 = SONG, 0x02 = REMOTE)
0x44 = Bank Size       (0x00 = 5, 0x02 = 1, 0x04 = 10?, 0x06 = 15? — 10/15 best guess)
0x46 = Bank Style      (0x00 = FIRST, 0x01 = CURNT, 0x02 = NONE)
0x2A = ? (PER-PR-mode-related flag, changed when SW11 toggled to PER PR)
```

⚠️ **The `0x44`/`0x46` attribution is ambiguous** (found by the 2026-07-19
offline re-analysis). T5 changed bank size (→1), bank style (→CURNT) *and*
SW1/SW2 switch types (MOM/HOLD) in one session, producing exactly two new
bytes: `0x44=0x02, 0x46=0x01`. Those values fit **both** readings — (bank
size=1 → 0x02, style CURNT → 0x01) *and* (SW1=MOM → 0x02, SW2=HOLD → 0x01).
T6 then set SW3/SW4 types and wrote the adjacent `0x40=0x02, 0x42=0x01`,
which looks type-shaped. Worse, T2 nominally set Bank Size = 10 and **no
frame-122 byte moved to `0x04`** — either the edit never persisted or bank
size doesn't live at `0x44`. Resolution needs T7 (change bank size alone,
dump, diff). Until then treat bank-size/style write-back as unverified.

Several other bytes (`0x76, 0x78, 0xB0, 0xB8, 0xC0, 0xC8, 0xCE, 0xD0`) change between dumps even with no user edits — operational state the device updates on every dump (last preset / last bank / dump counter). Ignored when modeling user state.

The 17-slot pattern at `0x4E..0xCF` (chan-id values 0x0B, 0x12, 0x0F, …) appears static across dumps and likely caches the most recent IA slot config — not a primary editable surface.

### Frame 123 (139 B) — MIDI Filter mask + Starting Preset ✅ partial

This is NOT all `01 00` pairs as a previous draft thought — it carries SETUP P6 (Starting Preset per channel) and SETUP P7 (MIDI Filter mask).

Confirmed bytes:

```
0x6A = Starting Preset for CH1  (0x01 default, 0x00 after toggle)
0x08, 0x0C = MIDI Filter bits  (0x01 default = MERG, 0x00 = BLOC)
```

T3's notes say "CC on CH2 = BLOC and Note-On on CH5 = BLOC" produced flips at
`0x08` and `0x0C` — but a T7-session device check (photo of SETUP P7) shows
the filtering page has **no channel field at all**: two fields only,
`<msg type> <bloc/merg>`. Filtering is per message type, device-wide.

**Working model (to be confirmed by T7 items 4a-4c):** one 2-byte cell per
message type from `0x06`, in the manual's list order — the same enum as the
CMD types. That re-reads T3's flips as `0x08` = NOTE ON (index 1) and
`0x0C` = CTR CHANGE (index 3); the "channels" in T3's notes never existed on
the page. Predicted cells: NOTE OFF `0x06`, NOTE ON `0x08`, KPRS `0x0A`,
CTR CHANGE `0x0C`, PROG CHANGE `0x0E`, … The Starting-Preset table
(`0x6A` = CH1) is a separate region later in the same frame.

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

T4 confirmed by setting SET1: `BK1=SG3, BK2=SG4, BK3=SG5`:

```
0x06 = 0x02 (= SG3 - 1)   bank 1
0x08 = 0x03 (= SG4 - 1)   bank 2
0x0A = 0x04 (= SG5 - 1)   bank 3
```

Stride per bank slot = 2 bytes. 50 banks × 2 = 100 bytes payload + 6-byte header + 1-byte tail = 107 ✓.

**Indexing resolved (offline re-analysis, 2026-07-19):** the programmatic
re-diff of T4 shows the SET1 edit landed in the **first 107-byte frame** of
the dump (0-based dump frame 134, i.e. immediately after the ten song
frames). There is no off-by-one and no header frame — `SET n` = the n-th
107-byte frame, slot value = song# − 1. The earlier "frame 135" observation
was a frame-counting slip (1-based vs 0-based).

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

Full observation matrix (programmatically re-derived 2026-07-19, frame-122
region `0x28..0x48`, one value byte every 2):

| Dump | Device edits that session | `0x28..0x48` value bytes |
|---|---|---|
| baseline | — | `01 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00` |
| T2/T3 | SW6/SW7 types (+ PER-PR flags) | `01 01 00 00 02 01 00 …` — new at `0x2A, 0x30, 0x32` |
| T4 | (T2's type edits reverted) | back to baseline pattern |
| T5 | bank size 1, style CURNT, SW1→MOM, SW2→HOLD | new at `0x44 = 02, 0x46 = 01` |
| T6 | SW3→MOM, SW4→HOLD | adds `0x40 = 02, 0x42 = 01` |

Values are always in the confirmed type enum {`00`=LATCH, `01`=HOLD,
`02`=MOM}, but no simple indexing (linear, reversed, banksize-relative, or
MRU stack) fits all three sessions — and T5's `0x44/0x46` are double-claimed
by the bank-size/style attribution (see the frame-122 caution above). The
cell-to-switch mapping is genuinely underdetermined by the existing captures;
this is a T7 item. **Editor keeps switch-type write-back disabled.**

## 6. Hardware quirks observed during testing

These came up while wiring the editor, useful to know when interpreting MIDI traffic:

- **Recall echo.** When the All Access receives a Program Change on its MIDI Receive Channel (`MIDI P7`, default ch1) and `Program Change Status` is `ON`, it recalls the matching preset and immediately broadcasts **the entire preset's PC + CC chain** on its MIDI Out. So sending `PC 0 ch1` to recall `MAN OF 1` causes the device to echo back ~10 events (PC on the device's other channels + CCs for every IA switch the preset toggles). The editor's IA LEDs auto-light from this echo, no need to read the dump's IA bitmap.
- **MR9 program byte on ch1.** PC traffic on ch1 is **not** a preset-recall signal — it's the device's outgoing program change for the MR9 loop controller (which happens to live on ch1 in the user's rig). For example, PR1 (`MAN OF 1`) sends PC 99 to the MR9. The editor explicitly does not interpret incoming ch1 PC as a preset selector; the foot-controller LCD is driven by virtual-switch clicks only.
- **No remote dump request.** The All Access has no documented MIDI message that asks it to send a bulk dump. The only way to capture a dump is to initiate it from the front panel (`SYSX → DUMP → CTR STORE`). The editor's "Read All" therefore arms a passive capture and waits.

## 7. T7 — the one remaining hardware capture session

The T0–T6 series plus the 2026-07-19 offline re-analysis
(`tools/analyze_captures.py`) resolved everything except the items below.
They share one property: the existing captures either never changed the
relevant bytes or changed too many at once. One more session pins them all.
**Golden rule learned from T2/T3: change ONE thing, dump, diff, repeat** —
several T2 edits (bank size 10, PED1 PER-PR, style NONE) left no trace,
almost certainly because CTR STORE wasn't pressed between edits.

| # | Item | Device steps | What the diff will show |
|---|------|--------------|-------------------------|
| 1 | **Bank size vs 0x44** | Change ONLY Bank Size 5→10, dump. Then 10→15, dump. | If frame 122 `0x44` → `0x04` then `0x06`, bank size is confirmed there and the T5 reading stands; if some other byte moves, `0x44/0x46` belong to switch types. |
| 2 | **Switch-type cell map** | Set SW1→MOM, dump. SW5→HOLD, dump. SW15→MOM, dump. (three separate dumps) | Three single-cell diffs in `0x28..0x48` reveal the SW#→offset rule (and settle #1's ambiguity from the other side). |
| 3 | **PER-PR header semantics** | Configure PER-PR on SW2 (CH6/CC10) *and* SW9 (CH12/CC20) in one preset, dump. | If `0x40/0x42` hold only the last-configured pair, it's an MRU record and per-slot channels must be hunted elsewhere; if two channel bytes appear, it's per-slot. |
| 4 | **Filter-grid geometry** | Set BLOC for one message type across CH1, CH2, CH3 (three dumps). | Cell offsets per channel give the channel stride; combined with T3's `0x08/0x0C` (type stride 4) the full (type × channel) grid falls out. |
| 5 | **Space acceptance (moot but cheap)** | Editor now writes device-native `0x2E` for space, so no risk remains — optionally Write All a renamed preset and confirm the LCD. | Round-trip sanity only. |
| 6 | **Enum stragglers** | Set op mode REMOTE, dump; PC status ON, dump; one CMD to `KPRS>` and one to `STOP`, dump. | Confirms `REMOTE=0x02`, `ON=0x01`, and two inferred CMD type codes (`0x02`, `0x0A`) — spot-checking the manual-order enum. |
| 7 | **Song/set OFF markers** | Set one song slot and one set bank to `OFF`, dump. | Whether songs/sets use the PC-map's `value=count` OFF convention (120/150) or something else. |

Method: **Save .syx** baseline first; after each single edit press CTR STORE,
dump via Read All, Save .syx, and diff with `tools/diff_dumps.py` (or run
`tools/analyze_captures.py baseline.syx new.syx` for the annotated report).
Keep the baseline as your restore point.

The full runnable session — these captures plus on-device validation of the
new write paths (CMD slots, 0x2E spaces, PC-map clear) — is scripted in
[HARDWARE_TEST_PLAN.md](HARDWARE_TEST_PLAN.md).

## 8. Timing for writing back

Manual: *"about 65 Hz (or about 1 byte every 15 milliseconds). Faster transfer will cause a "Buffer Overflow" error on All Access."*

`app.py` throttles between frames with `time.sleep(max(0.03, n * 0.015))` — at least 30 ms gap, longer for big frames. Conservative but reliable.

## 9. Save / Load round-trip

The editor's **Save .syx** button (`/api/dump/save`) streams the current `STATE['dump_bytes']` to the browser as a download. After a fresh **Load** with no edits, the saved file is byte-identical to the original (verified with `cmp`). After a preset rename, only the 24 name bytes inside the affected 309-byte frame change — every other byte (header, payload, tail) is preserved.

This is the safest possible writeback path: the editor never reconstructs a frame from decoded fields, only splices new bytes into the original raw frame at known offsets.
