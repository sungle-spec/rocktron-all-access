"""Pure byte-preserving SysEx codec. No Flask, MIDI, filesystem, or hardware I/O.

Unconfirmed fields must be preserved; see docs/REVERSE_ENGINEERING.md.
"""

# ── sysex protocol constants ─────────────────────────────────────────────────
SYX_HEADER = bytes([0xF0, 0x00, 0x00, 0x29, 0x08])   # F0 + Rocktron ID + device
CMD_PRESET_FIRST = 0x2A
CMD_PRESET_REST  = 0x2B
PRESET_FRAME_LEN = 309
NAME_BLOCK_LEN   = 279
PC_MAP_FRAME_LEN = 263
GLOBAL_STATE_FRAME_LEN = 223     # frame 122 — operating mode + bank + IA mode bitmap + state
FILTER_FRAME_LEN = 139           # frame 123 — MIDI filter mask + starting preset
SET_FRAME_LEN = 107              # 1 set per frame, 10 sets total
SONG_FRAME_LEN = 457             # 15 songs per frame, 10 frames = 150 songs total
TAIL_FRAME_LEN = 23              # final frame — also carries some globals (remote title, midi rx ch)

# Per-preset frame offsets (absolute, relative to F0)
NAME_OFF  = 0x06                 # corrected from 0x08 — title is 13 chars at 0x06..0x1E
NAME_CHARS = 13                  # corrected from 12

PC_SLOTS_OFF = 0x20              # T1 ascending values 0..15 occupy 0x20..0x3F
# Old 0x24 start skipped CH1/2 and overwrote the first PER-PR pedal slot.
PC_SLOTS_COUNT = 16
PC_SLOT_STRIDE = 2               # value byte + status byte (0x00=active, 0x01=inactive)

# IA on/off bitmap — 3 bytes, 5 bits each, REVERSED bit order within each byte.
# Confirmed by rev_T5_final.syx test (Bank Size = 1 + asymmetric pattern).
# 0xC8 bit 4 = SW1, bit 3 = SW2, bit 2 = SW3, bit 1 = SW4, bit 0 = SW5
# 0xCA bit 4 = SW6, bit 3 = SW7, bit 2 = SW8, bit 1 = SW9, bit 0 = SW10
# 0xCC bit 4 = SW11, bit 3 = SW12, bit 2 = SW13, bit 1 = SW14, bit 0 = SW15
IA_BITMAP_OFFS = (0xC8, 0xCA, 0xCC)   # (SW1-5, SW6-10, SW11-15)
IA_SWITCH_COUNT = 15

# Per-preset SysEx ON/OFF toggle — confirmed at 0xF6 from rev_T5_final.syx
SYSX_ON_OFF_BYTE = 0xF6

# Custom MIDI string — 5 commands × 8-byte slots starting at 0xCE.
# Slot layout (raw offsets within slot): +0 type, +2 channel (0-based),
# +4 data1, +6 data2; odd offsets are the usual 0x00 high-companion bytes.
# Layout confirmed offline (tools/analyze_captures.py): 600/600 slots across
# all 120 baseline presets decode cleanly, T1's CMD1 (N ON> ch1 n60 v100) and
# T4's CMD2 (C CH> ch5 cc20 v30) write-captures land exactly on these offsets,
# and PR106 carries a real command in slot 5 (0xEE). The region ends at 0xF5 —
# no overlap with the SysEx toggle (0xF6) or the IA bitmap (0xC8/0xCA/0xCC);
# the earlier "CMD1 type at 0xCA" reading was T1's all-IA-on bitmap (0x1F)
# misattributed as a type byte.
CMD_REGION_OFF = 0xCE
CMD_COUNT = 5
CMD_STRIDE = 8

# Type byte = index into the manual's CUSTOM P1 list (page order). Anchors:
# N ON>=0x01, C CH>=0x03, NONE=0x11; T7 reports KPRS>=0x02; the rest follow the list order
# (unobserved codes marked inferred in docs/REVERSE_ENGINEERING.md).
CMD_TYPE_ORDER = ['NOFF>', 'N ON>', 'KPRS>', 'C CH>', 'P CH>', 'CPRS>',
                  'PBEN>', 'T CLK', 'START', 'CONTU', 'STOP', 'ACTSN',
                  'SYSRS', 'M T C>', 'SGPP>', 'SGSL>', 'T REQ', 'NONE']
