#!/usr/bin/env python3
"""Offline reverse-engineering analysis of All Access bulk-dump captures.

Mines a baseline dump (and optionally the rev_T* capture series) for byte-level
evidence, verifies the documented field layouts against the data, and hunts for
structure in the regions the RE docs left open. This is the evidence record
behind docs/REVERSE_ENGINEERING.md — rerun it whenever a new capture lands.

Usage
-----
    python tools/analyze_captures.py BASELINE.syx [CAPTURE.syx ...]
    python tools/analyze_captures.py reference/baseline-pre-RE.syx reference/re-captures/rev_T*.syx

With only a baseline, runs the corpus analysis (120-preset statistics).
Each extra capture is diffed frame-by-frame against the baseline.

Exit code 0 = all layout assertions hold, 1 = a verified anchor failed.
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

# ── frame handling (mirrors app.py split_frames) ────────────────────────────
def split_frames(buf):
    frames, cur = [], None
    for b in buf:
        if b == 0xF0:
            cur = bytearray([b])
        elif cur is not None:
            cur.append(b)
            if b == 0xF7:
                frames.append(bytes(cur))
                cur = None
    return frames

FRAME_ROLES = {309: 'preset', 457: 'song', 107: 'set', 279: 'names',
               263: 'pcmap', 223: 'globals', 139: 'filter', 23: 'tail'}

# ── the confirmed CMD layout (see docs/REVERSE_ENGINEERING.md §Custom MIDI) ──
CMD_BASE, CMD_STRIDE, CMD_SLOTS = 0xCE, 8, 5
# within-slot: +0 type, +2 channel (0-based), +4 data1, +6 data2
CMD_TYPES = ['NOFF>', 'N ON>', 'KPRS>', 'C CH>', 'P CH>', 'CPRS>', 'PBEN>',
             'T CLK', 'START', 'CONTU', 'STOP', 'ACTSN', 'SYSRS', 'M T C>',
             'SGPP>', 'SGSL>', 'T REQ', 'NONE']      # manual list order; NONE=0x11

FAILURES = []

def check(label, cond, detail=''):
    mark = 'PASS' if cond else 'FAIL'
    print(f"  [{mark}] {label}" + (f" -- {detail}" if detail and not cond else ''))
    if not cond:
        FAILURES.append(label)

def decode_cmd_slot(frame, i):
    b = CMD_BASE + i * CMD_STRIDE
    return dict(idx=i + 1, type=frame[b], ch=frame[b + 2],
                d1=frame[b + 4], d2=frame[b + 6],
                hi=(frame[b + 1], frame[b + 3], frame[b + 5], frame[b + 7]))

# ── sections ─────────────────────────────────────────────────────────────────
def section_inventory(dumps):
    print("=" * 72)
    print("INVENTORY")
    print("=" * 72)
    for name, frames in dumps:
        hist = {}
        for f in frames:
            hist[len(f)] = hist.get(len(f), 0) + 1
        h = ' '.join(f"{n}x{ln}" for ln, n in sorted(hist.items(), reverse=True))
        print(f"  {name}: {len(frames)} frames  [{h}]")

def section_corpus(base_frames):
    print("=" * 72)
    print("CORPUS — 120-preset CMD-block statistics (base 0xCE, stride 8)")
    print("=" * 72)
    presets = [f for f in base_frames if len(f) == 309]
    check(f"baseline has 120 preset frames", len(presets) == 120, str(len(presets)))
    total = valid = 0
    cmds = []
    for pn, f in enumerate(presets, 1):
        for i in range(CMD_SLOTS):
            c = decode_cmd_slot(f, i)
            total += 1
            ok = (c['type'] <= 17 and c['ch'] <= 15 and c['d1'] <= 127
                  and c['d2'] <= 127 and all(h == 0 for h in c['hi']))
            valid += ok
            if ok and c['type'] != 0x11:
                cmds.append((pn, c))
    pct = 100.0 * valid / total
    check(f"CMD slots valid under layout: {valid}/{total} ({pct:.1f}%)", valid == total)
    print(f"  Non-NONE commands found: {len(cmds)}")
    for pn, c in cmds:
        print(f"    PR{pn:03d} CMD{c['idx']}: {CMD_TYPES[c['type']]:6s} "
              f"ch{c['ch'] + 1:<3d} d1={c['d1']:<3d} d2={c['d2']}")
    # CMD5 exists as a real slot and the region stops before the sysex toggle
    check("CMD region 0xCE..0xF5 ends before SysEx toggle 0xF6",
          CMD_BASE + CMD_SLOTS * CMD_STRIDE - 1 == 0xF5)
    check("at least one preset uses CMD5 (slot at 0xEE is real)",
          any(c['idx'] == 5 for _, c in cmds))

def frame_diffs(a, b):
    """Byte offsets differing between two equal-length frames."""
    return [o for o in range(min(len(a), len(b))) if a[o] != b[o]]

def section_diffs(base_frames, captures):
    print("=" * 72)
    print("CAPTURE DIFFS vs baseline (frame#, role, offsets)")
    print("=" * 72)
    for name, frames in captures:
        print(f"-- {name}")
        if len(frames) != len(base_frames):
            print(f"   frame count differs ({len(frames)} vs {len(base_frames)}) — skipped")
            continue
        for idx, (fa, fb) in enumerate(zip(base_frames, frames)):
            if fa == fb:
                continue
            offs = frame_diffs(fa, fb)
            role = FRAME_ROLES.get(len(fa), '?')
            shown = ' '.join(
                f"0x{o:02X}:{fa[o]:02X}->{fb[o]:02X}" for o in offs[:14])
            more = '' if len(offs) <= 14 else f" (+{len(offs) - 14} more)"
            print(f"   frame {idx:3d} ({role:7s} {len(fa)}B): {shown}{more}")

def section_anchors(base_frames, captures):
    """Assert every documented layout anchor against the captures that set it."""
    print("=" * 72)
    print("ANCHOR ASSERTIONS (documented layouts vs capture evidence)")
    print("=" * 72)
    cap = dict(captures)

    def pr(frames, n=1):
        return [f for f in frames if len(f) == 309][n - 1]

    t1 = cap.get('rev_T1_presets')
    if t1 is not None:
        p = pr(t1)
        c1 = decode_cmd_slot(p, 0)
        check("T1 CMD1 decodes as N ON> ch1 n60 v100",
              (c1['type'], c1['ch'], c1['d1'], c1['d2']) == (0x01, 0x00, 60, 100),
              str(c1))
        check("T1 IA bytes = SW6-15 all-on (C8=00 CA=1F CC=1F; SW1-5 are preset keys)",
              (p[0xC8], p[0xCA], p[0xCC]) == (0x00, 0x1F, 0x1F),
              f"{p[0xC8]:02X} {p[0xCA]:02X} {p[0xCC]:02X}")

    t4 = cap.get('rev_T4_final')
    if t4 is not None:
        p = pr(t4)
        c2 = decode_cmd_slot(p, 1)
        check("T4 CMD2 decodes as C CH> ch5 cc20 v30",
              (c2['type'], c2['ch'], c2['d1'], c2['d2']) == (0x03, 0x04, 20, 30),
              str(c2))

    t5 = cap.get('rev_T5_final')
    if t5 is not None:
        p = pr(t5)
        check("T5 IA asymmetric pattern 0xC8=05 0xCA=07 0xCC=16",
              (p[0xC8], p[0xCA], p[0xCC]) == (0x05, 0x07, 0x16))
        p2 = pr(t5, 2)
        check("T5 PR2 SysEx toggle 0xF6=01", p2[0xF6] == 0x01, f"{p2[0xF6]:02X}")
        c1, c2 = decode_cmd_slot(p, 0), decode_cmd_slot(p, 1)
        check("T5 preserves CMD1/CMD2 through later captures",
              c1['type'] == 0x01 and c2['type'] == 0x03)

def section_hunts(base_frames, captures):
    print("=" * 72)
    print("HUNTS — open items")
    print("=" * 72)
    cap = dict(captures)
    base_by_role = {}
    for i, f in enumerate(base_frames):
        base_by_role.setdefault(FRAME_ROLES.get(len(f), '?'), []).append((i, f))

    # -- Hunt 1: PER-PR channel byte (T2 set a preset's SW11 -> CH3, PED1 -> CH4)
    t2 = cap.get('rev_T2_globals')
    if t2 is not None:
        print("-- Hunt 1: PER-PR channel byte (T2, full preset-frame sweep)")
        for idx, (fa, fb) in enumerate(zip(base_frames, t2)):
            if len(fa) != 309 or fa == fb:
                continue
            offs = frame_diffs(fa, fb)
            print(f"   preset frame {idx} diff offsets: " +
                  ' '.join(f"0x{o:02X}:{fa[o]:02X}->{fb[o]:02X}" for o in offs))
        print("   (doc model: 17-slot x 8B IA table at 0x40..0xC7, reversed chan-id"
              " order -> SW11 slot at 0x70: flag+0, CC#+2, ON+4, OFF+6;"
              " look above for a 0x03/0x04 channel byte)")

    # -- Hunt 2: PC-map OFF encoding (baseline's own unmapped slots)
    print("-- Hunt 2: PC-map high bytes / OFF pattern (baseline)")
    pcmap = base_by_role.get('pcmap', [])
    if pcmap:
        f = pcmap[0][1]
        pairs = [(f[0x06 + 2 * i], f[0x07 + 2 * i]) for i in range(128)]
        from collections import Counter
        his = Counter(hi for _, hi in pairs)
        print(f"   high-byte histogram: {dict(his)}")
        offvals = Counter(lo for lo, _ in pairs if lo >= 120)
        print(f"   low bytes >=120 (doc's 'unmapped' heuristic): {dict(offvals) or 'none'}")

    # -- Hunt 3: set-frame off-by-one (T4 set SET1 banks 1-3)
    t4 = cap.get('rev_T4_final')
    if t4 is not None:
        print("-- Hunt 3: set-frame indexing (T4 SET1 edit)")
        seti = [i for i, f in enumerate(base_frames) if len(f) == 107]
        for n, i in enumerate(seti):
            if base_frames[i] != t4[i]:
                offs = frame_diffs(base_frames[i], t4[i])
                print(f"   set frame position {n} (dump frame {i}) changed: " +
                      ' '.join(f"0x{o:02X}:{base_frames[i][o]:02X}->{t4[i][o]:02X}"
                               for o in offs))

    # -- Hunt 4: 0x44 double-attribution (bank size vs switch-type stack)
    print("-- Hunt 4: globals-frame 0x28..0x48 across captures (switch-type stack"
          " vs bank-size)")
    gidx = base_by_role.get('globals', [(None, None)])[0][0]
    if gidx is not None:
        for name in ('rev_T0_idempotent', 'rev_T2_globals', 'rev_T3_songs',
                     'rev_T4_final', 'rev_T5_final', 'rev_T6_final'):
            fr = cap.get(name)
            if fr is None:
                continue
            g = fr[gidx]
            row = ' '.join(f"{g[o]:02X}" for o in range(0x28, 0x4A, 2))
            print(f"   {name:18s} 0x28..0x48: {row}")
        g0 = base_frames[gidx]
        row = ' '.join(f"{g0[o]:02X}" for o in range(0x28, 0x4A, 2))
        print(f"   {'baseline':18s} 0x28..0x48: {row}")
        print("   (T2 nominally set Bank Size=10 -> look for a byte at 0x04;"
              " T5 set size=1 + SW1/2 types; T6 set SW3/4 types)")

    # -- Hunt 5: filter frame structure
    print("-- Hunt 5: filter-frame (139B) diffs across captures")
    fidx = base_by_role.get('filter', [(None, None)])[0][0]
    if fidx is not None:
        for name, fr in captures:
            if fr[fidx] != base_frames[fidx]:
                offs = frame_diffs(base_frames[fidx], fr[fidx])
                print(f"   {name}: " + ' '.join(
                    f"0x{o:02X}:{base_frames[fidx][o]:02X}->{fr[fidx][o]:02X}"
                    for o in offs))

    # -- Hunt 6: unknown-byte census in preset frames (outside documented fields)
    print("-- Hunt 6: preset-frame unknown-byte census (baseline, non-zero bytes"
          " outside documented fields)")
    documented = set()
    documented.update(range(0x00, 0x06))              # header F0 00 00 29 08 cmd
    documented.update(range(0x06, 0x06 + 26))         # name pairs
    documented.update(range(0x24, 0x24 + 32))         # PC slots
    documented.update(range(0x40, 0xC8))              # PER-PR IA slot table
    documented.update({0xC8, 0xC9, 0xCA, 0xCB, 0xCC, 0xCD})   # IA bitmap
    documented.update(range(CMD_BASE, CMD_BASE + CMD_SLOTS * CMD_STRIDE))  # CMD
    documented.update({0xF6, 0xF7})                   # sysex toggle
    documented.update(range(0xF8, 0xF8 + 60))         # sysex string pairs
    documented.add(308)                               # trailing F7
    from collections import Counter
    unk = Counter()
    for _, f in base_by_role.get('preset', []):
        for o in range(309):
            if o not in documented and f[o] != 0:
                unk[o] += 1
    if unk:
        print("   offset: #presets-nonzero -> " + ' '.join(
            f"0x{o:02X}:{n}" for o, n in sorted(unk.items())))
    else:
        print("   none — every non-zero byte in all 120 preset frames is accounted for")

def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(2)
    paths = [Path(a) for a in args]
    for p in paths:
        if not p.exists():
            sys.exit(f"not found: {p}")
    dumps = [(p.stem, split_frames(p.read_bytes())) for p in paths]
    base_name, base_frames = dumps[0]
    captures = dumps[1:]

    section_inventory(dumps)
    section_corpus(base_frames)
    if captures:
        section_diffs(base_frames, captures)
        section_anchors(base_frames, captures)
        section_hunts(base_frames, captures)

    print("=" * 72)
    if FAILURES:
        print(f"RESULT: {len(FAILURES)} assertion(s) FAILED:")
        for f in FAILURES:
            print(f"  - {f}")
        sys.exit(1)
    print("RESULT: all layout assertions hold")

if __name__ == '__main__':
    main()
