import unittest
from pathlib import Path

from rocktron import codec
from rocktron.validation import FrameValidationError, validate_dump
from tools.uat_headless import build_synthetic_dump


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.buf = build_synthetic_dump()
        self.frames = [self.buf[o:o+n] for o, n in codec.split_frames(self.buf)]

    def test_canonical_synthetic_is_complete(self):
        self.assertTrue(validate_dump(self.buf))
        self.assertTrue(codec.parse_dump(self.buf)['complete'])

    def test_real_captures_validate_if_available(self):
        paths = list(Path('reference').rglob('*.syx'))
        for path in paths:
            with self.subTest(path=path.name):
                validate_dump(path.read_bytes())

    def test_same_counts_wrong_order_rejected(self):
        frames = list(self.frames)
        frames[120], frames[121] = frames[121], frames[120]
        with self.assertRaises(FrameValidationError):
            validate_dump(b''.join(frames))

    def test_wrong_command_rejected(self):
        buf = bytearray(self.buf)
        buf[5] = 0x2B
        with self.assertRaises(FrameValidationError):
            validate_dump(buf)

    def test_no_silent_discard_of_malformed_bytes(self):
        mutations = [b'junk' + self.buf, self.buf + b'\xF0', self.buf + b'junk']
        nested = bytearray(self.buf)
        nested[16] = 0xF0
        mutations.append(nested)
        status = bytearray(self.buf)
        status[16] = 0x90
        mutations.append(status)
        for buf in mutations:
            with self.subTest(prefix=bytes(buf[:10]).hex()):
                with self.assertRaises(FrameValidationError):
                    validate_dump(buf, strict=False)

    def test_partial_never_promoted_after_edit(self):
        frames = self.frames[:-1]
        validate_dump(b''.join(frames), strict=False)
        frames[0] = codec.rebuild_preset_frame(frames[0], name='PARTIAL')
        self.assertFalse(codec.parse_dump(b''.join(frames))['complete'])

    def test_pc16_does_not_overwrite_pedal_config(self):
        orig = bytearray(self.frames[0])
        orig[0x40:0x48] = bytes([3, 0, 11, 0, 127, 0, 0, 0])
        new = codec.rebuild_preset_frame(orig, pc_slots=[{'ch':16, 'value':73, 'active':True}])
        self.assertEqual(73, new[0x3E])
        self.assertEqual(orig[0x40:0xC8], new[0x40:0xC8])

    def test_capture_pc_channel_mapping(self):
        path = Path('reference/re-captures/rev_T1_presets.syx')
        if not path.exists():
            self.skipTest('private capture not distributed')
        frame = path.read_bytes()[:309]
        slots = codec.decode_pc_slots(frame)
        self.assertEqual(list(range(16)), [s['value'] for s in slots])
        self.assertTrue(all(s['active'] for s in slots))
        base = Path('reference/baseline-pre-RE.syx').read_bytes()[:309]
        self.assertEqual(99, codec.decode_pc_slots(base)[0]['value'])

    def test_unknown_global_values_are_preserved(self):
        state, tail = bytearray(self.frames[121]), bytearray(self.frames[-1])
        state[0x46] = 5
        tail[0x12] = 0x7F
        g = codec.decode_globals(state, tail)
        self.assertIsNone(g['switch_types'][1])
        self.assertIsNone(g['midi_rx_channel'])
        self.assertIsNone(g['operating_mode'])
        ns, nt = codec.encode_globals(state, tail, {'operating_mode':'REMOTE', 'bank_style':'OFF'})
        self.assertEqual(bytes(state), ns)
        self.assertEqual(bytes(tail), nt)

    def test_sysex_eox_noop_preserves_all_bytes(self):
        frame = bytearray(self.frames[0])
        frame[codec.SYSX_OFF:codec.SYSX_OFF+4] = b'\x01\x00\x02\x00'
        for i in range(2, codec.SYSX_COUNT):
            frame[codec.SYSX_OFF+i*2] = 17  # preserve even stale payload after EOX
            frame[codec.SYSX_OFF+i*2+1] = 1
        values = codec.decode_sysex_string(frame)
        self.assertEqual([1, 2], values[:2])
        self.assertTrue(all(v is None for v in values[2:]))
        self.assertEqual(bytes(frame), codec.rebuild_preset_frame(frame, sysex_bytes=values))

    def test_last_song_last_slot_does_not_change_frame_terminator(self):
        orig = self.frames[124]
        new = codec.encode_song(orig, 14, [None]*14+[117])
        changed = [i for i, (a,b) in enumerate(zip(orig,new)) if a != b]
        self.assertEqual([454], changed)
        self.assertEqual(orig[-2:], new[-2:])
        self.assertEqual(117, codec.decode_songs([new])[-1]['slots'][-1])


if __name__ == '__main__':
    unittest.main()