CMD_TYPE_LABELS = {i: lbl for i, lbl in enumerate(CMD_TYPE_ORDER)}
CMD_LABEL_TO_TYPE = {v: k for k, v in CMD_TYPE_LABELS.items()}

# SysEx string — 30 bytes × 2-byte pair encoding
SYSX_OFF = 0xF8
SYSX_COUNT = 30
SYSX_STRIDE = 2

NAME_STRIDE = 2  # ascii byte + 0x00 high byte (legacy alias used by encode_ascii_pairs)

# The All Access stores `.` (0x2E) as its space-equivalent character — most
# pre-90s Rocktron units have no real space char in their A-Z 0-9 set, so the
# user picks `.` to indicate word breaks. We display 0x2E as space and write
# 0x2E back for space — matching the device's own representation, since 0x20
# acceptance was never verified on hardware.

def decode_ascii_pairs(buf, off, n_chars):
    """Decode n_chars from (ascii_lo, hi) byte pairs. 0x00 ends the string."""
    out = bytearray()
    for k in range(n_chars):
        lo = buf[off + 2*k]
        if lo == 0:
            break
        # Treat the device's `.` as space; stop on anything non-printable.
        if lo == 0x2E:
            out.append(0x20)
        elif 0x20 <= (lo & 0x7F) <= 0x7E:
            out.append(lo & 0x7F)
        else:
            break
    return out.decode('ascii', errors='replace').rstrip()

def encode_ascii_pairs(s, n_chars):
    padded = (s or '').upper().encode('ascii', errors='replace')[:n_chars].ljust(n_chars, b' ')
    out = bytearray()
    for b in padded:
        # Store space as the device-native `.` (0x2E) — see note above.
        out.append(0x2E if b == 0x20 else b & 0x7F)
        out.append(0x00)
    return bytes(out)

# ── sysex parser ─────────────────────────────────────────────────────────────
def split_frames(buf):
    """Return list of (offset, length) for every F0..F7 frame in buf."""
    frames = []
    i = 0
    while i < len(buf):
        if buf[i] == 0xF0:
            j = i + 1
            while j < len(buf) and buf[j] != 0xF7:
                j += 1
            if j < len(buf):
                frames.append((i, j - i + 1))
                i = j + 1
            else:
                break
        else:
            i += 1
    return frames

def parse_dump(buf):
    """Parse a full bulk dump. Returns a dict of preset list + global blocks."""
    frames_info = split_frames(buf)
    frames = [buf[o:o+l] for (o, l) in frames_info]

    presets = []
    globals_raw = {
        'name_block': None,        # 279 B: channel + switch names
        'pc_map': None,            # 263 B: PC1..128 → preset
        'global_state': None,      # 223 B: operating mode + bank size + IA mode bitmap + state
        'filter_mask': None,       # 139 B: MIDI filter mask + starting preset
        'song_frames': [],         # 10 × 457 B: 150 songs total
        'set_frames':  [],         # 10 × 107 B: 10 sets × 50 banks
        'tail': None,              # 23 B end-of-dump (also carries remote title + MIDI rx ch)
        'misc': [],                # any unknown extras
    }

    preset_frames = [f for f in frames if len(f) == PRESET_FRAME_LEN]
    for idx, f in enumerate(preset_frames):
        presets.append(parse_preset_frame(idx + 1, f))

    for f in frames:
        n = len(f)
        if n == PRESET_FRAME_LEN:
            continue
        if n == NAME_BLOCK_LEN:
            globals_raw['name_block'] = bytes(f)
        elif n == PC_MAP_FRAME_LEN:
            globals_raw['pc_map'] = bytes(f)
        elif n == GLOBAL_STATE_FRAME_LEN:
            globals_raw['global_state'] = bytes(f)
        elif n == FILTER_FRAME_LEN:
            globals_raw['filter_mask'] = bytes(f)
        elif n == SONG_FRAME_LEN:
            globals_raw['song_frames'].append(bytes(f))
        elif n == SET_FRAME_LEN:
            globals_raw['set_frames'].append(bytes(f))
        elif n == TAIL_FRAME_LEN:
            globals_raw['tail'] = bytes(f)
        else:
            globals_raw['misc'].append(bytes(f))

    channel_names, switch_names = [], {'pedals': [], 'switches': []}
    if globals_raw['name_block']:
        channel_names, switch_names = decode_name_block(globals_raw['name_block'])

    pc_map = decode_pc_map(globals_raw['pc_map']) if globals_raw['pc_map'] else [None] * 128
    songs = decode_songs(globals_raw['song_frames']) if globals_raw['song_frames'] else []
    sets  = decode_sets(globals_raw['set_frames'])  if globals_raw['set_frames']  else []
    globals_decoded = decode_globals(globals_raw['global_state'], globals_raw['tail'])

    from .validation import is_complete_dump
    return {
        'complete': is_complete_dump(buf),
        'presets': presets,
        'channel_names': channel_names,
        'switch_names': switch_names,
        'pc_map': pc_map,
        'songs': songs,
        'sets':  sets,
        'globals': globals_decoded,
        'has_dump': True,
        'frame_count': len(frames),
        '_raw': {   # kept for round-trip writes
            'frames': [bytes(f) for f in frames],
            'preset_frame_indices': [i for i, f in enumerate(frames)
                                     if len(f) == PRESET_FRAME_LEN],
        },
    }

