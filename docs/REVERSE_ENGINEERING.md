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

### IA slot table (offsets 0x40..0xC7) — 17 slots × 8 bytes ✅ fully resolved

The region between the PC slots and the custom MIDI block is a **17-slot table** holding per-IA-slot config (channel + CC# + ON + OFF values) for each of the 15 IA switches + 2 pedals. Slots are stored in **REVERSED slot-index order** (slot for chan-id 17 first, descending to chan-id 1 at 0xC0) — same reversal pattern as the switch names in frame 123. Formula: `offset(chanid) = 0x40 + (17 - chanid) * 8`.

PR1 baseline default state shows the layout cleanly:

```
0x40: 00 00 11 00 7F 00 00 00   ← slot for chan-id 17 (PED1)
0x48: 00 00 10 00 7F 00 00 00   ← slot for chan-id 16 (PED2)
0x50: 00 00 0F 00 7F 00 00 00   ← chan-id 15
0x58: 00 00 0E 00 7F 00 00 00   ← chan-id 14
…
0x70: 00 00 0B 00 7F 00 00 00   ← chan-id 11 (SW11)
…
0xC0: 00 00 01 00 7F 00 00 00   ← slot for chan-id 1 (SW1)
```

**Field layout (resolved offline, 2026-07-20 T7 session, `app.py::decode_per_pr_slots`):**

| Offset within slot | Field | Default | Meaning |
|---|---|---|---|
| +0 | **Channel** (0-based!) | `0x00` = CH1 | the earlier "flag byte" reading was wrong — this is the MIDI channel, 0-based |
| +2 | **CC#** | slot's own chan-id number (e.g. SW11 defaults to CC#11) | the default happening to equal the chan-id is what made +2 look like a "slot identifier" before |
| +4 | **ON value** | `0x7F` (127) | |
| +6 | **OFF value** | `0x00` (0) | |

**Resolution evidence — two independent sessions agree exactly:**

1. A clean single-preset T7 capture configured SW9 (CH12/CC20/ON100/OFF50) and SW2 (CH6/CC10/ON127/OFF0) with no other edits. Result: SW9's slot (`0x80`, chanid 9) → `channel=0x0B(=CH12 0-based), cc=0x14(20), on=0x64(100), off=0x32(50)` — all four fields land exactly on target. SW2's slot (`0xB8`) → `channel=0x05(=CH6 0-based), cc=0x0A(10)` (ON/OFF unchanged because the targets equalled the defaults).
2. Re-checking the **original T2 capture** against **PR1** (not PR3 as the T2 steps intended — see the quirk note below) with this same formula: SW11's slot (`0x70`, chanid 11) → `channel=0x02(=CH3), cc=0x2A(42), on=0x64(100), off=0x14(20)`, and PED1's slot (`0x40`, chanid 17) → `channel=0x03(=CH4), cc=0x0B(11)` — matching the T2.D steps (`SW11 → CH3/cc42/on100/off20`, `PED1 → CH4/cc11/on127/off0`) byte-for-byte.

**chan-id mapping confirmed:** 1-15 = SW1-15 directly, **17 = PED1** (confirmed above), 16 = PED2 (by the same descending pattern; not yet independently isolated).

**Quirk uncovered while reconciling this:** T2.D's steps say "recall PR3" before configuring SW11/PED1, but the resulting bytes land on **PR1**, not PR3 — the recall apparently didn't take effect (possibly a Bank-Size-10 navigation issue at the time) and CTR STORE saved onto whichever preset was still active. Worth remembering when running any preset-targeted capture: verify the LCD shows the intended preset number immediately before CTR STORE.

The editor decodes this table read-only (`per_pr_slots` in each preset's parsed output); write-back UI is a natural follow-up using the same splice pattern as the other preset fields.

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

This is the manual's **CUSTOM P1 command list, in its exact printed order**
(verified against `reference/rocktron-manual.pdf` p.62). Four codes are
hardware-observed — `N ON>`=0x01, `KPRS>`=0x02, `C CH>`=0x03, `NONE`=0x11 —
and all four sit exactly on their list index, including both endpoints
(index 1 and the terminal NONE at 17, the `0x11` "default marker" seen at
every unset slot). The intermediate codes follow the same documented list,
so `stored byte = CUSTOM P1 list index`. (Note: the MIDI *filter* page uses
a different list order — see the Filter section — so don't cross-map the
two.)

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

### Frame 122 (223 B) — global config + state ✅ revised after T7 (2026-07-20)

**Bank Size and Bank Style were misattributed to this frame.** A prior
ambiguity note (kept below for the record) flagged that `0x44`/`0x46` could
be either bank-size/style or switch-type cells. The T7 hardware session
resolved it completely: **`0x44` and `0x46` are switch-type cells (SW2 and
SW1)** — see the Switch Type section below. Bank Size actually lives in the
**tail frame** (§ Frame 144 below); **Bank Style's real location is still
unknown** — the old "FIRST/CURNT/NONE" claim was misread from T5's compound
edit and is now retracted. (Note the manual's Bank Style values are actually
**FIRST / CURNT / OFF**, not "NONE".) This was a real bug: the app's write path used to
silently corrupt SW1/SW2's switch type whenever Bank Size or Bank Style was
saved. Fixed in `app.py::encode_globals` (bank_style writes are now a
documented no-op; bank_size targets the tail frame).

Confirmed fields:

```
0x08 = Operating Mode — UNCONFIRMED, see the caution below
0x2A..0x46 = Switch Type table (see dedicated section below)
0x2A = also the location of SW15's type cell (was previously misread as a
       "PER-PR-mode-related flag" — see the T7 finding, it's just SW15)
```

⚠️ **Operating Mode discrepancy — unresolved.** The T7 session set Operating
Mode to REMOTE as a clean, isolated edit. Frame-122 `0x08` (the byte
`encode_globals` currently writes) **did not change**. Instead, **tail frame
`0x0A`** flipped `0x00 → 0x02`. Either the original T2-derived "`0x08` =
operating mode" claim was wrong from the start, or `0x08` tracks something
that only differs for BANK↔SONG transitions and REMOTE is a special case.
The code still writes `0x08` (removing working behaviour on a hunch is worse
than leaving it), but this needs a **T8 follow-up**: a clean BANK↔SONG
single-edit dump, ideally combined with a Write-All-then-Read-back test to
check whether the app's current write path actually takes effect on
hardware at all.

Several other bytes (`0x76, 0x78, 0xB0, 0xB8, 0xC0, 0xC8, 0xCE, 0xD0`) change between dumps even with no user edits — operational state the device updates on every dump (last preset / last bank / dump counter). Ignored when modeling user state.

The 17-slot pattern at `0x4E..0xCF` (chan-id values 0x0B, 0x12, 0x0F, …) appears static across dumps and likely caches the most recent IA slot config — not a primary editable surface.

**Bonus finding — IA operating-status bitmap (GLOBAL vs PER PR).** Setting
SETUP P4 to `PER PR` for SW2 and SW9 (as part of the T7 PER-PR test) flipped
two extra globals-frame bytes: `0x16: 0x01→0x00` (SW2) and `0x24: 0x01→0x00`
(SW9). Formula: `offset(SW#) = 0x12 + SW# * 2`. Encoding: `0x01` = GLOBAL
(default), `0x00` = PER PR. Not yet wired into the app (read-only decode
would follow the same pattern as Switch Type).

### Switch Type table (globals frame, 0x2A..0x46) ✅ fully resolved

**Formula:** `offset(SW#) = 0x46 - (SW# - 1) * 2` for SW1..SW15 — SW1 at
`0x46`, counting **down** by 2 bytes per switch to SW15 at `0x2A`.
**Encoding:** `LATCH = 0x00` (default), `MOMENTARY = 0x01`, `HOLD = 0x02`
(this corrects an earlier draft that had MOM/HOLD swapped).

Resolved by the T7 session's three clean single-switch edits, and the
formula/encoding **retroactively explains the original T5/T6 data with zero
contradictions** once re-decoded correctly:

| Test | Edit | Byte(s) | Formula says | Match |
|---|---|---|---|---|
| T7 2a | SW1 → MOMENTARY | `0x46: 00→01` | SW1@0x46=MOM(0x01) | ✅ |
| T7 2b | SW5 → HOLD | `0x3E: 00→02` | SW5@0x3E=HOLD(0x02) | ✅ |
| T7 2c | SW15 → MOMENTARY | `0x2A: 00→01` | SW15@0x2A=MOM(0x01) | ✅ |
| T5 (original) | SW1→MOM, SW2→HOLD | `0x44=02, 0x46=01` | SW2@0x44=HOLD(0x02), SW1@0x46=MOM(0x01) | ✅ |
| T6 (original) | SW3→MOM, SW4→HOLD | `0x40=02, 0x42=01` | SW4@0x40=HOLD(0x02), SW3@0x42=MOM(0x01) | ✅ |

Five independent data points, zero contradictions. The earlier "stack-style,
most-recently-modified-switch-at-0x40" hypothesis was an artifact of never
having done a single-switch isolation test — the real structure is a plain
linear table. Implemented in `app.py::decode_globals`/`encode_globals`
(`switch_types` field) and exposed in the editor's Channels & Switches tab.

### Frame 123 (139 B) — MIDI Filter mask + Starting Preset ✅ resolved

This is NOT all `01 00` pairs as a previous draft thought — it carries SETUP P6 (Starting Preset per channel) and SETUP P7 (MIDI Filter mask).

```
0x6A = Starting Preset for CH1  (0x01 default, 0x00 after toggle)
0x06..0x28 = MIDI Filter cells (see below)
```

**Resolved 2026-07-20.** A T7-session photo of the SETUP P7 device screen
shows it is **two fields only** — `<msg type> <bloc/merg>` — with **no
channel field at all**. MANUAL_SUMMARY.md's "per message-type AND per
channel" description is technically correct (see below) but the two axes
aren't a grid: the page cycles through a single **34-entry flat list** —
18 message types followed by 16 channel entries — each with its own
BLOC/MERG toggle. Photographed cycle order (exact, page P7):

```
Note Off, Note On, Key Pressure, Ctr Change, Prog Change, Chan Pressure,
Pitch Bend, System Excl, MTC, Song PP, Song Select, Tune Request,
Timing Clock, Start, Continue, Stop, Active Sens, System Rst,
Channel 1, Channel 2, … Channel 16
```

Note this order is the **standard MIDI status-byte order** (channel voice →
sysex → system common → system realtime), and it **differs** from the
Custom-MIDI-command list (CUSTOM P1) after the first 7 entries — the two
pages share NOFF/NON/KPRS/CCH/PCH/CPRS/PBEN (indices 0-6) but diverge from
index 7 onward (filter goes SysEx→MTC→…; CUSTOM P1 goes T CLK→START→…). These
are **two independent lists**: the filter cells use this filter-page order,
while the CMD-type byte uses the **CUSTOM P1 list order** (documented in the
manual — see the Custom MIDI section). Don't cross-map them.

**Formula (confirmed):** `offset(type_index) = 0x06 + type_index * 2` for
the first 18 entries (message types), using the list order above.

| Test | Edit | Result | Match |
|---|---|---|---|
| T7 4a | Ctr Change → BLOC | `0x0C: 01→00` | index 3 → `0x06+6=0x0C` ✅ |
| T7 4b | Prog Change → BLOC | `0x0E: 01→00` | index 4 → `0x06+8=0x0E` ✅ |
| T7 4c | Note Off → BLOC | `0x06: 01→00` | index 0 → `0x06+0=0x06` ✅ |

This also retroactively explains T3's original flips (`0x08`=Note On,
`0x0C`=Ctr Change) — the "CH2"/"CH5" in T3's old notes never existed on the
page; T3 just landed on two type cells.

**Not yet directly tested:** the 16 channel-entry cells (indices 18-33).
Predicted range `0x2A..0x48` continuing the same 2-byte stride, but this
overlaps the Switch Type table's byte range in the *other* frame only by
coincidence of offset value — filter cells live in the 139 B frame, switch
types in the 223 B frame, so there's no real collision, just a reminder to
double-check which frame a given `0x2A` reference belongs to when reading
this document.

### Frames 124..133 (10 × 457 B) — SONGS ✅ (correcting earlier guess)

Earlier guess had the song/set assignment swapped. **The 457 B frames hold SONGS, the 107 B frames hold SETS** (confirmed by T4 — see frame 134..143 below).

Each 457 B frame holds **15 songs**, each song = **30 bytes** = **15 preset
slots × 2 bytes**, no overhead. (A song is one bank's worth of presets, and
max Bank Size is 15 — the manual's Song Create page assigns to `SW1-15`. An
earlier draft said "10 slots + 10 overhead"; that was wrong — the baseline's
own SONG1 stores 15 valid preset indices, and the decoder now reads all 15.)
Confirmed from `rev_T3_songs.syx`:

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

So SONG stride = 30 bytes; presets stored as `(preset_index, 0x00)` pairs at 2-byte stride, 15 slots each. 15 songs × 30 bytes = 450 bytes data + 7 bytes header/padding = 457 ✓. 10 frames × 15 songs = **150 songs** total ✓ (matches manual).

**No OFF state (confirmed by the manual + hardware).** The manual's Song
Create page (SONG/SET P2, p.56) offers three fields only — `SONG1-150 /
SW1-15 / PR1-120` — the preset field cycles `PR1-120` with **no OFF option**.
On the device the page shows `SG1 SW1 PR4`-style, and there is no `OFF`/blank
entry to select (which is exactly why the T7 attempt to "set a slot to OFF"
was impossible). This matches the baseline dump, where every song slot holds
a valid preset index. There is no OFF-marker convention for songs (unlike the
PC-map, which does expose `OFF`) — every song slot always references a real
preset.

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

**No OFF state (manual + hardware).** Same as songs — Set Create (SONG/SET
P3, p.58) offers `SET1-10 / BK1-50 / SONG1-150`; the song field cycles
`SONG1-150` with no OFF option (device shows `set2 bk1 sg1`-style). Every
bank slot must reference a real song.

### Frame 144 (23 B) — tail / global config block ✅ revised after T7 (2026-07-20)

Originally suspected to be a checksum + dump-type. Actually carries **more globals**. All major fields now decoded:

```
0x06 = ? operational flag (flips between dumps without edits)
0x08 = Program Change Status  (0x00 = OFF, 0x01 = ON, 0x02 = MAP)        ✅ fully confirmed
0x0A = possibly Operating Mode (REMOTE=0x02 observed) — see the frame-122
       operating-mode caution above; BANK/SONG values not yet captured here
0x0C = Bank Size  (1=0x00, 5=0x01, 10=0x02, 15=0x03)                     ✅ relocated here from
       the old (wrong) frame-122 0x44 guess
0x0E = ?  (still unexplained — was paired with PC status in an earlier guess
       that assumed 0x0A/0x0C were also PC-status-related; now that 0x0A/0x0C
       have their own explanations, 0x0E's role is open again)
0x10 = Remote Title Number  (0..127)                                      ✅
0x12 = MIDI Receive Channel  (0..15 = ch 1-16, 0x10 (=16) = OMNI)         ✅
0x14 = 0x66                   ← constant across all captured dumps
0x15 = 0x01                   ← constant
0x16 = F7
```

**PC Status fully confirmed** (2026-07-20): a clean ON→MAP→OFF sequence in
the T7 session hit all three values in order (`0x01`, `0x02`, `0x00`) —
`ON=0x01` was previously only a best guess, now hardware-proven.

**Bank Size fully confirmed, relocated.** Three clean single-edit dumps
(5→10→15→1) walked the byte `0x01→0x02→0x03→0x00`, a simple ordinal
encoding matching the manual's own list order (1, 5, 10, 15). This is a
different byte, in a different frame, from the two previous (wrong) guesses
— see the frame-122 caution above.

`66 01` before `F7` confirmed constant across all captured dumps — not a checksum, just an end-of-dump marker.

## 6. Hardware quirks observed during testing

These came up while wiring the editor, useful to know when interpreting MIDI traffic:

- **Recall echo.** When the All Access receives a Program Change on its MIDI Receive Channel (`MIDI P7`, default ch1) and `Program Change Status` is `ON`, it recalls the matching preset and immediately broadcasts **the entire preset's PC + CC chain** on its MIDI Out. So sending `PC 0 ch1` to recall `MAN OF 1` causes the device to echo back ~10 events (PC on the device's other channels + CCs for every IA switch the preset toggles). The editor's IA LEDs auto-light from this echo, no need to read the dump's IA bitmap.
- **MR9 program byte on ch1.** PC traffic on ch1 is **not** a preset-recall signal — it's the device's outgoing program change for the MR9 loop controller (which happens to live on ch1 in the user's rig). For example, PR1 (`MAN OF 1`) sends PC 99 to the MR9. The editor explicitly does not interpret incoming ch1 PC as a preset selector; the foot-controller LCD is driven by virtual-switch clicks only.
- **No remote dump request.** The All Access has no documented MIDI message that asks it to send a bulk dump. The only way to capture a dump is to initiate it from the front panel (`SYSX → DUMP → CTR STORE`). The editor's "Read All" therefore arms a passive capture and waits.
- **⚠️ REMOTE mode destabilizes the CUSTOM MIDI page.** Discovered during the T7 session (2026-07-20): with Operating Mode set to REMOTE, the CUSTOM MIDI editing page (`2ND → CUSTOM`) worked for exactly one edit and then became unresponsive on any further edit, requiring a power cycle. Revert Operating Mode to BANK before doing any Custom MIDI work on the device. (This may be related to the still-unresolved Operating Mode byte discrepancy above — REMOTE mode changes preset addressing, and CUSTOM P1's preset-select field may not handle that correctly in this firmware.)
- **A "recall preset" step in a procedure can silently fail.** T2.D's steps said "recall PR3" before configuring PER-PR values, but the resulting bytes landed on PR1 — the recall didn't take, and CTR STORE saved onto whichever preset was actually active. Always confirm the LCD shows the intended preset immediately before CTR STORE, especially after a bank-size change (which remaps which physical switches are preset switches).
- **Song and Set assignment pages have no OFF state** (manual SONG/SET P2/P3, pp.56-58). Unlike the PC-map (which does support `OFF` to unmap a PC), Song Create (`PR1-120`) and Set Create (`SONG1-150`) only cycle through valid targets — there's no way to leave a slot unassigned from the front panel. A song has 15 preset slots (SW1-15); only the first Bank-Size are active but all 15 are stored.
- **⚠️ Bulk load (restore) drops the last preset + corrupts some options** (reported 2026-07-21). After any bulk restore, PR120 is blank and a few global options are wrong — reproducible. **It's the device's own bulk-load firmware, not the editor:** a device-to-device restore (a known-good second unit dumping over Rocktron's native format) leaves PR120 blank too, and the editor is confirmed to send all 145 frames correctly at spec pacing. Undocumented in the manual. **Working recovery: factory reset (SETUP P9 = code 230) first, then restore on top — PR120 sticks.** That implies the bug is tied to stale/persistent memory state (a clean slate commits everything; restoring over existing data doesn't flush the last preset). Parked for a future session — needs a post-restore dump diff to pick the fix. Full detail in [HARDWARE_TEST_PLAN.md](HARDWARE_TEST_PLAN.md).

## 7. T7 — completed 2026-07-20; T8 follow-up remains

The T7 session (15 clean single-edit captures: 1a-1c, 2a-2c, 3, 4a-4c, 5a-5c,
6) resolved almost everything that was still open. Results, cross-checked
against the original T2/T5/T6 data with zero contradictions:

| # | Item | Result |
|---|------|--------|
| 1 | Bank size location | ✅ **Relocated** — tail frame `0x0C`, not frame-122 `0x44`. Encoding `1=0x00,5=0x01,10=0x02,15=0x03`. |
| 2 | Switch-type cell map | ✅ **Fully resolved** — linear table, `offset(SW#)=0x46-(SW#-1)*2`, `LATCH=0,MOM=1,HOLD=2`. |
| 3 | PER-PR header semantics | ✅ **Resolved** — slot `+0` = channel (0-based), not a "flag". Confirmed twice independently (new SW9/SW2 test + re-decoded old T2 SW11/PED1 test). |
| 4 | Filter-grid geometry | ✅ **Resolved, model corrected** — no channel field exists on the filter page at all; it's 18 message-type cells + 16 channel cells in one flat list, `offset=0x06+type_index*2`. |
| 5 | Space acceptance | ✅ Moot — 0x2E already in use, no hardware risk. |
| 6 | Enum stragglers | ⚠️ **Partial** — `PC status ON=0x01` and `KPRS>=0x02` confirmed. REMOTE mode caused a device crash after one CUSTOM MIDI edit (see Hardware Quirks) so `STOP` wasn't captured, and REMOTE's target byte turned out to be tail `0x0A`, not frame-122 `0x08` as expected — see the T8 item below. |
| 7 | Song/set OFF markers | ✅ **Resolved (negative result)** — neither page exposes an OFF option; every slot must reference a valid target. |

Bonus finding not on the original list: the **IA GLOBAL/PER-PR bitmap**
location (globals frame, `offset=0x12+SW#*2`).

### T8 — one small follow-up

The refined session is **8 read-only capture dumps** (reusing T7's `5a.syx`
for the REMOTE data point):

| id | Item | What it settles |
|----|------|------------------|
| 1a | BANK-mode CUSTOM stability | Confirms the CUSTOM crash was REMOTE-specific, not an editor bug; yields 3 more CMD-type anchors. |
| 2a | Operating Mode: BANK→SONG | Which byte tracks SONG; with baseline (BANK) + `5a` (REMOTE) fully maps the field and resolves the frame-122 `0x08` vs tail `0x0A` discrepancy. |
| 3a-3c | Bank Style FIRST/CURNT/OFF | Bank Style's real byte (now that `0x46` is proven to be SW1's switch type). |
| 4a | Filter Channel 1 → BLOC | The 16 per-channel filter cells (predicts `0x2A`). |
| 4b | Starting Preset CH2 | Per-channel Starting Preset offsets (only CH1 at `0x6A` known). |
| 4c | PED2 PER-PR | chan-id 16 = PED2 (only PED1=17 confirmed). |

Method: **Save .syx** baseline first; after each single edit press CTR STORE
where needed, dump via Read All, Save .syx, and diff with
`tools/analyze_captures.py baseline.syx new.syx`. Keep the baseline as the
restore point.

**Separately, Part 2 (write-path validation) is blocked** on the Write All
last-preset drop (see Hardware Quirks above) — that must be diagnosed and
fixed before any editor→device write test is meaningful.

Full narrative + exact manual-verified button presses:
[HARDWARE_TEST_PLAN.md](HARDWARE_TEST_PLAN.md).

## 8. Timing for writing back

Manual: *"about 65 Hz (or about 1 byte every 15 milliseconds). Faster transfer will cause a "Buffer Overflow" error on All Access."*

`app.py::dump_send` streams the whole 145-frame dump as one continuous byte
stream, **1 byte per `send_message`, `time.sleep(0.015)` between bytes**, with
**no inter-frame pause** (chunk_size=1, byte_delay=15 ms, inter_frame=0). Real
transfers measure ~18 ms/byte once OS/USB scheduling overhead is added, so the
device is never driven faster than spec. Earlier tests found that byte delays
>15 ms or *any* inter-frame pause made the device commit early and ignore
later frames, so these values are hard-locked. **Caveat:** this pacing still
reproducibly loses the last preset (PR120) on restore — see the Write All
quirk under §6 and [HARDWARE_TEST_PLAN.md](HARDWARE_TEST_PLAN.md).

## 9. Save / Load round-trip

The editor's **Save .syx** button (`/api/dump/save`) streams the current `STATE['dump_bytes']` to the browser as a download. After a fresh **Load** with no edits, the saved file is byte-identical to the original (verified with `cmp`). After a preset rename, only the 24 name bytes inside the affected 309-byte frame change — every other byte (header, payload, tail) is preserved.

This is the safest possible writeback path: the editor never reconstructs a frame from decoded fields, only splices new bytes into the original raw frame at known offsets.
