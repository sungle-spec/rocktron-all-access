#!/usr/bin/env python3
"""Frame-align two All Access bulk dumps and report per-frame diffs.

Each frame starts F0 and ends F7. The two dumps must have the same number of
frames in the same order. For each frame with byte differences, prints:

    Frame <idx> (size <n>, cmd <hex>):
      0xNN  AA -> BB
      0xNN  AA -> BB
      ...

Contiguous diffs are clustered and labeled with their before/after byte ranges.
"""

import sys
from pathlib import Path


def split_frames(buf: bytes) -> list[bytes]:
    frames = []
    start = None
    for i, b in enumerate(buf):
        if b == 0xF0:
            start = i
        elif b == 0xF7 and start is not None:
            frames.append(buf[start:i + 1])
            start = None
    return frames


def cluster_diffs(diffs: list[tuple[int, int, int]]) -> list[list[tuple[int, int, int]]]:
    """Group offset-contiguous diffs together."""
    if not diffs:
        return []
    clusters = [[diffs[0]]]
    for d in diffs[1:]:
        if d[0] == clusters[-1][-1][0] + 1:
            clusters[-1].append(d)
        else:
            clusters.append([d])
    return clusters


def diff_frames(a: bytes, b: bytes) -> list[tuple[int, int, int]]:
    diffs = []
    n = min(len(a), len(b))
    for i in range(n):
        if a[i] != b[i]:
            diffs.append((i, a[i], b[i]))
    if len(a) != len(b):
        # frames different sizes — flag the trailing region
        big = a if len(a) > len(b) else b
        for i in range(n, len(big)):
            diffs.append((i, a[i] if i < len(a) else -1, b[i] if i < len(b) else -1))
    return diffs


def fmt_cluster(c: list[tuple[int, int, int]]) -> str:
    if len(c) == 1:
        off, av, bv = c[0]
        return f"  0x{off:04X}  {av:02X} -> {bv:02X}"
    start = c[0][0]
    end = c[-1][0]
    a_hex = " ".join(f"{x[1]:02X}" for x in c)
    b_hex = " ".join(f"{x[2]:02X}" for x in c)
    return f"  0x{start:04X}..0x{end:04X} ({len(c)}B):  {a_hex}\n                          -> {b_hex}"


def main():
    if len(sys.argv) != 3:
        print(f"usage: {sys.argv[0]} <baseline.syx> <test.syx>", file=sys.stderr)
        sys.exit(1)
    a = Path(sys.argv[1]).read_bytes()
    b = Path(sys.argv[2]).read_bytes()
    fa = split_frames(a)
    fb = split_frames(b)
    print(f"baseline: {len(a)} bytes, {len(fa)} frames")
    print(f"test:     {len(b)} bytes, {len(fb)} frames")
    if len(fa) != len(fb):
        print(f"WARN: frame count mismatch ({len(fa)} vs {len(fb)})")
    n = min(len(fa), len(fb))
    total_diff_frames = 0
    for idx in range(n):
        d = diff_frames(fa[idx], fb[idx])
        if not d:
            continue
        total_diff_frames += 1
        cmd = fa[idx][5] if len(fa[idx]) > 5 else 0
        print(f"\nFrame {idx + 1} (size {len(fa[idx])}, cmd 0x{cmd:02X}):  {len(d)} byte diffs")
        for c in cluster_diffs(d):
            print(fmt_cluster(c))
    print(f"\n{total_diff_frames} of {n} frames differ")


if __name__ == "__main__":
    main()