def decode_pc_slots(frame):
    """Decode the 16 per-channel PC slots from a preset frame.

    Returns a list of {ch, value, active} dicts (CH1..CH16).
    """
    slots = []
    for ch in range(PC_SLOTS_COUNT):
        off = PC_SLOTS_OFF + ch * PC_SLOT_STRIDE
        slots.append({
            'ch': ch + 1,
            'value': frame[off],
            'active': frame[off + 1] == 0x00,   # 0x00 = active, 0x01 = OFF
        })
    return slots


def decode_ia_bitmap(frame):
    """Decode the 15-bit IA on/off bitmap into a list of bools (SW1..SW15).

    Storage: 3 bytes at 0xC8 (SW1-5), 0xCA (SW6-10), 0xCC (SW11-15).
    Each byte holds 5 bits in REVERSED order — bit 4 of each byte = the
    lowest-numbered switch in that group.
    """
    bits = [False] * 15
    for group, off in enumerate(IA_BITMAP_OFFS):
        b = frame[off]
        for i in range(5):
            bits[group * 5 + i] = bool(b & (1 << (4 - i)))
    return bits


def encode_ia_bitmap(bits):
    """Inverse of decode_ia_bitmap. Takes a list of 15 bools (or None) and
    returns a 3-tuple of bytes for offsets 0xC8, 0xCA, 0xCC. None = False.
    """
    out = [0, 0, 0]
    for group in range(3):
        for i in range(5):
            if bits[group * 5 + i]:
                out[group] |= (1 << (4 - i))
    return tuple(out)


def decode_custom_midi(frame):
    """Decode the 5 custom MIDI command slots (8-byte stride at 0xCE).

    `channel` is returned 1-based (1-16) to match the PC-slot editor; the
    wire byte is 0-based. An unset slot is type NONE (0x11).
    """
    cmds = []
    for i in range(CMD_COUNT):
        base = CMD_REGION_OFF + i * CMD_STRIDE
        type_byte = frame[base]
        cmds.append({
            'idx': i + 1,
            'type_byte': type_byte,
            'type_label': CMD_TYPE_LABELS.get(type_byte, f'??(0x{type_byte:02X})'),
            'channel': (frame[base + 2] & 0x0F) + 1,
            'data1': frame[base + 4],
            'data2': frame[base + 6],
        })
    return cmds


PER_PR_TABLE_BASE = 0x40   # IA/PED slot table — offset = base + (17 - chanid) * 8
PER_PR_SLOT_STRIDE = 8
PER_PR_SLOT_COUNT = 17     # chan-id 1..15 = SW1..SW15, 16 = PED2, 17 = PED1

