"""
Rocktron All Access — Web Editor + Virtual Foot Controller

Run:  python app.py  →  http://localhost:5002
Mac:  pip3 install flask flask-socketio python-rtmidi
Win:  pip  install flask flask-socketio python-rtmidi

Protocol status (reverse-engineered — see docs/REVERSE_ENGINEERING.md):
  Manufacturer ID:  00 00 29 (Rocktron)
  Device ID:        08 (All Access)
  Bulk dump:        145 sysex frames per full dump
    • Frame 0         cmd 2A, 309 B  preset 1 (start-of-dump marker)
    • Frames 1..119   cmd 2B, 309 B  presets 2..120
    • Frames 120..122 cmd 2B, mixed  global tables (PC map, misc)
    • Frame 123       cmd 2B, 279 B  channel+switch NAME block
    • Frames 124..133 cmd 2B, 457 B  songs (15 songs × 30 B each, 10 blocks)
    • Frames 134..143 cmd 2B, 107 B  sets  (50 banks × 2 B each, 10 blocks)
    • Frame 144       cmd 2B, 23 B   end-of-dump marker + checksum-ish
  Every payload byte is the low 7-bit of an ASCII/value byte, followed by
  a 0x00 "high-bit" companion byte (standard 7-bit sysex packing).
"""
import json
import os
import platform
import threading
import time
from collections import defaultdict, deque
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory, Response
from flask_socketio import SocketIO, emit

try:
    import rtmidi
    HAS_RTMIDI = True
except ImportError:
    rtmidi = None
    HAS_RTMIDI = False

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

PC_SLOTS_OFF = 0x24              # 16 PC slots × 2 bytes (value, status)
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

