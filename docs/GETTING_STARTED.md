# Getting Started

Use Python 3.9+ and a USB-MIDI interface for live MIDI, or work with files offline.
This is a partial reverse-engineered editor, not a verified restore utility.

## Launch

For the public distribution:

```bash
git clone https://github.com/sungle-spec/rocktron-all-access.git
cd rocktron-all-access
bash run.sh
```

On Windows use `run.bat`. Open <http://localhost:5002>.
The launchers create a virtual environment and install pinned requirements.
The shell launcher refreshes them when `requirements.txt` changes; the Windows
launcher checks them each time. A failed install stops startup and can be retried.
For manual installation use `.venv/bin/python -m pip install -r requirements.txt`
(or the Windows equivalent). Keep the server local: it has no authentication.

For explicit file-only use, `AA_DISABLE_MIDI=1 bash run.sh` avoids constructing
hardware MIDI ports. The Socket.IO browser client is bundled locally. Optional
Google Fonts can fail without preventing operation.

## Back up and edit

1. Connect interface OUT → All Access IN and All Access OUT → interface IN.
   Use the manufacturer's cabling/power guidance; do not infer phantom-power
   wiring from the older editor guide.
2. Select MIDI IN/OUT in the header. For a file-only session, load a `.syx` instead.
3. Click **Read All**, then initiate the dump on the device: `SYSX → DUMP → CTR STORE`.
4. Save the untouched dump and retain it separately. This is a backup of captured
   state, not necessarily factory state or a proven restore point.
5. Edit and save a separate `.syx`. Partial captures are for inspection and cannot
   be promoted to a complete dump just by editing them.

PC slots now use the capture-backed base `0x20`; older versions used `0x24`
and could alter PED1 when saving CH15/CH16. EOX appears as an empty SysEx field
(API `null`); do not replace empty fields with zero to preserve an existing string.
Operating Mode and Bank Style remain unresolved and do not write to the dump.

## Hardware writes are experimental

Bulk and single-preset sends require `AA_ENABLE_EXPERIMENTAL_WRITE=1` and are
otherwise disabled. Opt-in does not validate the message chunk transport or
standalone receive behavior. Host progress is not wire delivery or device acceptance.

A restore session reported blank PR120 and altered globals, including during a
device-to-device restore. The cause is unknown. One reset-then-restore success
was reported; factory reset is destructive and not a guaranteed recovery step.
Retain all dumps and consult the [hardware plan](HARDWARE_TEST_PLAN.md) before
planning any device experiment. Do not treat a backup file as guaranteed rollback.

See the [user manual](USER_MANUAL.md), [audit](AUDIT_REPORT.md), and
[architecture](ARCHITECTURE.md). For a different local port use
`PORT=5003 bash run.sh` on a POSIX shell and open <http://localhost:5003>.