def decode_per_pr_slots(frame):
    """Decode the 17-slot per-preset IA/pedal override table (0x40..0xC7).

    Confirmed offline (2026-07-20, T7 session): field +0 = MIDI channel
    (0-based!), +2 = CC#, +4 = ON value, +6 = OFF value. Default/unconfigured
    state: channel=0 (CH1), CC# = the slot's own chan-id number, ON=127,
    OFF=0 — reproduced exactly by two independent hardware sessions
    (T2's original SW11+PED1 capture, re-attributed from PR1 not PR3; and a
    clean SW9+SW2 single-preset capture). chan-id 17 = PED1 (confirmed);
    16 = PED2 (by the same descending pattern, not yet independently tested).
    """
    slots = []
    for chanid in range(1, PER_PR_SLOT_COUNT + 1):
        base = PER_PR_TABLE_BASE + (17 - chanid) * PER_PR_SLOT_STRIDE
        label = f'SW{chanid}' if chanid <= 15 else ('PED2' if chanid == 16 else 'PED1')
        slots.append({
            'chanid': chanid, 'label': label,
            'channel': frame[base] + 1,   # report 1-based to match the rest of the UI
            'cc': frame[base + 2],
            'on': frame[base + 4],
            'off': frame[base + 6],
        })
    return slots


def decode_sysex_string(frame):
    """Decode the 30-byte SysEx string at 0xF8 (2-byte pair encoding)."""
    bytes_out = []
    for i in range(SYSX_COUNT):
        off = SYSX_OFF + i * SYSX_STRIDE
        bytes_out.append(None if frame[off + 1] else frame[off])
    return bytes_out


def parse_preset_frame(preset_num, frame):
    """Parse a single 309-byte preset frame into a structured dict."""
    return {
        'num': preset_num,
        'name': decode_ascii_pairs(frame, NAME_OFF, NAME_CHARS),
        'pc_slots': decode_pc_slots(frame),
        'ia_bits': decode_ia_bitmap(frame),
        'cmd_slots': decode_custom_midi(frame),
        'per_pr_slots': decode_per_pr_slots(frame),
        'sysex_bytes': decode_sysex_string(frame),
        'sysex_on': bool(frame[SYSX_ON_OFF_BYTE] & 0x01),   # confirmed via rev_T5
        'raw_hex': frame.hex(),
    }

def decode_name_block(f):
    """Frame 123 holds channel + switch names, 4 chars each, as ascii+00 pairs.

    Observed from rocktron.syx: 16 × 4-char channel names (CH1..CH16),
    then PED1, PED2 (4 chars each), then 15 × 4-char switch names
    (SW1..SW15). Layout needs device-specific confirmation but matches the
    CONTROL NUMBERS / CHANNELS spreadsheet.
    """
    # Payload starts at offset 6 (after F0 00 00 29 08 2B). Each 4-char slot
    # consumes 8 bytes (4 lo/hi pairs).
    off = 6
    SLOT = 4 * 2  # 8 bytes
    def slot_at(i):
        return decode_ascii_pairs(f, off + i*SLOT, 4)
    channel_names = [slot_at(i) for i in range(16)]
    # pedal names at indices 16, 17
    pedals = [slot_at(16), slot_at(17)]
    # Switch slots 18..32 are stored REVERSED on the device — slot 18 = SW15,
    # slot 32 = SW1. Confirmed against the user's MUSE rig where SW6..SW15
    # = VOX1, DIEZ, MARS, FUN, SW10, PHSD, COMP, FUZZ, TOPB, DLAY.
    switches = [slot_at(18 + (14 - i)) for i in range(15)]
    return channel_names, {'pedals': pedals, 'switches': switches}