# Type byte = index into the manual's CUSTOM P1 list (page order). Observed on
# hardware: N ON>=0x01, C CH>=0x03, NONE=0x11; the rest follow the list order
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

    channel_names, switch_names = [], []
    if globals_raw['name_block']:
        channel_names, switch_names = decode_name_block(globals_raw['name_block'])

    pc_map = decode_pc_map(globals_raw['pc_map']) if globals_raw['pc_map'] else [None] * 128
    songs = decode_songs(globals_raw['song_frames']) if globals_raw['song_frames'] else []
    sets  = decode_sets(globals_raw['set_frames'])  if globals_raw['set_frames']  else []
    globals_decoded = decode_globals(globals_raw['global_state'], globals_raw['tail'])

    return {
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


def decode_sysex_string(frame):
    """Decode the 30-byte SysEx string at 0xF8 (2-byte pair encoding)."""
    bytes_out = []
    for i in range(SYSX_COUNT):
        bytes_out.append(frame[SYSX_OFF + i * SYSX_STRIDE])
    return bytes_out


def parse_preset_frame(preset_num, frame):
    """Parse a single 309-byte preset frame into a structured dict."""
    return {
        'num': preset_num,
        'name': decode_ascii_pairs(frame, NAME_OFF, NAME_CHARS),
        'pc_slots': decode_pc_slots(frame),
        'ia_bits': decode_ia_bitmap(frame),
        'cmd_slots': decode_custom_midi(frame),
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


def decode_songs(song_frames):
    """Decode the 10 song frames (457 B each) into 150 songs.

    Each frame holds 15 songs at 30-byte stride. Each song has 10 preset
    slots (2 bytes each, value = preset_index = preset_num - 1).
    """
    songs = []
    for fi, frame in enumerate(song_frames):
        for s in range(15):
            base = 6 + s * 30
            slots = []
            for k in range(10):
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
            out[off] = 0x78
            continue
        out[off] = max(0, min(119, int(v))) & 0x7F
    return bytes(out)


def encode_song(orig_frame, song_pos, slots):
    """Splice a single song's 10 preset slots into a 457 B song frame.

    song_pos = 0..14 (which song within this frame)
    slots = list of up to 10 preset_indices (0..119) or None
    """
    out = bytearray(orig_frame)
    base = 6 + song_pos * 30
    for k, v in enumerate(slots[:10]):
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


def encode_globals(global_state_frame, tail_frame, edits):
    """Splice global edits into the 223 B state frame and 23 B tail frame.

    Byte offsets from rev_T5_final.syx:
      Frame 122 (global state, 223 B):
        0x08 — operating mode (0=BANK, 1=SONG, 2=REMOTE)
        0x44 — bank size       (5=0, 1=2; 10/15 still TBD — use best guesses)
        0x46 — bank style      (FIRST=0, CURNT=1, NONE=2)
      CAUTION: the 0x44/0x46 attribution is ambiguous — T5 changed bank size,
      bank style AND SW1/SW2 switch types in one session, and T6's SW3/SW4
      type edits landed at the adjacent 0x40/0x42 with type-shaped values.
      0x44/0x46 may therefore be switch-type cells, not bank size/style.
      Needs the T7 disambiguation capture (docs/REVERSE_ENGINEERING.md §T7)
      before bank-size write-back can be trusted.
      Frame 145 (tail, 23 B):
        0x08 — PC status       (OFF=0, MAP=2; ON=1 best guess)
        0x10 — remote title number (0-127)
        0x12 — MIDI receive channel - 1 (0-15)

    `edits` keys: operating_mode, bank_size, bank_style, remote_title,
                  midi_rx_channel, pc_status.
    """
    BANK_SIZE_BYTES = {5: 0x00, 1: 0x02, 10: 0x04, 15: 0x06}   # 10/15 still TBD
    BANK_STYLE_BYTES = {'FIRST': 0x00, 'CURNT': 0x01, 'NONE': 0x02}
    OP_MODE_BYTES = {'BANK': 0x00, 'SONG': 0x01, 'REMOTE': 0x02}
    PC_STATUS_BYTES = {'OFF': 0x00, 'ON': 0x01, 'MAP': 0x02}

    new_state = bytearray(global_state_frame) if global_state_frame else None
    new_tail  = bytearray(tail_frame)         if tail_frame         else None

    if new_state is not None and len(new_state) > 0x46:
        if 'operating_mode' in edits and edits['operating_mode'] in OP_MODE_BYTES:
            new_state[0x08] = OP_MODE_BYTES[edits['operating_mode']]
        if 'bank_size' in edits and int(edits['bank_size']) in BANK_SIZE_BYTES:
            new_state[0x44] = BANK_SIZE_BYTES[int(edits['bank_size'])]
        if 'bank_style' in edits and edits['bank_style'] in BANK_STYLE_BYTES:
            new_state[0x46] = BANK_STYLE_BYTES[edits['bank_style']]

    if new_tail is not None and len(new_tail) > 0x12:
        if 'pc_status' in edits and edits['pc_status'] in PC_STATUS_BYTES:
            new_tail[0x08] = PC_STATUS_BYTES[edits['pc_status']]
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

    Byte offsets confirmed via rev_T5_final.syx; encoding for some enum
    values still partial — listed in encode_globals().
    """
    g = {
        'operating_mode': None, 'operating_mode_byte': None,
        'bank_size': None, 'bank_size_byte': None,
        'bank_style': None, 'bank_style_byte': None,
        'pc_status': None, 'pc_status_byte': None,
        'remote_title': None,
        'midi_rx_channel': None,
    }
    BANK_SIZE_LABELS = {0x00: 5, 0x02: 1, 0x04: 10, 0x06: 15}   # 10/15 best guess
    BANK_STYLE_LABELS = {0x00: 'FIRST', 0x01: 'CURNT', 0x02: 'NONE'}
    OP_MODE_LABELS = {0x00: 'BANK', 0x01: 'SONG', 0x02: 'REMOTE'}
    PC_STATUS_LABELS = {0x00: 'OFF', 0x01: 'ON', 0x02: 'MAP'}

    if global_state_frame and len(global_state_frame) > 0x46:
        g['operating_mode_byte'] = global_state_frame[0x08]
        g['operating_mode']      = OP_MODE_LABELS.get(g['operating_mode_byte'])
        g['bank_size_byte']      = global_state_frame[0x44]
        g['bank_size']           = BANK_SIZE_LABELS.get(g['bank_size_byte'])
        g['bank_style_byte']     = global_state_frame[0x46]
        g['bank_style']          = BANK_STYLE_LABELS.get(g['bank_style_byte'])

    if tail_frame and len(tail_frame) > 0x12:
        g['pc_status_byte']  = tail_frame[0x08]
        g['pc_status']       = PC_STATUS_LABELS.get(g['pc_status_byte'])
        g['remote_title']    = tail_frame[0x10]
        rx = tail_frame[0x12]
        # 0..15 = channels 1..16; 16 (0x10) = OMNI (confirmed by rev_T6 — user inadvertently
        # advanced to OMNI when navigating MIDI P7 to confirm we're past the channel range).
        g['midi_rx_channel'] = 'OMNI' if rx >= 16 else (rx + 1)

    return g

# ── MIDI I/O wrapper ─────────────────────────────────────────────────────────
class MidiBridge:
    """Thin wrapper around python-rtmidi that emits socket.io events."""

    def __init__(self, socketio):
        self.sio = socketio
        self.in_port = self.out_port = None
        self.in_name = self.out_name = None
        self._sysex_buf = bytearray()
        self._in_sysex = False
        self._monitor = deque(maxlen=500)
        # rtmidi can fail to construct on headless boxes (no ALSA), busy
        # CoreMIDI servers, or fresh installs without build tools. Treat any
        # failure the same as `not HAS_RTMIDI` so the editor still loads for
        # file-only work — exactly what HAS_RTMIDI was meant to enable.
        if HAS_RTMIDI:
            try:
                self.in_port  = rtmidi.MidiIn()
                self.out_port = rtmidi.MidiOut()
                self.in_port.ignore_types(sysex=False, timing=True, active_sense=True)
                self.in_port.set_callback(self._on_midi_in)
            except Exception as e:
                print(f"[MIDI] init failed ({e}); running file-only.", flush=True)
                self.in_port = self.out_port = None

    def list_ports(self):
        if not self.in_port or not self.out_port:
            return {'in': [], 'out': [], 'error': 'MIDI unavailable (file-only mode)'}
        return {
            'in':  self.in_port.get_ports(),
            'out': self.out_port.get_ports(),
            'selected_in':  self.in_name,
            'selected_out': self.out_name,
        }

    def open_in(self, name):
        if not HAS_RTMIDI or self.in_port is None: return False
        try:
            self.in_port.close_port()
        except Exception:
            pass
        # Recreate the input so a fresh callback registration sticks. Some
        # python-rtmidi backends drop the callback when a port is closed.
        self.in_port = rtmidi.MidiIn()
        self.in_port.ignore_types(sysex=False, timing=True, active_sense=True)
        self.in_port.set_callback(self._on_midi_in)
        ports = self.in_port.get_ports()
        if name in ports:
            self.in_port.open_port(ports.index(name))
        else:
            self.in_port.open_virtual_port(name)
        self.in_name = name
        # Diagnostic — confirm the in-port is live.
        self._log_event('info', f'IN open: {name}'.encode())
        return True

    def open_out(self, name):
        if not HAS_RTMIDI or self.out_port is None: return False
        try:
            self.out_port.close_port()
        except Exception:
            pass
        ports = self.out_port.get_ports()
        if name in ports:
            self.out_port.open_port(ports.index(name))
        else:
            self.out_port.open_virtual_port(name)
        self.out_name = name
        return True

    def send(self, msg_bytes):
        if not HAS_RTMIDI or self.out_port is None or not self.out_name:
            return False
        try:
            self.out_port.send_message(list(msg_bytes))
            self._log_event('tx', msg_bytes)
            return True
        except Exception as e:
            self._log_event('err', str(e).encode())
            return False

    def send_throttled(self, msg_bytes, chunk_size=8, byte_delay_s=0.018):
        """Send a (potentially long) sysex frame to the All Access in small
        chunks with explicit byte-rate pacing.

        The All Access manual states the unit can receive at most one byte
        every ~15 ms (~65 Hz). A single rtmidi `send_message` call dumps the
        whole frame to USB at line speed, which can overflow the device's
        small input buffer even if we sleep between frames. We therefore
        split each frame into `chunk_size`-byte pieces and wait
        `chunk_size * byte_delay_s` between pieces. With the defaults
        (8 bytes / 18 ms-per-byte), worst-case dump time is ~13 minutes for
        a full 43 kB bulk dump but it never overflows.

        We use 18 ms per byte rather than 15 ms to leave a safety margin —
        some USB-MIDI interfaces add their own jitter on top.
        """
        if not HAS_RTMIDI or self.out_port is None or not self.out_name:
            return False
        msg = list(msg_bytes)
        try:
            for i in range(0, len(msg), chunk_size):
                chunk = msg[i:i + chunk_size]
                self.out_port.send_message(chunk)
                time.sleep(len(chunk) * byte_delay_s)
            self._log_event('tx', bytes(msg))
            return True
        except Exception as e:
            self._log_event('err', str(e).encode())
            return False

    def _on_midi_in(self, event, _data=None):
        msg, _delta = event
        # accumulate sysex
        if msg and msg[0] == 0xF0:
            self._in_sysex = True
            self._sysex_buf = bytearray(msg)
        elif self._in_sysex:
            self._sysex_buf.extend(msg)
        else:
            self._log_event('rx', bytes(msg))
            return
        if self._sysex_buf and self._sysex_buf[-1] == 0xF7:
            payload = bytes(self._sysex_buf)
            self._in_sysex = False
            self._sysex_buf = bytearray()
            self._log_event('rx-sysex', payload)
            self.sio.emit('sysex-recv', {'hex': payload.hex(),
                                         'len': len(payload)})
            # Forward to capture-mode aggregator if active
            cb = STATE.get('capture_cb')
            if cb:
                cb(payload)

    def _log_event(self, kind, data):
        entry = {
            't': time.time(),
            'kind': kind,
            'hex': data.hex() if isinstance(data, (bytes, bytearray)) else data,
            'len': len(data) if isinstance(data, (bytes, bytearray)) else 0,
        }
        self._monitor.append(entry)
        self.sio.emit('monitor', entry)

    def recent_monitor(self, n=200):
        return list(self._monitor)[-n:]

# ── app state ────────────────────────────────────────────────────────────────
STATE = {
    'dump': None,          # parsed dump dict
    'dump_bytes': None,    # raw bytes for writeback
    'loaded_from': None,
    'global_config': {     # seeded from manual defaults + MUSE spreadsheet
        'bank_mode': 'BANK',
        'bank_size': 5,
        'bank_style': 'FIRST',
        'midi_rx_channel': 1,
        'preset_start': 1,
    },
    'virtual_fc': {
        'bank': 0,
        'sw_state': [False]*15,
        'ped1': 0,
        'ped2': 0,
    },
    'capture_cb': None,        # callback while capture mode is active
    'capture_frames': [],      # accumulated raw sysex frames during capture
    'capture_target': None,    # 'all' | 'preset:N'
}
STATE_LOCK = threading.Lock()

# ── Flask + SocketIO ─────────────────────────────────────────────────────────
app = Flask(__name__, static_folder='static', template_folder='templates')
_AA_PORT = int(os.environ.get('PORT', 5002))
sio = SocketIO(app, async_mode='threading',
               cors_allowed_origins=[f'http://localhost:{_AA_PORT}',
                                     f'http://127.0.0.1:{_AA_PORT}'])
MIDI = MidiBridge(sio)

# Hardening: random per-launch SECRET_KEY, Origin+CSRF guard, and dump
# validation. See security_patch.py.
from security_patch import install_security
install_security(app, sio, port=_AA_PORT)

@app.route('/')
def index():
    resp = send_from_directory('templates', 'index.html')
    # Always serve fresh — the UI iterates frequently and we don't want
    # the browser to hold onto stale JS/SVG between development reloads.
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    resp.headers['Pragma']  = 'no-cache'
    resp.headers['Expires'] = '0'
    return resp

@app.route('/static/<path:p>')
def static_file(p):
    return send_from_directory('static', p)

@app.route('/api/midi/ports')
def midi_ports():
    return jsonify(MIDI.list_ports())

@app.route('/api/midi/open', methods=['POST'])
def midi_open():
    d = request.get_json(force=True)
    in_ok  = MIDI.open_in(d['in_port'])   if d.get('in_port')  else True
    out_ok = MIDI.open_out(d['out_port']) if d.get('out_port') else True
    return jsonify({'in_ok': in_ok, 'out_ok': out_ok, 'ports': MIDI.list_ports()})

@app.route('/api/dump/load', methods=['POST'])
def dump_load():
    """Load a .syx bulk dump uploaded from the browser file picker."""
    from security_patch import validate_dump, FrameValidationError
    d = request.get_json(force=True)
    if not d.get('hex'):
        return jsonify({'ok': False, 'error': 'need hex payload'}), 400
    hex_str = d['hex']
    # Real dump is ~87 KB of hex; anything wildly larger is either a
    # mistake or an attempt to exhaust memory. Cap at 1 MB hex.
    if len(hex_str) > 1_000_000:
        return jsonify({'ok': False, 'error': 'hex payload too large'}), 413
    buf = bytes.fromhex(hex_str)
    src = d.get('filename', 'upload')

    # Soft validation on load: a partial capture is still inspectable, but
    # the response carries `complete=false` so the UI can block Write All.
    try:
        validate_dump(buf, strict=False)
    except FrameValidationError as e:
        return jsonify({'ok': False, 'error': str(e)}), 400
    try:
        validate_dump(buf, strict=True)
        complete = True
    except FrameValidationError:
        complete = False

    parsed = parse_dump(buf)
    parsed['complete'] = complete
    with STATE_LOCK:
        STATE['dump'] = parsed
        STATE['dump_bytes'] = buf
        STATE['loaded_from'] = src
    return jsonify(_dump_payload(parsed, src))


def _dump_payload(parsed, src):
    """Slim JSON-safe view of a parsed dump for client responses."""
    return {
        'ok': True,
        'loaded_from': src,
        'presets': parsed['presets'],
        'channel_names': parsed['channel_names'],
        'switch_names': parsed['switch_names'],
        'pc_map': parsed['pc_map'],
        'songs': parsed['songs'],
        'sets':  parsed['sets'],
        'globals': parsed['globals'],
        'frame_count': parsed['frame_count'],
        'complete': parsed.get('complete', True),
        'has_dump': True,
    }


@app.route('/api/dump/status')
def dump_status():
    with STATE_LOCK:
        d = STATE['dump']
        src = STATE.get('loaded_from')
    if not d:
        return jsonify({'has_dump': False})
    return jsonify(_dump_payload(d, src))

@app.route('/api/dump/save')
def dump_save():
    """Stream the current in-memory dump back as a .syx download.

    Filename is derived from a timestamp so successive saves don't
    overwrite each other. Browser will Save As… into the user's
    Downloads folder.
    """
    with STATE_LOCK:
        buf = STATE.get('dump_bytes')
    if not buf:
        return jsonify({'ok': False, 'error': 'no dump loaded'}), 400
    ts = time.strftime('%Y%m%d-%H%M%S')
    fname = f'rocktron-{ts}.syx'
    return Response(
        bytes(buf),
        mimetype='application/octet-stream',
        headers={'Content-Disposition': f'attachment; filename="{fname}"'},
    )

@app.route('/api/dump/capture', methods=['POST'])
def dump_capture():
    """Enter capture mode — collect incoming sysex frames into a buffer.

    The All Access manual documents no remote dump-request message, so the
    user must initiate the dump from the device (SYSX page 1 → DUMP). This
    endpoint primes the backend to accumulate frames as they arrive and
    auto-parse once the end-of-dump marker (23-byte tail) lands.
    """
    target = (request.get_json(silent=True) or {}).get('target', 'all')

    with STATE_LOCK:
        STATE['capture_frames'] = []
        STATE['capture_target'] = target

    def on_frame(payload):
        # Only count Rocktron All Access frames
        if not payload.startswith(SYX_HEADER):
            return
        with STATE_LOCK:
            frames = STATE['capture_frames']
            frames.append(payload)
            count = len(frames)
        sio.emit('capture-progress', {'count': count, 'last_len': len(payload)})
        # End-of-dump tail = 23-byte frame ending with the …F7 marker
        if len(payload) == 23:
            _finalize_capture()

    with STATE_LOCK:
        STATE['capture_cb'] = on_frame
    return jsonify({'ok': True, 'target': target,
                    'message': 'Capture armed. Initiate DUMP on the device '
                               '(SYSX page 1 → DUMP, then CTR STORE).'})

@app.route('/api/dump/capture/cancel', methods=['POST'])
def dump_capture_cancel():
    with STATE_LOCK:
        STATE['capture_cb'] = None
        STATE['capture_frames'] = []
        STATE['capture_target'] = None
    return jsonify({'ok': True})

def _finalize_capture():
    with STATE_LOCK:
        frames = STATE['capture_frames']
        target = STATE['capture_target']
        STATE['capture_cb'] = None
        STATE['capture_frames'] = []
        STATE['capture_target'] = None
    if not frames:
        return
    buf = b''.join(frames)
    parsed = parse_dump(buf)
    with STATE_LOCK:
        STATE['dump'] = parsed
        STATE['dump_bytes'] = buf
        STATE['loaded_from'] = f'capture ({len(frames)} frames)'
    sio.emit('capture-done', {
        'frame_count': parsed['frame_count'],
        'preset_count': len(parsed['presets']),
        'target': target,
    })

@app.route('/api/dump/send', methods=['POST'])
def dump_send():
    """Send the entire dump (Write All) — device must be on SYSX page 1 → LOAD.

    Throttled to the manual's 65 Hz / 15 ms-per-byte limit to avoid the
    "Buffer Overflow" error.
    """
    with STATE_LOCK:
        buf = STATE['dump_bytes']
    if not buf:
        return jsonify({'ok': False, 'error': 'no dump loaded'}), 400
    if not MIDI.out_name:
        return jsonify({'ok': False, 'error': 'MIDI out not selected'}), 400
    # Never stream a partial/corrupt capture to the hardware — an interrupted
    # Read All parses fine but is missing frames, and writing it would brick
    # presets. validate_dump(strict=True) enforces the exact 145-frame multiset.
    from security_patch import validate_dump, FrameValidationError
    try:
        validate_dump(buf, strict=True)
    except FrameValidationError as e:
        return jsonify({'ok': False,
                        'error': f'refusing to write incomplete dump: {e}'}), 400

    # Pacing is HARD-LOCKED to the manual's literal "1 byte every 15 ms"
    # spec. These values are confirmed by real-hardware testing — anything
    # else (larger chunks, byte delays >15 ms, or any inter-frame pause)
    # caused the device to commit early and ignore subsequent frames,
    # corrupting the rig. Do not loosen these without empirical proof on
    # your hardware that the new values are safe end-to-end (UAT pass).
    chunk_size = 1
    byte_delay = 0.015
    inter_frame = 0.0

    def run():
        frames = split_frames(buf)
        total_frames = len(frames)
        stream = bytearray()
        for off, n in frames:
            stream.extend(buf[off:off+n])
        total_bytes = len(stream)

        t0 = time.time()
        sent_bytes = 0
        last_progress = 0
        last_log = 0
        last_logged_frames = 0
        # Server-side log so we can SEE write progress even if the browser
        # tab loses focus. Lines go to the server's stdout (the terminal
        # that launched run.sh / run.bat).
        print(f'[WRITE_ALL] start: {total_bytes} bytes, {total_frames} frames, '
              f'chunk={chunk_size}, byte_delay={byte_delay*1000:.0f}ms', flush=True)
        # Track non-daemon so the OS won't kill it if main thread reloads
        for i in range(0, total_bytes, chunk_size):
            chunk = bytes(stream[i:i + chunk_size])
            try:
                MIDI.out_port.send_message(list(chunk))
            except Exception as e:
                print(f'[WRITE_ALL] ERROR at byte {sent_bytes}/{total_bytes}: {e}', flush=True)
                try:
                    sio.emit('dump-progress',
                             {'sent': sent_bytes, 'total': total_bytes,
                              'error': str(e)})
                except Exception:
                    pass
                return
            time.sleep(len(chunk) * byte_delay)
            sent_bytes += len(chunk)
            if inter_frame > 0 and sent_bytes < total_bytes and \
               stream[sent_bytes - 1] == 0xF7:
                time.sleep(inter_frame)
            # Server-side log every 10 frames so we always have ground truth
            now = time.time()
            frames_sent = stream[:sent_bytes].count(0xF7)
            if frames_sent >= last_logged_frames + 10 or sent_bytes == total_bytes:
                last_logged_frames = frames_sent
                pct = round(sent_bytes * 100 / total_bytes, 1)
                print(f'[WRITE_ALL] {frames_sent}/{total_frames} frames, '
                      f'{sent_bytes}/{total_bytes} bytes ({pct}%), '
                      f'{round(now - t0, 1)}s elapsed', flush=True)
            # Throttle browser progress emit to ~10 Hz; protect against socket drops
            if now - last_progress > 0.1 or sent_bytes == total_bytes:
                last_progress = now
                try:
                    sio.emit('dump-progress',
                             {'sent': frames_sent,
                              'total': total_frames,
                              'bytes_sent': sent_bytes,
                              'bytes_total': total_bytes,
                              'elapsed_s': round(now - t0, 1)})
                except Exception:
                    pass    # browser disconnected — keep transmitting anyway
        elapsed = round(time.time() - t0, 1)
        print(f'[WRITE_ALL] DONE: {total_frames}/{total_frames} frames in {elapsed}s', flush=True)
        try:
            sio.emit('dump-progress',
                     {'sent': total_frames, 'total': total_frames, 'done': True,
                      'bytes_sent': total_bytes, 'bytes_total': total_bytes,
                      'elapsed_s': elapsed})
        except Exception:
            pass

    # Use non-daemon thread so it survives if main thread restarts
    t = threading.Thread(target=run, daemon=False)
    t.start()
    return jsonify({'ok': True, 'frames': len(split_frames(buf)),
                    'pacing': {'chunk_size': chunk_size,
                               'byte_delay_s': byte_delay,
                               'inter_frame_s': inter_frame}})


@app.route('/api/writeback-pacing', methods=['GET', 'POST'])
def writeback_pacing():
    """Read-only view of the locked-in Write All pacing.

    Pacing is hard-coded in `dump_send` after the rig-corruption incident
    of 2026-04-27 — POSTs are accepted (for backwards compat with older
    UI bundles) but ignored.
    """
    locked = {'chunk_size': 1, 'byte_delay_s': 0.015,
              'inter_frame_s': 0.0, 'locked': True,
              'note': 'Pacing is hard-locked to the manual\'s 1-byte-per-15ms spec.'}
    return jsonify(locked)

@app.route('/api/preset/<int:num>/send', methods=['POST'])
def preset_send(num):
    """Send a single preset frame (Write Preset).

    Useful after editing one preset's name without re-sending the whole dump.
    Note: the manual doesn't confirm whether the device commits a single
    preset frame received outside a bulk-dump context. May require the
    device to be on SYSX page 1 → LOAD.
    """
    with STATE_LOCK:
        buf = STATE['dump_bytes']
        dump = STATE['dump']
    if not buf or not dump:
        return jsonify({'ok': False, 'error': 'no dump loaded'}), 400
    if not (1 <= num <= len(dump['presets'])):
        return jsonify({'ok': False, 'error': 'preset out of range'}), 400
    if not MIDI.out_name:
        return jsonify({'ok': False, 'error': 'MIDI out not selected'}), 400

    frames = split_frames(buf)
    preset_idx = [i for i, (_, n) in enumerate(frames)
                  if n == PRESET_FRAME_LEN][num - 1]
    off, n = frames[preset_idx]
    MIDI.send(buf[off:off+n])
    return jsonify({'ok': True, 'bytes': n})

def _splice_preset(num, **edits):
    """Apply field edits to preset `num` and re-parse the dump.

    `edits` are forwarded as kwargs to rebuild_preset_frame (name,
    pc_slots, ia_bits, cmd_slots, sysex_bytes). Returns the updated
    preset dict, or raises ValueError on bad state.
    """
    with STATE_LOCK:
        dump = STATE['dump']
        buf  = STATE['dump_bytes']
    if not dump or not buf:
        raise ValueError('no dump loaded')
    if not (1 <= num <= len(dump['presets'])):
        raise ValueError('preset out of range')

    frames = split_frames(buf)
    preset_frame_indices = [i for i, (_, n) in enumerate(frames)
                            if n == PRESET_FRAME_LEN]
    fi = preset_frame_indices[num - 1]
    off, n = frames[fi]
    orig = buf[off:off+n]
    new  = rebuild_preset_frame(orig, **edits)
    new_buf = bytearray(buf)
    new_buf[off:off+n] = new

    parsed = parse_dump(bytes(new_buf))
    with STATE_LOCK:
        STATE['dump_bytes'] = bytes(new_buf)
        STATE['dump'] = parsed
    return parsed['presets'][num - 1]


@app.route('/api/preset/<int:num>/rename', methods=['POST'])
def preset_rename(num):
    d = request.get_json(force=True)
    new_name = (d.get('name') or '').upper()[:NAME_CHARS]
    try:
        preset = _splice_preset(num, name=new_name)
    except ValueError as e:
        return jsonify({'ok': False, 'error': str(e)}), 400
    return jsonify({'ok': True, 'preset': preset})


@app.route('/api/preset/<int:num>/pc', methods=['POST'])
def preset_pc(num):
    """Update one or more PC slots on a preset.

    Body: {"slots": [{"ch":1, "value":12, "active":true}, ...]}
    """
    d = request.get_json(force=True)
    slots = d.get('slots') or []
    try:
        preset = _splice_preset(num, pc_slots=slots)
    except ValueError as e:
        return jsonify({'ok': False, 'error': str(e)}), 400
    return jsonify({'ok': True, 'preset': preset})


@app.route('/api/preset/<int:num>/ia', methods=['POST'])
def preset_ia(num):
    """Update the IA on/off bitmap for a preset.

    Body: {"bits": [false,...false (15 bools, SW1..SW15)]}
    """
    d = request.get_json(force=True)
    bits = d.get('bits') or [False] * 15
    while len(bits) < 15:
        bits.append(False)
    try:
        preset = _splice_preset(num, ia_bits=bits)
    except ValueError as e:
        return jsonify({'ok': False, 'error': str(e)}), 400
    return jsonify({'ok': True, 'preset': preset})


@app.route('/api/preset/<int:num>/cmd', methods=['POST'])
def preset_cmd(num):
    """Update one or more custom MIDI command slots.

    Body: {"cmds": [{"idx":1, "type_label":"N ON>", "channel":1, "data1":60, "data2":100}, ...]}
    """
    d = request.get_json(force=True)
    cmds = d.get('cmds') or []
    try:
        preset = _splice_preset(num, cmd_slots=cmds)
    except ValueError as e:
        return jsonify({'ok': False, 'error': str(e)}), 400
    return jsonify({'ok': True, 'preset': preset})


def _splice_frame_by_match(matcher, new_bytes):
    """Find the first frame in the dump satisfying matcher(frame_bytes) and
    replace it with new_bytes. Re-parses + persists. Returns the updated parsed dump.
    """
    with STATE_LOCK:
        buf = STATE['dump_bytes']
    if not buf:
        raise ValueError('no dump loaded')
    frames = split_frames(buf)
    target = None
    for i, (off, n) in enumerate(frames):
        if matcher(buf[off:off+n]):
            target = (i, off, n)
            break
    if target is None:
        raise ValueError('matching frame not found')
    _, off, n = target
    if len(new_bytes) != n:
        raise ValueError(f'frame length mismatch: got {len(new_bytes)}, expected {n}')
    new_buf = bytearray(buf)
    new_buf[off:off+n] = new_bytes
    parsed = parse_dump(bytes(new_buf))
    with STATE_LOCK:
        STATE['dump_bytes'] = bytes(new_buf)
        STATE['dump'] = parsed
    return parsed


def _splice_song_frame(frame_index_in_songs, new_bytes):
    """Same as above but for the Nth (0-indexed) song frame (size 457)."""
    with STATE_LOCK:
        buf = STATE['dump_bytes']
    if not buf:
        raise ValueError('no dump loaded')
    frames = split_frames(buf)
    song_indices = [i for i, (_, n) in enumerate(frames) if n == SONG_FRAME_LEN]
    if frame_index_in_songs >= len(song_indices):
        raise ValueError('song frame index out of range')
    fi = song_indices[frame_index_in_songs]
    off, n = frames[fi]
    new_buf = bytearray(buf)
    new_buf[off:off+n] = new_bytes
    parsed = parse_dump(bytes(new_buf))
    with STATE_LOCK:
        STATE['dump_bytes'] = bytes(new_buf)
        STATE['dump'] = parsed
    return parsed


def _splice_set_frame(set_idx, new_bytes):
    """Splice the Nth set frame (107 B). set_idx is 0-indexed."""
    with STATE_LOCK:
        buf = STATE['dump_bytes']
    if not buf:
        raise ValueError('no dump loaded')
    frames = split_frames(buf)
    set_indices = [i for i, (_, n) in enumerate(frames) if n == SET_FRAME_LEN]
    if set_idx >= len(set_indices):
        raise ValueError('set frame index out of range')
    fi = set_indices[set_idx]
    off, n = frames[fi]
    new_buf = bytearray(buf)
    new_buf[off:off+n] = new_bytes
    parsed = parse_dump(bytes(new_buf))
    with STATE_LOCK:
        STATE['dump_bytes'] = bytes(new_buf)
        STATE['dump'] = parsed
    return parsed


@app.route('/api/song/<int:num>', methods=['POST'])
def song_save(num):
    """Update one song's preset slot list.

    Body: {"slots": [preset_num or null, ...]}   (up to 10 slots)
    """
    if not (1 <= num <= 150):
        return jsonify({'ok': False, 'error': 'song out of range'}), 400
    d = request.get_json(force=True)
    slots = d.get('slots') or []
    indices = [None if v is None else max(0, min(119, int(v) - 1)) for v in slots]

    frame_index = (num - 1) // 15
    song_pos    = (num - 1) % 15

    with STATE_LOCK:
        buf = STATE['dump_bytes']
    if not buf:
        return jsonify({'ok': False, 'error': 'no dump loaded'}), 400
    frames = split_frames(buf)
    song_frame_indices = [i for i, (_, n) in enumerate(frames) if n == SONG_FRAME_LEN]
    fi = song_frame_indices[frame_index]
    off, n = frames[fi]
    orig = buf[off:off+n]
    new = encode_song(orig, song_pos, indices)
    parsed = _splice_song_frame(frame_index, new)
    return jsonify({'ok': True, 'song': parsed['songs'][num - 1]})


@app.route('/api/set/<int:num>', methods=['POST'])
def set_save(num):
    """Update one set's 50 bank slots.

    Body: {"banks": [song_num (1..150) or null, ...]}
    """
    if not (1 <= num <= 10):
        return jsonify({'ok': False, 'error': 'set out of range'}), 400
    d = request.get_json(force=True)
    banks = d.get('banks') or []
    indices = [None if v is None else max(0, min(149, int(v) - 1)) for v in banks]

    with STATE_LOCK:
        buf = STATE['dump_bytes']
    if not buf:
        return jsonify({'ok': False, 'error': 'no dump loaded'}), 400
    frames = split_frames(buf)
    set_frame_indices = [i for i, (_, n) in enumerate(frames) if n == SET_FRAME_LEN]
    fi = set_frame_indices[num - 1]
    off, n = frames[fi]
    orig = buf[off:off+n]
    new = encode_set(orig, indices)
    parsed = _splice_set_frame(num - 1, new)
    return jsonify({'ok': True, 'set': parsed['sets'][num - 1]})


@app.route('/api/pcmap', methods=['POST'])
def pcmap_save():
    """Update the PC-map table (incoming PC1..128 → preset).

    Body: {"map": [preset_num (1..120) or null, ...] (up to 128)}
    """
    d = request.get_json(force=True)
    m = d.get('map') or []
    indices = [None if v is None else max(0, min(119, int(v) - 1)) for v in m]
    while len(indices) < 128:
        indices.append(None)

    with STATE_LOCK:
        buf = STATE['dump_bytes']
    if not buf:
        return jsonify({'ok': False, 'error': 'no dump loaded'}), 400
    frames = split_frames(buf)
    pcmap_idx = next((i for i, (_, n) in enumerate(frames) if n == PC_MAP_FRAME_LEN), None)
    if pcmap_idx is None:
        return jsonify({'ok': False, 'error': 'no PC-map frame in dump'}), 400
    off, n = frames[pcmap_idx]
    orig = buf[off:off+n]
    new = encode_pc_map(orig, indices)
    new_buf = bytearray(buf); new_buf[off:off+n] = new
    parsed = parse_dump(bytes(new_buf))
    with STATE_LOCK:
        STATE['dump_bytes'] = bytes(new_buf)
        STATE['dump'] = parsed
    return jsonify({'ok': True, 'pc_map': parsed['pc_map']})


@app.route('/api/dump-globals', methods=['POST'])
def dump_globals_save():
    """Splice global edits into the dump's 223 B state frame and 23 B tail.

    Body: {"operating_mode":..., "bank_size":..., "bank_style":...,
           "remote_title":..., "midi_rx_channel":...}
    """
    d = request.get_json(force=True)
    with STATE_LOCK:
        buf = STATE['dump_bytes']
    if not buf:
        return jsonify({'ok': False, 'error': 'no dump loaded'}), 400
    frames = split_frames(buf)

    state_idx = next((i for i, (_, n) in enumerate(frames) if n == GLOBAL_STATE_FRAME_LEN), None)
    tail_idx  = next((i for i, (_, n) in enumerate(frames) if n == TAIL_FRAME_LEN), None)
    if state_idx is None and tail_idx is None:
        return jsonify({'ok': False, 'error': 'global frames not found'}), 400

    state_orig = buf[frames[state_idx][0]:frames[state_idx][0] + frames[state_idx][1]] if state_idx is not None else None
    tail_orig  = buf[frames[tail_idx][0]:frames[tail_idx][0] + frames[tail_idx][1]]   if tail_idx  is not None else None
    new_state, new_tail = encode_globals(state_orig, tail_orig, d)

    new_buf = bytearray(buf)
    if new_state is not None:
        off, n = frames[state_idx]
        new_buf[off:off+n] = new_state
    if new_tail is not None:
        off, n = frames[tail_idx]
        new_buf[off:off+n] = new_tail
    parsed = parse_dump(bytes(new_buf))
    with STATE_LOCK:
        STATE['dump_bytes'] = bytes(new_buf)
        STATE['dump'] = parsed
    return jsonify({'ok': True, 'globals': parsed['globals']})


@app.route('/api/names', methods=['POST'])
def names_save():
    """Splice channel + switch + pedal names back into the 279 B name block.

    Body: {"channel_names": [16 strings], "switch_names": {pedals:[2], switches:[15]}}
    """
    d = request.get_json(force=True)
    with STATE_LOCK:
        buf = STATE['dump_bytes']
    if not buf:
        return jsonify({'ok': False, 'error': 'no dump loaded'}), 400
    frames = split_frames(buf)
    name_idx = next((i for i, (_, n) in enumerate(frames) if n == NAME_BLOCK_LEN), None)
    if name_idx is None:
        return jsonify({'ok': False, 'error': 'no name block in dump'}), 400
    off, n = frames[name_idx]
    orig = buf[off:off+n]
    new = encode_name_block(orig, d.get('channel_names') or [],
                            d.get('switch_names') or {})
    new_buf = bytearray(buf); new_buf[off:off+n] = new
    parsed = parse_dump(bytes(new_buf))
    with STATE_LOCK:
        STATE['dump_bytes'] = bytes(new_buf)
        STATE['dump'] = parsed
    return jsonify({'ok': True, 'channel_names': parsed['channel_names'],
                    'switch_names': parsed['switch_names']})


@app.route('/api/preset/<int:num>/sysex', methods=['POST'])
def preset_sysex(num):
    """Update the 30-byte SysEx string and/or ON/OFF toggle for a preset.

    Body: {"bytes": [int...] (optional), "on": bool (optional)}
    """
    d = request.get_json(force=True)
    edits = {}
    if 'bytes' in d: edits['sysex_bytes'] = d['bytes'] or []
    if 'on' in d:    edits['sysex_on']    = bool(d['on'])
    if not edits:
        return jsonify({'ok': False, 'error': 'no edits provided'}), 400
    try:
        preset = _splice_preset(num, **edits)
    except ValueError as e:
        return jsonify({'ok': False, 'error': str(e)}), 400
    return jsonify({'ok': True, 'preset': preset})

@app.route('/api/vfc/send', methods=['POST'])
def vfc_send():
    """Virtual foot controller — send raw MIDI.

    Body: {"channel": 1-16, "type": "pc"|"cc", "num": 0-127, "val": 0-127}
    """
    d = request.get_json(force=True)
    ch = max(1, min(16, int(d.get('channel', 1)))) - 1
    if d['type'] == 'pc':
        MIDI.send(bytes([0xC0 | ch, int(d['num']) & 0x7F]))
    elif d['type'] == 'cc':
        MIDI.send(bytes([0xB0 | ch, int(d['num']) & 0x7F, int(d['val']) & 0x7F]))
    elif d['type'] == 'note-on':
        MIDI.send(bytes([0x90 | ch, int(d['num']) & 0x7F, int(d['val']) & 0x7F]))
    elif d['type'] == 'note-off':
        MIDI.send(bytes([0x80 | ch, int(d['num']) & 0x7F, int(d['val']) & 0x7F]))
    else:
        return jsonify({'ok': False, 'error': 'unknown type'}), 400
    return jsonify({'ok': True})

@app.route('/api/vfc/preset', methods=['POST'])
def vfc_preset():
    """Recall a preset on a given channel via PC."""
    d = request.get_json(force=True)
    ch = max(1, min(16, int(d.get('channel', 1)))) - 1
    pc = max(0, min(127, int(d.get('pc', 0))))
    MIDI.send(bytes([0xC0 | ch, pc]))
    return jsonify({'ok': True})

@app.route('/api/monitor')
def monitor():
    return jsonify(MIDI.recent_monitor())

@app.route('/api/global-config', methods=['GET', 'POST'])
def global_config():
    if request.method == 'GET':
        return jsonify(STATE['global_config'])
    d = request.get_json(force=True)
    with STATE_LOCK:
        STATE['global_config'].update(d)
    return jsonify(STATE['global_config'])

@sio.on('connect')
def on_connect():
    emit('hello', {'midi': HAS_RTMIDI,
                   'ports': MIDI.list_ports(),
                   'has_dump': STATE['dump'] is not None})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5002))
    # Loopback only by default — this editor streams SysEx straight to your
    # rig and has no auth. Set AA_BIND=0.0.0.0 to expose on the LAN (only
    # behind something that does auth — never expose it raw).
    host = os.environ.get('AA_BIND', '127.0.0.1')
    print(f"Rocktron All Access editor — http://localhost:{port}")
    print(f"  bind: {host}  (AA_BIND=0.0.0.0 to expose on the LAN — not recommended)")
    print(f"  python-rtmidi: {'yes' if HAS_RTMIDI else 'NO — pip install python-rtmidi'}")
    sio.run(app, host=host, port=port, allow_unsafe_werkzeug=True)
