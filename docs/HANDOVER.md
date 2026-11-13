# Audit and refactor handover

Updated 2026-11-13. Start with the [audit report](AUDIT_REPORT.md) for evidence,
fixes, verification limits, and dependency advisories. The
[architecture guide](ARCHITECTURE.md) explains the refactored file layout.

## Delivered source changes

- Corrected the PC-slot base to `0x20` using the tracked T1 ascending capture.
  The old `0x24` base skipped CH1/2 and allowed CH15/16 edits to overwrite PED1.
- Preserved SysEx EOX flags, unknown command codes, and unknown settings.
- Added canonical dump validation and kept partial captures partial after edits.
- Serialized edits and hardware output; exposed write status and actual errors.
- Disabled unresolved Operating Mode/Bank Style writes and default-disabled
  experimental bulk and single-preset sends.
- Split codec/validation into `rocktron/`; extracted frontend assets into `static/`.
- Patched runtime dependency pins, added API/protocol/export tests and CI,
  bundled the browser Socket.IO client, and archived superseded working notes.
- Added an explicit source exporter that refuses existing destinations.

## Run and verify

Install the updated requirements before starting an existing environment:

```bash
.venv/bin/python -m pip install -r requirements.txt
AA_DISABLE_MIDI=1 .venv/bin/python -m unittest discover -s tests -v
python3 tools/uat_headless.py
node --check static/editor.js
AA_DISABLE_MIDI=1 bash run.sh
```

Open <http://localhost:5002>. Omit `AA_DISABLE_MIDI=1` only when live MIDI is
intended. On Windows use `.venv\Scripts\python` and `run.bat`; Windows hardware
behavior has not been tested by this audit. The launchers now retry failed installs
and check changed requirements. No database migration or factory reset is needed.

The audit passed 33 unittest cases, the legacy 47-check private-baseline harness
and 45-check synthetic harness, and a Chromium smoke test. The public snapshot
passes the same unittest suite with one private-capture test skipped. CI runs
Python 3.9/3.12 on Ubuntu 22.04 (pinned after the rolling image dropped 3.9); consult the actual remote workflow result for the release commits.
These checks establish software behavior, not hardware receipt or restore safety.

Optional browser check, with Playwright and Chromium installed:

```bash
python tools/browser_smoke.py
```

That test starts and stops its own temporary server with MIDI disabled.

## Resume hardware work

1. Recover and hash the missing T7 source dumps/photo before repeating isolated
   edits already reported in that session.
2. Capture controlled Operating Mode and Bank Style transitions and reversals.
3. Finish PER-PR/global controller semantics, independent PED2 mapping, filter
   cells/channel regions, remaining command codes, upper Remote Title range,
   and PC display-base behavior.
4. Investigate restore symptoms with recorded MIDI output and complete readback.
   Host sent counts do not establish wire delivery. The device-to-device symptom
   and one reset recovery do not establish a firmware mechanism or universal fix.
5. Keep the meaning of `66 01` open; observed constancy does not rule out a checksum.

The [hardware plan](HARDWARE_TEST_PLAN.md) contains the capture worksheet and
acceptance criteria. Bulk transport and standalone-preset targeting remain
unverified. Do not enable experimental writes simply to complete a software
release. Factory reset is not a routine recovery prerequisite.

## Release workflow

The private repo retains its original history, manual, workbook, and raw evidence.
The public repo receives only reviewed source, current docs, tests and assets.
Neither the private archive nor local dumps are public release material.

```bash
python3 tools/export_public.py /path/to/new-snapshot
```

The destination must not exist. Review it and run the offline tests there. When
synchronizing an existing public checkout, copy only manifest entries and preserve
everything outside the manifest, particularly `.git` and local captures. Use
normal commits and fast-forward pulls; never delete a dumps directory to prepare
an export. Review upstream changes before copying over tracked source files.