def rebuild_preset_frame(orig_frame, *, name=None, pc_slots=None,
                         ia_bits=None, cmd_slots=None, sysex_bytes=None,
                         sysex_on=None):
    """Splice edits into a preset's original 309-byte frame (non-destructive).

    Each kwarg is optional — only the supplied edits are applied; everything
    else stays bit-for-bit identical to the original frame.
    """
    out = bytearray(orig_frame)

    if name is not None:
        nb = encode_ascii_pairs(name, NAME_CHARS)
        for i, b in enumerate(nb):
            out[NAME_OFF + i] = b

    if pc_slots is not None:
        for slot in pc_slots:
            ch = slot['ch']
            off = PC_SLOTS_OFF + (ch - 1) * PC_SLOT_STRIDE
            out[off] = max(0, min(127, int(slot['value']))) & 0x7F
            out[off + 1] = 0x00 if slot.get('active', True) else 0x01

    if ia_bits is not None:
        bytes3 = encode_ia_bitmap(ia_bits)
        for off, b in zip(IA_BITMAP_OFFS, bytes3):
            out[off] = b

    if cmd_slots is not None:
        # All 5 slots are writable: the 8-byte-stride region 0xCE..0xF5 is
        # fully inside the preset frame and touches neither the IA bitmap
        # (0xC8/0xCA/0xCC) nor the SysEx toggle (0xF6) / string (0xF8+).
        # The headless UAT asserts a CMD5 write leaves 0xF6 untouched.
        for cmd in cmd_slots:
            i = cmd['idx'] - 1
            if not 0 <= i < CMD_COUNT:
                continue
            base = CMD_REGION_OFF + i * CMD_STRIDE
            tb = cmd.get('type_byte')
            if tb is None and cmd.get('type_label'):
                tb = CMD_LABEL_TO_TYPE.get(cmd['type_label'])
            if tb is not None:
                out[base] = tb & 0x7F
            if 'channel' in cmd:
                # UI channel is 1-based; the wire byte is 0-based.
                out[base + 2] = max(0, min(15, int(cmd['channel']) - 1)) & 0x7F
            if 'data1' in cmd:
                out[base + 4] = max(0, min(127, int(cmd['data1']))) & 0x7F
            if 'data2' in cmd:
                out[base + 6] = max(0, min(127, int(cmd['data2']))) & 0x7F

    if sysex_bytes is not None:
        # Each sysex byte is stored as a 2-byte pair: (value, terminator).
        # Default/baseline pattern is `00 01 00 01 ...` — terminator = 0x01
        # tells the device the slot is empty/EOX. When the user writes a
        # value, we must also clear the terminator to 0x00 so the device
        # actually transmits that byte on preset recall.
        for i, b in enumerate(sysex_bytes[:SYSX_COUNT]):
            if b is None:
                if out[SYSX_OFF + i * SYSX_STRIDE + 1] == 0:
                    out[SYSX_OFF + i * SYSX_STRIDE + 1] = 0x01
                continue
            out[SYSX_OFF + i * SYSX_STRIDE]     = max(0, min(127, int(b))) & 0x7F
            out[SYSX_OFF + i * SYSX_STRIDE + 1] = 0x00

    if sysex_on is not None:
        out[SYSX_ON_OFF_BYTE] = 0x01 if sysex_on else 0x00

    return bytes(out)


# ── global-frame decoders ────────────────────────────────────────────────────
def decode_pc_map(frame):
    """Decode the PC-map table (frame size 263 B) — incoming PC1..PC128 → preset.

    Returns a list of 128 ints (preset index 0..119) or None for unmapped.
    """
    if not frame or len(frame) < 6 + 128 * 2:
        return [None] * 128
    out = []
    for i in range(128):
        off = 6 + i * 2
        val = frame[off]
        out.append(val if val < 120 else None)
    return out


SONG_SLOTS = 15   # SW1..SW15 — a song holds one bank's worth of presets, and
                  # max Bank Size is 15 (manual: Song Create assigns SW1-15).
                  # 30 B/song = 15 slots × 2 B, no overhead. Only the first
                  # Bank-Size slots are "active" on the device, but all 15 are
                  # stored, so all 15 are decoded/editable.

def decode_songs(song_frames):
    """Decode the 10 song frames (457 B each) into 150 songs.

    Each frame holds 15 songs at 30-byte stride. Each song has 15 preset
    slots (2 bytes each, value = preset_index = preset_num - 1).
    """
    songs = []
    for fi, frame in enumerate(song_frames):
        for s in range(15):
            base = 6 + s * 30
            slots = []
            for k in range(SONG_SLOTS):
                off = base + k * 2
                if off + 1 >= len(frame):
                    slots.append(None)
                    continue
                v = frame[off]
                slots.append(v if v < 120 else None)
            songs.append({'idx': fi * 15 + s + 1, 'slots': slots})
    return songs[:150]


def decode_sets(set_frames):
    """Decode the 10 set frames (107 B each) — each set assigns 50 banks → song IDs.

    Returns a list of 10 sets, each a list of 50 song indices (1..150) or None.
    """
    sets_out = []
    for fi, frame in enumerate(set_frames):
        slots = []
        for b in range(50):
            off = 6 + b * 2
            if off + 1 >= len(frame):
                slots.append(None)
                continue
            v = frame[off]
            slots.append(v + 1 if v < 150 else None)
        sets_out.append({'idx': fi + 1, 'banks': slots})
    return sets_out


