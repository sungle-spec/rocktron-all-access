from pathlib import Path
import tempfile
import unittest

from tools.export_public import FILES, export_snapshot


class ExportTests(unittest.TestCase):
    def test_existing_capture_directory_is_never_deleted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            capture = root / 'dumps' / 'unique.syx'
            capture.parent.mkdir()
            capture.write_bytes(b'personal capture')
            with self.assertRaises(ValueError):
                export_snapshot(root)
            self.assertEqual(b'personal capture', capture.read_bytes())

    def test_explicit_allowlist_excludes_local_evidence_and_history(self):
        self.assertFalse(any(name.startswith(('reference/', '.git/', 'docs/archive/')) for name in FILES))
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)/'source'
            source.mkdir()
            for name in FILES:
                path = source/name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('reviewed source')
            (source/'untracked-secret.env').write_text('not for export')
            destination = Path(tmp)/'public'
            export_snapshot(destination, source=source)
            self.assertEqual(set(FILES), {str(p.relative_to(destination)) for p in destination.rglob('*') if p.is_file()})


if __name__ == '__main__':
    unittest.main()
