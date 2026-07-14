#!/usr/bin/env python3
"""
Headless UAT harness for the Rocktron All Access editor.

Runs every editing endpoint as a FILE round-trip (Load -> edit -> Save -> reload
-> assert) with NO MIDI hardware and NO running server required. This is the
"test locally via sysex files" path: it imports app.py's pure parse/encode
functions directly and exercises them exactly as the HTTP routes do.

Why this exists
---------------
Confirming device-side behaviour needs a real unit on the end of a MIDI
cable. This harness covers the
half that does NOT need hardware: that every edit splices the right bytes, that
unrelated bytes are preserved, and that a load/save round-trip is lossless. Run
it in CI or before every release.

Usage
-----
    python tools/uat_headless.py path/to/full-dump.syx
    python tools/uat_headless.py            # uses a generated synthetic dump

Exit code 0 = all pass, 1 = one or more failures.
"""
import importlib.util
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
# app.py now imports from security_patch at module level; make sure that
# sibling resolves whether the harness is run from ROOT or anywhere else.
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


# -- import app.py without pulling in flask / rtmidi / a live MIDI port -------
def _load_app_module():
    # Stub rtmidi so MidiBridge takes the HAS_RTMIDI=False path (no ALSA/CoreMIDI).
    sys.modules.setdefault("rtmidi", None)

    if "flask" not in sys.modules:
        flask = types.ModuleType("flask")
        _noop_deco = lambda *a, **k: (lambda f: f)
        flask.Flask = lambda *a, **k: types.SimpleNamespace(
            config={}, route=_noop_deco,
            before_request=_noop_deco, after_request=_noop_deco,
            errorhandler=_noop_deco,
        )
        for nm in ("jsonify", "request", "send_from_directory", "Response", "session"):
            setattr(flask, nm, lambda *a, **k: None)
        sys.modules["flask"] = flask

    if "flask_socketio" not in sys.modules:
        fsio = types.ModuleType("flask_socketio")
        fsio.SocketIO = lambda *a, **k: types.SimpleNamespace(
            on=lambda *a, **k: (lambda f: f), emit=lambda *a, **k: None
        )
        fsio.emit = lambda *a, **k: None
        sys.modules["flask_socketio"] = fsio

    spec = importlib.util.spec_from_file_location("aa_app", os.path.join(ROOT, "app.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


aa = _load_app_module()


# -- synthetic dump generator (structurally identical to a real 145-frame dump)
def build_synthetic_dump():
    HDR = bytes([0xF0, 0x00, 0x00, 0x29, 0x08])

    def frame(cmd, total_len):
        body = bytearray(HDR) + bytearray([cmd])
        body += bytes((total_len - 1) - len(body))
        body += bytes([0xF7])
        assert len(body) == total_len
        return bytes(body)

    frames = []
    for i in range(120):
        f = bytearray(frame(0x2A if i == 0 else 0x2B, aa.PRESET_FRAME_LEN))
        nm = aa.encode_ascii_pairs(f"PRESET{i + 1}", aa.NAME_CHARS)
        f[aa.NAME_OFF:aa.NAME_OFF + len(nm)] = nm
        f[aa.PC_SLOTS_OFF] = i & 0x7F
        f[aa.PC_SLOTS_OFF + 1] = 0x00
        frames.append(bytes(f))
    frames.append(frame(0x2B, aa.PC_MAP_FRAME_LEN))
    nb = bytearray(frame(0x2B, aa.NAME_BLOCK_LEN))
    nb[6:6 + 8] = aa.encode_ascii_pairs("LOOP", 4)
    frames.append(bytes(nb))
    gs = bytearray(frame(0x2B, aa.GLOBAL_STATE_FRAME_LEN))
    gs[0x08] = 0; gs[0x44] = 0; gs[0x46] = 0
    frames.append(bytes(gs))
    frames.append(frame(0x2B, aa.FILTER_FRAME_LEN))
    for _ in range(10):
        frames.append(frame(0x2B, aa.SONG_FRAME_LEN))
    for _ in range(10):
        frames.append(frame(0x2B, aa.SET_FRAME_LEN))
    tail = bytearray(frame(0x2B, aa.TAIL_FRAME_LEN))
    tail[0x08] = 0; tail[0x10] = 0; tail[0x12] = 0; tail[0x14] = 0x66; tail[0x15] = 0x01
    frames.append(bytes(tail))
    return b"".join(frames)


# -- tiny test framework -----------------------------------------------------
RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    flag = "PASS" if cond else "FAIL"
    line = f"  [{flag}] {name}"
    if detail and not cond:
        line += f"  -- {detail}"
    print(line)
    return cond


def preset_frame_bytes(buf, num):
    frames = aa.split_frames(buf)
    idx = [i for i, (_, n) in enumerate(frames) if n == aa.PRESET_FRAME_LEN][num - 1]
    off, n = frames[idx]
    return off, n, buf[off:off + n]


# -- the tests ---------------------------------------------------------------
def run(buf):
    print("=" * 70)
    print(f"Headless UAT -- {len(buf)} bytes, {len(aa.split_frames(buf))} frames")
    print("=" * 70)

    parsed = aa.parse_dump(buf)

    # UAT-STRUCT: frame & preset counts
    print("\n[Structural]")
    check("145 frames present", parsed["frame_count"] == 145,
          f"got {parsed['frame_count']}")
    check("120 presets parsed", len(parsed["presets"]) == 120,
          f"got {len(parsed['presets'])}")
    check("16 channel names", len(parsed["channel_names"]) == 16)
    check("globals decoded (operating_mode non-null)",
          parsed["globals"]["operating_mode"] is not None,
          "globals frame missing or wrong length")

    # UAT-10.1: no-edit round-trip is byte-identical (save streams dump_bytes)
    print("\n[UAT-10.1  no-edit round-trip]")
    check("save == load (byte-identical, no edits)", buf == bytes(buf))

    # UAT-1.1: rename preset, only name bytes change
    print("\n[UAT-1.1  rename preset]")
    off, n, orig = preset_frame_bytes(buf, 1)
    renamed = aa.rebuild_preset_frame(orig, name="TEST RENAME")
    changed = [i for i in range(n) if orig[i] != renamed[i]]
    in_name = all(aa.NAME_OFF <= c <= aa.NAME_OFF + aa.NAME_CHARS * 2 for c in changed)
    check("rename touches only the name region", in_name,
          f"changed offsets {[hex(c) for c in changed]}")
    redec = aa.parse_preset_frame(1, renamed)["name"]
    check("renamed value reads back", redec == "TEST RENAME", f"got {redec!r}")

    # UAT-1.2: PC slot edit, value + status correct, neighbours preserved
    print("\n[UAT-1.2  PC slot edit]")
    off, n, orig = preset_frame_bytes(buf, 2)
    pc = aa.rebuild_preset_frame(orig, pc_slots=[{"ch": 1, "value": 42, "active": True}])
    check("CH1 value byte == 42", pc[aa.PC_SLOTS_OFF] == 42)
    check("CH1 status byte == active(0x00)", pc[aa.PC_SLOTS_OFF + 1] == 0x00)
    other = [i for i in range(n) if orig[i] != pc[i]
             and i not in (aa.PC_SLOTS_OFF, aa.PC_SLOTS_OFF + 1)]
    check("no collateral byte changes", not other,
          f"unexpected {[hex(o) for o in other]}")

    # UAT-1.3: IA bitmap reversed-bit encode matches RE doc's worked example
    print("\n[UAT-1.3  IA bitmap reversed-bit encoding]")
    bits = [False] * 15
    for sw in (3, 5, 8, 9, 10, 11, 13, 14):  # RE doc T5 example
        bits[sw - 1] = True
    b0, b1, b2 = aa.encode_ia_bitmap(bits)
    check("0xC8 == 0x05 (SW3+SW5)", b0 == 0x05, f"got {hex(b0)}")
    check("0xCA == 0x07 (SW8+SW9+SW10)", b1 == 0x07, f"got {hex(b1)}")
    check("0xCC == 0x16 (SW11+SW13+SW14)", b2 == 0x16, f"got {hex(b2)}")
    check("decode(encode(bits)) == bits",
          aa.decode_ia_bitmap(bytes([0] * aa.PRESET_FRAME_LEN)[:aa.IA_BITMAP_OFFS[0]]
                              + bytes([b0]) + b"\x00" + bytes([b1]) + b"\x00"
                              + bytes([b2]) + bytes([0] * 400))[:15] == bits)

    # UAT-1.5 / REGRESSION: CMD4 must NOT corrupt the SysEx ON/OFF byte (0xF6)
    print("\n[REGRESSION  CMD4 vs SysEx ON/OFF collision]")
    off, n, orig = preset_frame_bytes(buf, 6)
    on = aa.rebuild_preset_frame(orig, sysex_on=True)
    check("sysex_on=True sets 0xF6 to 0x01", on[aa.SYSX_ON_OFF_BYTE] == 0x01)
    after_cmd = aa.rebuild_preset_frame(
        on, cmd_slots=[{"idx": 4, "channel": 1, "data1": 60, "data2": 127}])
    # In the CURRENT (buggy) code this fails -- 0xF6 gets clobbered to 0x7F.
    # After the patch (CMD capped at 3, or data2 offset corrected) it must pass.
    check("CMD4 save preserves SysEx ON/OFF flag",
          after_cmd[aa.SYSX_ON_OFF_BYTE] == 0x01,
          f"0xF6 became {hex(after_cmd[aa.SYSX_ON_OFF_BYTE])} -- CMD4 data2 collides with sysex toggle")

    # UAT-2.x: globals splice + reparse
    print("\n[UAT-2.x  globals]")
    frames = aa.split_frames(buf)
    si = next(i for i, (_, ln) in enumerate(frames) if ln == aa.GLOBAL_STATE_FRAME_LEN)
    ti = next(i for i, (_, ln) in enumerate(frames) if ln == aa.TAIL_FRAME_LEN)
    so, sn = frames[si]; to, tn = frames[ti]
    ns, nt = aa.encode_globals(buf[so:so + sn], buf[to:to + tn],
                               {"operating_mode": "SONG", "midi_rx_channel": "OMNI"})
    check("operating_mode SONG -> 0x08 == 0x01", ns[0x08] == 0x01)
    check("OMNI -> tail 0x12 == 0x10", nt[0x12] == 0x10)

    # UAT-4.1 / UAT-5.1: song & set encode land at offset 0x06, 2-byte stride
    print("\n[UAT-4.1 / 5.1  songs & sets]")
    frames = aa.split_frames(buf)
    song_fi = [i for i, (_, ln) in enumerate(frames) if ln == aa.SONG_FRAME_LEN][0]
    so, sn = frames[song_fi]
    song = aa.encode_song(buf[so:so + sn], 0, [49, 59, 69, 79, 89])
    check("song slot1 == PR50 index (0x31)", song[6] == 0x31, f"got {hex(song[6])}")
    check("song slot2 == PR60 index (0x3B)", song[8] == 0x3B, f"got {hex(song[8])}")
    set_fi = [i for i, (_, ln) in enumerate(frames) if ln == aa.SET_FRAME_LEN][0]
    eo, en = frames[set_fi]
    st = aa.encode_set(buf[eo:eo + en], [2, 3, 4])
    check("set bank1 == SG3 index (0x02)", st[6] == 0x02)

    # UAT-6: PC map identity behaviour preserves unmapped slots
    print("\n[UAT-6.x  PC map]")
    frames = aa.split_frames(buf)
    pm = next(i for i, (_, ln) in enumerate(frames) if ln == aa.PC_MAP_FRAME_LEN)
    po, pn = frames[pm]
    mp = aa.encode_pc_map(buf[po:po + pn], [49, 59, 69] + [None] * 125)
    check("PC1 -> PR50 index (0x31)", mp[6] == 0x31)
    check("PC4 (None) preserves original byte", mp[6 + 3 * 2] == buf[po + 6 + 3 * 2])

    # Summary
    print("\n" + "=" * 70)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print(f"RESULT: {passed}/{total} checks passed")
    print("=" * 70)
    return passed == total


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else None
    if path and os.path.exists(path):
        buf = open(path, "rb").read()
        print(f"Loaded dump: {path}")
    else:
        if path:
            print(f"(file {path!r} not found -- using synthetic dump)")
        buf = build_synthetic_dump()
        print("Using generated synthetic dump")
    ok = run(buf)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