def encode_pc_map(orig_frame, pc_map):
    """Splice a PC-map list (128 ints, value = preset_index 0..119, or None)
    back into the original 263-byte frame.
    """
    out = bytearray(orig_frame)
    for i, v in enumerate(pc_map[:128]):
        off = 6 + i * 2
        if v is None:
            # `OFF` = 120 (0x78). Confirmed offline: the baseline dump's 8
            # unmapped slots all store exactly 120 with high byte 0x00
            # (tools/analyze_captures.py, Hunt 2).
            if out[off] < 120:
                out[off] = 0x78
            continue
        out[off] = max(0, min(119, int(v))) & 0x7F
    return bytes(out)


def encode_song(orig_frame, song_pos, slots):
    """Splice a single song's 15 preset slots into a 457 B song frame.

    song_pos = 0..14 (which song within this frame)
    slots = list of up to 15 preset_indices (0..119) or None
    """
    out = bytearray(orig_frame)
    base = 6 + song_pos * 30
    for k, v in enumerate(slots[:SONG_SLOTS]):
        if v is None:
            continue
        out[base + k * 2] = max(0, min(119, int(v))) & 0x7F
    return bytes(out)


def encode_set(orig_frame, banks):
    """Splice 50 bank slots into a 107 B set frame. Each slot = song_index 0..149."""
    out = bytearray(orig_frame)
    for b, v in enumerate(banks[:50]):
        if v is None:
            continue
        out[6 + b * 2] = max(0, min(149, int(v))) & 0x7F
    return bytes(out)


SWITCH_TYPE_BASE = 0x46    # globals frame — offset(SW#) = base - (SW# - 1) * 2
SWITCH_TYPE_BYTES = {'LATCH': 0x00, 'MOMENTARY': 0x01, 'HOLD': 0x02}
SWITCH_TYPE_LABELS = {v: k for k, v in SWITCH_TYPE_BYTES.items()}

def _switch_type_offset(sw):
    return SWITCH_TYPE_BASE - (sw - 1) * 2


def encode_globals(global_state_frame, tail_frame, edits):
    """Splice supported globals; preserve unconfirmed mode/style bytes.

    T7 session reports locate bank size at tail 0x0C (1/5/10/15 -> 0/1/2/3)
    and switch types at state-frame 0x46 - 2*(SW-1). Old 0x44/0x46 bank
    fields were wrong and overwrote SW2/SW1. PC status uses tail 0x08;
    receive channel uses 0x12; only the lower remote-title range at 0x10
    is supported. Reports are not hardware write-acceptance verification.

    operating_mode and bank_style are intentionally ignored here for legacy
    callers. The HTTP API rejects them explicitly. Their real locations
    require controlled captures; no candidate bytes are modified.
    """
    BANK_SIZE_BYTES = {1: 0x00, 5: 0x01, 10: 0x02, 15: 0x03}
    PC_STATUS_BYTES = {'OFF': 0x00, 'ON': 0x01, 'MAP': 0x02}

    new_state = bytearray(global_state_frame) if global_state_frame else None
    new_tail  = bytearray(tail_frame)         if tail_frame         else None

    if new_state is not None and len(new_state) > 0x46:
        # bank_size / bank_style are intentionally NOT written here anymore —
        # see the CAUTION note above. bank_size is handled via the tail frame
        # below; bank_style has no known safe target.
        if 'switch_types' in edits:
            for sw_str, type_label in (edits['switch_types'] or {}).items():
                sw = int(sw_str)
                if 1 <= sw <= 15 and type_label in SWITCH_TYPE_BYTES:
                    new_state[_switch_type_offset(sw)] = SWITCH_TYPE_BYTES[type_label]

    if new_tail is not None and len(new_tail) > 0x12:
        if 'pc_status' in edits and edits['pc_status'] in PC_STATUS_BYTES:
            new_tail[0x08] = PC_STATUS_BYTES[edits['pc_status']]
        if 'bank_size' in edits and int(edits['bank_size']) in BANK_SIZE_BYTES:
            new_tail[0x0C] = BANK_SIZE_BYTES[int(edits['bank_size'])]
        if 'remote_title' in edits:
            new_tail[0x10] = max(0, min(127, int(edits['remote_title']))) & 0x7F
        if 'midi_rx_channel' in edits:
            v = edits['midi_rx_channel']
            if isinstance(v, str) and v.upper() == 'OMNI':
                new_tail[0x12] = 0x10
            else:
                new_tail[0x12] = max(0, min(15, int(v) - 1)) & 0x7F

    return (bytes(new_state) if new_state is not None else None,
            bytes(new_tail)  if new_tail  is not None else None)


