#!/usr/bin/env python3
"""Create a public source snapshot in a NEW directory, without copying Git history.

Never overwrites a checkout or deletes local captures. Review the result before
publishing. Copyrighted manuals and personal dumps are intentionally excluded.
"""
import argparse
from pathlib import Path
import shutil
import tempfile

ROOT = Path(__file__).resolve().parents[1]
FILES = (
    '.gitignore', 'LICENSE', 'README.md', 'app.py', 'security_patch.py',
    'requirements.txt', 'run.sh', 'run.bat',
    '.github/workflows/tests.yml',
    'rocktron/__init__.py', 'rocktron/codec.py', 'rocktron/validation.py',
    'templates/index.html', 'static/editor.css', 'static/editor.js',
    'static/vendor/socket.io.min.js', 'static/vendor/socket.io.LICENSE',
    'tools/uat_headless.py', 'tools/analyze_captures.py', 'tools/diff_dumps.py',
    'tools/export_public.py', 'tools/browser_smoke.py',
    'tests/__init__.py', 'tests/test_protocol.py', 'tests/test_api.py',
    'tests/test_export.py',
    'docs/GETTING_STARTED.md', 'docs/HARDWARE_TEST_PLAN.md',
    'docs/MANUAL_SUMMARY.md', 'docs/MIDI_SPEC.md',
    'docs/REVERSE_ENGINEERING.md', 'docs/USER_MANUAL.md',
    'docs/AUDIT_REPORT.md', 'docs/ARCHITECTURE.md', 'docs/HANDOVER.md',
)


def export_snapshot(destination, source=ROOT):
    destination = Path(destination).absolute()
    if destination.exists():
        raise ValueError('destination already exists; use a new directory to protect local files')
    missing = [name for name in FILES if not (source / name).is_file()]
    if missing:
        raise ValueError('missing public files: ' + ', '.join(missing))
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Build first; failed copies cannot leave a half-populated release snapshot.
    with tempfile.TemporaryDirectory(prefix='all-access-export-', dir=destination.parent) as tmp:
        staging = Path(tmp) / 'snapshot'
        staging.mkdir()
        for name in FILES:
            target = staging / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source / name, target)
        staging.rename(destination)
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', type=Path, help='new snapshot directory (must not exist)')
    args = parser.parse_args()
    try:
        result = export_snapshot(args.destination)
    except ValueError as error:
        parser.error(str(error))
    print(f'Exported {len(FILES)} reviewed source files to {result}')


if __name__ == '__main__':
    main()
