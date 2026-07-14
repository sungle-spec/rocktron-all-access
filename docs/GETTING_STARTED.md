# Getting Started

Get the Rocktron All Access editor running and talking to your unit in about
five minutes.

## What you need

- **Python 3.9+** (`python3 --version` to check)
- A **MIDI interface** connected to the All Access — both directions:
  - interface **OUT → All Access MIDI IN**
  - All Access **MIDI OUT → interface IN**
- The All Access itself (any firmware with the SYSX menu — tested on v1.7)

No MIDI hardware? The editor still runs — you can load, inspect, and edit
`.syx` dump files offline and save the result.

## 1. Install & launch

```bash
git clone https://github.com/sungle-spec/rocktron-all-access.git
cd rocktron-all-access
./run.sh          # Windows: run.bat
```

The first launch creates a local `.venv` and installs the three dependencies
(Flask, Flask-SocketIO, python-rtmidi). When you see:

```
Rocktron All Access editor — http://localhost:5002
```

open **http://localhost:5002** in your browser.

> The server binds to `127.0.0.1` only — nothing is exposed to your network.

## 2. Connect MIDI

In the header bar, pick your interface in the **IN** and **OUT** dropdowns.
The dots next to each turn green when the port opens.

## 3. Get a dump into the editor

Two ways:

- **From the device (recommended first step):** click **Read All**
  (the down-arrow icon), then on the All Access run `SYSX → SEND/DUMP`.
  The editor captures all 145 frames (~44 KB) and populates every tab.
- **From a file:** click **Load .syx** (folder icon) and pick a `.syx`
  bulk-dump file with the file picker.

**Immediately click Save .syx** (floppy icon) after your first Read All —
that download is your factory-state backup. Keep it somewhere safe.

## 4. Make your first edit

1. Open the **Presets** tab and click a preset.
2. Rename it, or toggle a PC slot / IA switch state.
3. Click **Save** in the preset detail panel — this splices your change into
   the in-memory dump (every other byte is preserved exactly).

## 5. Write it back to the device

1. On the All Access: `SYSX → LOAD` (the unit waits for incoming sysex).
2. In the editor: click **Write All** (yellow upload icon).
3. Wait — a full dump takes ~11 minutes. The pacing is hard-locked to the
   manual's 1-byte-per-15 ms spec to avoid the unit's Buffer Overflow error.
   A progress bar tracks the transfer; the terminal running the server logs
   progress too.

When it finishes, the unit returns to normal operation with your edits live.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `pip` fails building **python-rtmidi** | Install a C compiler first (macOS: `xcode-select --install`; Windows: "Desktop development with C++" from VS Build Tools; Linux: `sudo apt install build-essential libasound2-dev`). Then delete `.venv/` and rerun `run.sh`. |
| Editor runs but says **python-rtmidi: no** | MIDI features are disabled but file editing still works. Fix the install above to enable hardware I/O. |
| My interface isn't in the dropdowns | Plug it in, then wait a few seconds — the port list refreshes every 4 s. Check no other app (DAW) holds the port exclusively. |
| **Read All stalls** partway | Check the All Access MIDI OUT → interface IN cable. The editor flags a partial capture and blocks Write All so you can't burn an incomplete dump. |
| Device shows **Buffer Overflow** during Write All | Shouldn't happen at stock pacing. Make sure nothing else is sending MIDI to the unit at the same time. |
| Port 5002 already in use | `PORT=5003 ./run.sh` and open `http://localhost:5003`. |

## Next steps

- [USER_MANUAL.md](USER_MANUAL.md) — every tab and workflow in detail
- [REVERSE_ENGINEERING.md](REVERSE_ENGINEERING.md) — the byte-level dump format
- [MANUAL_SUMMARY.md](MANUAL_SUMMARY.md) / [MIDI_SPEC.md](MIDI_SPEC.md) —
  protocol notes extracted from the original 1994 manual
