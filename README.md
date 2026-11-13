# Rocktron All Access — Web Editor + Virtual Foot Controller

A local Flask + Socket.IO editor for inspecting and editing Rocktron All Access
bulk-dump files, with live MIDI monitoring and a virtual foot controller.
The protocol map is partial and hardware write acceptance remains unresolved.

Start with [Getting Started](docs/GETTING_STARTED.md) and the
[user manual](docs/USER_MANUAL.md). See the [audit](docs/AUDIT_REPORT.md),
[architecture](docs/ARCHITECTURE.md), [byte map](docs/REVERSE_ENGINEERING.md),
and [hardware evidence plan](docs/HARDWARE_TEST_PLAN.md).

## Run

Python 3.9+ is the documented baseline. From this checkout:

```bash
bash run.sh
# Windows:
run.bat
```

The launchers create `.venv` and install `requirements.txt` when the environment
is absent. The shell launcher also refreshes requirements when the requirements file changes;
the Windows launcher checks them each time. Failed installations stop startup. Open
<http://localhost:5002>. The default bind is loopback; there is no user authentication.
Do not expose the service publicly. The public distribution is
[rocktron-all-access](https://github.com/sungle-spec/rocktron-all-access).
The public source is synchronized from a reviewed allowlist; private history
and hardware evidence stay in the private working repo.

## File editing and live controls

- Load a `.syx` file or capture a device-initiated full dump with **Read All**.
  Save an untouched backup before editing. A backup is not proof that restoration works.
- Edit preset names, 16 PC slots, IA state, five Custom MIDI slots, and SysEx
  strings; edit names, songs (150 × 15 slots), sets (10 × 50 banks), and PC map.
- Some globals have candidate or reported mappings. Operating Mode decodes
  `null` with raw/candidate data and ignores writes. Bank Style remains unknown
  and ignores writes. Unknown switch types remain `null`, not LATCH.
- SysEx EOX uses `null` in the API and an empty browser field. No-op saves preserve
  EOX companion flags and stored payload; entering a number clears that slot's flag.
- The virtual controller sends live MIDI to selected outputs. It is not verified
  as a complete emulation of every device mode or custom-message behavior.
- Strict validation checks supported framing and canonical order. It cannot prove
  an unknown checksum, correct field semantics, wire delivery, or device acceptance.

**Critical PC-map correction:** PC slots start at `0x20`, not `0x24`.
The tracked T1 ascending capture directly supports `0x20..0x3F`. The old base
made CH15/CH16 writes overwrite PED1 configuration. Display-number-base semantics
remain unverified. See the [evidence map](docs/REVERSE_ENGINEERING.md).

## Experimental hardware writes

Bulk and single-preset dump sends are disabled by default. Explicit opt-in uses
`AA_ENABLE_EXPERIMENTAL_WRITE=1`; this enables an experiment, not a validated
restore path. The message-fragment transport is unverified across RtMidi backends.
Host sent counts and progress do not prove MIDI wire delivery or receipt.
Standalone-preset receive/addressing behavior is unknown.

A session report describes blank PR120 and altered globals after bulk restores.
A similar device-to-device symptom excludes this app as the sole explanation but
does not prove a firmware cause. One factory-reset-then-restore recovery was
reported; it is not a universal workaround. No flush/next-frame mechanism is
established. Factory reset is destructive; it is not a routine install or recovery
step here. CUSTOM unresponsiveness was reported during REMOTE mode; causality
remains unproven. See the [hardware plan](docs/HARDWARE_TEST_PLAN.md).

## Tests and tools

```bash
AA_DISABLE_MIDI=1 .venv/bin/python -m unittest discover -s tests -v
PYTHONDONTWRITEBYTECODE=1 python3 tools/uat_headless.py
PYTHONDONTWRITEBYTECODE=1 python3 tools/uat_headless.py reference/baseline-pre-RE.syx
node --check static/editor.js
```

The legacy harness imports the pure codec; it does not exercise HTTP routes.
Its no-edit check reconstructs the stream from parsed raw frames. Counts depend
on selected fixtures and are not a release guarantee. `tests/test_protocol.py`
covers framing, unknown preservation, EOX, and literal PC capture/isolation.
Actual API tests are a separate layer in `tests/test_api.py`; CI runs offline checks.
Neither layer proves hardware acceptance. Synthetic fixtures must use canonical
frame order for strict validation.

`tools/diff_dumps.py` compares aligned frames; `tools/analyze_captures.py` runs
selected corpus/anchor checks, not every documented claim. Both have limitations
recorded in the [audit](docs/AUDIT_REPORT.md).

## Reference data and public export

This private working tree tracks raw `.syx` dumps, a Rocktron manual PDF, and a
personal workbook. T7 raw captures and the referenced photo are missing. Historical
Git commits retain personal author information, paths, and deleted rig exports.
Nothing in this documentation change sanitizes history or author metadata.

Public export must use an explicit allowlist reviewed for code, docs, assets,
and licensing. Do not copy the whole tree, `.git`, `reference/`, environments,
or private captures. Do not delete dumps to prepare an export. Use `python3 tools/export_public.py /path/to/new-snapshot`; the destination
must not exist. Review its manifest and output before publication.
MIT covers project-authored material; it does not establish redistribution rights
for the manual, workbook, or reference data.

Historical working plans are in private `docs/archive/` and are not current
procedures; the public snapshot deliberately excludes that directory. Manual references: [summary](docs/MANUAL_SUMMARY.md),
[MIDI chart](docs/MIDI_SPEC.md).

## Support and license

[Buy me a coffee](https://buymeacoffee.com/sungle.spec).
Project-authored code is under [MIT](LICENSE), subject to the reference-material
qualification above.

Resume notes and release instructions: [Handover](docs/HANDOVER.md).

Optional UI regression test (install Playwright separately):

```bash
python tools/browser_smoke.py
```

It starts and stops its own temporary server with MIDI disabled. The app now
serves the Socket.IO client locally. Optional Google Fonts affect styling only.