def encode_name_block(orig_frame, channel_names, switch_names_dict):
    """Splice channel + switch + pedal names back into the 279 B name block.

    channel_names: list of 16 strings (4 chars each)
    switch_names_dict: {pedals: [2 strings], switches: [15 strings (SW1..SW15)]}
    Switches stored REVERSED in the device — SW1 lives in slot 32, SW15 in slot 18.
    """
    out = bytearray(orig_frame)
    SLOT_BYTES = 8   # 4 chars × 2-byte pair
    base = 6
    for i in range(16):
        nb = encode_ascii_pairs((channel_names[i] if i < len(channel_names) else '')[:4], 4)
        for k, b in enumerate(nb):
            out[base + i * SLOT_BYTES + k] = b
    pedals = (switch_names_dict or {}).get('pedals', []) or []
    for i in range(2):
        nb = encode_ascii_pairs((pedals[i] if i < len(pedals) else '')[:4], 4)
        for k, b in enumerate(nb):
            out[base + (16 + i) * SLOT_BYTES + k] = b
    switches = (switch_names_dict or {}).get('switches', []) or []
    for i in range(15):    # SW1..SW15
        slot_idx = 32 - i  # SW1 -> 32, SW2 -> 31, ... SW15 -> 18
        nb = encode_ascii_pairs((switches[i] if i < len(switches) else '')[:4], 4)
        for k, b in enumerate(nb):
            out[base + slot_idx * SLOT_BYTES + k] = b
    return bytes(out)


def decode_globals(global_state_frame, tail_frame):
    """Decode the editable globals from frames 122 + tail (frame 145).

    See encode_globals() for the full offset provenance (T7 session,
    2026-07-20). bank_style has no known location and always decodes None.
    """
    g = {
        'operating_mode': None, 'operating_mode_byte': None,
        'bank_size': None, 'bank_size_byte': None,
        'bank_style': None,   # location unknown — see encode_globals CAUTION
        'switch_types': None,
        'pc_status': None, 'pc_status_byte': None,
        'remote_title': None,
        'midi_rx_channel': None,
    }
    BANK_SIZE_LABELS = {v: k for k, v in {1: 0x00, 5: 0x01, 10: 0x02, 15: 0x03}.items()}
    OP_MODE_LABELS = {0x00: 'BANK', 0x01: 'SONG', 0x02: 'REMOTE'}
    PC_STATUS_LABELS = {0x00: 'OFF', 0x01: 'ON', 0x02: 'MAP'}

    if global_state_frame and len(global_state_frame) > 0x46:
        g['operating_mode_byte'] = global_state_frame[0x08]
        # Preserve evidence without claiming a resolved mode decode.
        g['operating_mode'] = None
        g['operating_mode_candidate'] = OP_MODE_LABELS.get(g['operating_mode_byte'])
        g['switch_types'] = {
            sw: SWITCH_TYPE_LABELS.get(global_state_frame[_switch_type_offset(sw)])
            for sw in range(1, 16)
        }

    if tail_frame and len(tail_frame) > 0x12:
        g['pc_status_byte']  = tail_frame[0x08]
        g['pc_status']       = PC_STATUS_LABELS.get(g['pc_status_byte'])
        g['bank_size_byte']  = tail_frame[0x0C]
        g['bank_size']       = BANK_SIZE_LABELS.get(g['bank_size_byte'])
        g['remote_title']    = tail_frame[0x10]
        rx = tail_frame[0x12]
        # 0..15 = channels 1..16; 16 (0x10) = OMNI (confirmed by rev_T6 — user inadvertently
        # advanced to OMNI when navigating MIDI P7 to confirm we're past the channel range).
        g['midi_rx_channel'] = 'OMNI' if rx == 16 else (rx + 1 if rx < 16 else None)

    return g
