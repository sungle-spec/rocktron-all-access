"""Structural validation, deliberately independent of checksum hypotheses."""
from collections import Counter

from .codec import SYX_HEADER

EXPECTED_FRAME_ORDER = (309,) * 120 + (263, 223, 139, 279) + (457,) * 10 + (107,) * 10 + (23,)
EXPECTED_FRAME_COUNTS = dict(Counter(EXPECTED_FRAME_ORDER))
EXPECTED_TOTAL = len(EXPECTED_FRAME_ORDER)
MAX_DUMP_BYTES = 500_000


class FrameValidationError(ValueError):
    """A stream cannot safely be interpreted as the supported dump format."""


def validated_frames(buf):
    """Return complete frames; reject discarded bytes, nested starts and status bytes."""
    if not isinstance(buf, (bytes, bytearray)) or not buf:
        raise FrameValidationError('empty dump; expected SysEx bytes')
    if len(buf) > MAX_DUMP_BYTES:
        raise FrameValidationError('dump too large')
    frames = []
    offset = 0
    while offset < len(buf):
        if buf[offset] != 0xF0:
            raise FrameValidationError(f'non-SysEx data at byte {offset}')
        end = buf.find(b'\xF7', offset + 1)
        if end < 0:
            raise FrameValidationError('unterminated SysEx frame')
        frame = bytes(buf[offset:end + 1])
        if len(frame) < 7 or not frame.startswith(SYX_HEADER):
            raise FrameValidationError('not a Rocktron All Access frame')
        if any(b >= 0x80 for b in frame[1:-1]):
            raise FrameValidationError('embedded MIDI status or nested SysEx start')
        if len(frame) not in EXPECTED_FRAME_COUNTS:
            raise FrameValidationError(f'unsupported frame length: {len(frame)}')
        if frame[5] not in (0x2A, 0x2B):
            raise FrameValidationError('unsupported dump command')
        if frame[5] == 0x2A and (frames or len(frame) != 309):
            raise FrameValidationError('start-of-dump command outside first preset')
        frames.append(frame)
        offset = end + 1
    return frames


def validate_dump(buf, *, strict=True):
    """Validate supported framing; strict mode additionally requires canonical order.

    Inspection accepts a structurally valid partial stream with a preset. It
    cannot determine the identity of presets missing from a position-based dump.
    Never use inspection success as evidence a stream is safe to write.
    """
    frames = validated_frames(buf)
    if not any(len(frame) == 309 for frame in frames):
        raise FrameValidationError('no preset frames present')
    if strict:
        if len(frames) != EXPECTED_TOTAL:
            raise FrameValidationError(f'expected {EXPECTED_TOTAL} frames, got {len(frames)}')
        if tuple(map(len, frames)) != EXPECTED_FRAME_ORDER:
            raise FrameValidationError('frame order does not match a complete dump')
        if frames[0][5] != 0x2A or any(f[5] != 0x2B for f in frames[1:]):
            raise FrameValidationError('incorrect start/continuation dump commands')
    return True


def is_complete_dump(buf):
    try:
        validate_dump(buf)
    except FrameValidationError:
        return False
    return True
