# Rocktron All Access — Web Editor + Virtual Foot Controller

Web-based editor for the Rocktron All Access MIDI foot controller (1994; 18 switches / 2 pedals / 16-channel MIDI). Runs a small Flask + Socket.IO + python-rtmidi server on your laptop, serves a navy-blue metallic single-page UI at <http://localhost:5002>, and talks to the hardware over your OS's standard MIDI ports.

**New here? Start with [docs/GETTING_STARTED.md](docs/GETTING_STARTED.md).**

## Features

### MIDI / device I/O
- **In/Out port dropdowns** in the top bar with green/red connection LEDs, auto-refreshing every 4 s so a freshly-plugged USB-MIDI interface appears without a page reload.
- **Load .syx** — pick a bulk-dump file with the browser file picker.
- **Save .syx** — downloads the current in-memory dump (with your edits spliced in) to your `Downloads` folder as `rocktron-YYYYMMDD-HHMMSS.syx`.
- **Read All** — arms a capture session; trigger from the device (`SYSX → DUMP → CTR STORE`). The editor accumulates the 145 frames and auto-parses when the 23-byte end-of-dump marker lands.
- **Write All** — sends the current dump back, byte-by-byte at the manual's literal "1 byte every 15 ms" rate (~11 minutes for a full 43 KB dump). Live progress in the toast + server log. **Refuses to write a partial/corrupt dump** (validates the exact 145-frame multiset first).
- **Partial-dump banner** — load tags the dump as `complete: true/false`; a yellow banner appears and disables Write All if the capture is incomplete.
- **Server-side write logging** — Write All progress is logged to the terminal running the server, so you can watch a transfer even if the browser tab loses focus.

### Per-preset editing (Presets tab)

Click any of the 120 presets in the left list to open the workbench:

| Editor | What it covers |
|---|---|
| **Name** | 13 characters (A-Z, 0-9, `.` renders as space). |
| **PC slots — 16 channels** | Per-channel value (0-127) + active/OFF toggle, with channel-name labels (LOOP, HEAD, DIEZ, …). Save in one click. |
| **IA bitmap** | All 15 IA switches (SW1-15) as toggle pills. Click to flip on/off, save. |
| **Custom MIDI** | **All 5 CMD slots writable** — full 18-type list, channel 1-16, data1/data2. Layout (5 × 8-byte slots at 0xCE) confirmed against every preset of a real bulk dump plus two hardware write-captures; no overlap with the IA bitmap or SysEx toggle. |
| **SysEx string** | 30-byte payload + per-preset ON/OFF toggle. |

### Globals (Global Config tab)

Editable + write-back to dump:
- Operating Mode (BANK / SONG / REMOTE)
- Bank Size (1, 5, 10, 15)
- Bank Style (FIRST / CURNT / NONE)
- MIDI Receive Channel (1-16 + OMNI)
- Starting Preset Number
- Remote Title # (0-255)
- Program Change Status (OFF / ON / MAP)
- **Write All pacing** (chunk size / byte delay / inter-frame pause) — defaults match the manual's spec.

### Channels & Switches tab

- 16 channel names (CH1-CH16) with custom 4-char labels.
- 15 switch names (SW1-SW15).
- 2 pedal names (PED1, PED2).
- IA Routing reference table (channel + CC# the *virtual* foot controller transmits per IA switch).
- **Save names to dump** — splices new names back into the 279 B name block.

### Songs / Sets / PC Map tabs

- **Songs** — pick from 150 songs, edit each song's 10 preset slots.
- **Sets** — pick from 10 sets, assign one of the 150 songs to each of the 50 banks.
- **PC Map** — 128-row table mapping incoming PC# → preset (for use when MIDI P5 = MAP).

### Virtual Foot Controller tab

- **All Access SVG** — anodised-blue chassis with chrome-domed footswitches, glowing cyan **14-segment** LCD that spells out preset names with proper diagonal letters (`M`, `N`, `V`, `W`, `K`, `X`, `Y`, `Z`, `/`), red footswitch LEDs, gold "All Access" italic-serif branding, robotic "ROCKTRON" wordmark.
  - Top row = 2ND, middle = BANK +, bottom = BANK -.
  - SW1..SW(bank-size) = preset switches in the current bank.
  - Clicking a preset switch recalls the preset, lights its LED, and **updates the IA switch LEDs from the preset's stored on/off bitmap**.
  - SW(bank-size+1)..SW15 = instant-access switches with the IA name from the dump (VOX1, DIEZ, MARS, …).
- **Expression pedals** — two sliders mirroring PED1/PED2, sending live CC as you drag.
- **Quick send** — fire any single PC or CC (channel / number / value) for testing a rig connection.

### MIDI activity log

A 240 px scrolling text log below the controller captures every PC/CC the editor sends and that comes back from the device, newest event on top. Channels are labelled with the loaded dump's own channel names:

```
22:55:49.260  RX  CH2  CC#12 DIEZ ON · AS4 (CH2)    127
22:55:49.252  TX  CH2  CC#12 DIEZ ON (AS4 (CH2))    127
22:55:49.408  TX  CH0  BANK + Bank 2/24 (PR006-010)
```

## Reverse-engineering status

After 7 capture/diff cycles (T0-T6), every editable field documented in the manual is decoded. Field-by-field:

| Field                                       | Decode | Edit | Write-back to device |
|---------------------------------------------|:------:|:----:|:---:|
| Preset name (13 chars)                      | ✅ | ✅ | ✅ |
| 16 PC slots per preset                      | ✅ | ✅ | ✅ |
| IA on/off bitmap (SW1-15)                   | ✅ | ✅ | ✅ |
| Custom MIDI (CMD1-CMD5, full type enum)     | ✅ | ✅ | ✅ (layout re-derived offline from the capture corpus; 3 of 18 type codes hardware-observed, rest follow the manual's list order) |
| SysEx string (30 bytes) + ON/OFF toggle     | ✅ | ✅ | ✅ |
| Channel / switch / pedal names (4 chars)    | ✅ | ✅ | ✅ (splice into name block) |
| Songs (150 songs × 10 preset slots)         | ✅ | ✅ | ✅ |
| Sets (10 sets × 50 banks → song)            | ✅ | ✅ | ✅ |
| PC Map (incoming PC1..128 → preset, incl. clearing to OFF) | ✅ | ✅ | ✅ |
| Operating Mode / Bank Size / Bank Style     | ✅ | ✅ | ✅ |
| MIDI Receive Channel (incl. OMNI)           | ✅ | ✅ | ✅ |
| Remote Title Number                         | ✅ | ✅ | ✅ |
| Program Change Status (OFF/ON/MAP)          | ✅ | ✅ | ✅ |
| Switch Type (LATCH/MOM/HOLD per IA)         | ✅ | ⛔ | ⛔ — stack-style indexing not pinned |
| Per-preset PER-PR override block            | partial — slot table + CC#/ON/OFF decoded, channel byte located at 0x40 | ⛔ | ⛔ pending slot-semantics capture |
| MIDI Filter mask (per type / per channel)   | partial | ⛔ | ⛔ |
| Tail "66 01" before final F7                | confirmed constant — not a checksum | n/a | n/a |

See [docs/REVERSE_ENGINEERING.md](docs/REVERSE_ENGINEERING.md) for the byte-level map.

## Security & robustness

The editor streams SysEx straight to expensive hardware and has no auth, so it is locked down for the obvious threat model — *a web page you visit in the same browser quietly talking to localhost*. See [`security_patch.py`](security_patch.py).

- **Loopback bind by default** — `127.0.0.1:5002`. Set `AA_BIND=0.0.0.0` only if you put auth in front of it.
- **Socket.IO CORS** locked to `http://localhost:5002` / `http://127.0.0.1:5002` (was `*`).
- **Per-session CSRF token** issued in the `X-AA-CSRF` response header; required back on every state-changing `/api/*` POST. The fetch shim in `templates/index.html` echoes it transparently.
- **Origin check** — cross-origin POST/PUT/DELETE/PATCH returns 403.
- **Upload-only dump loading** — `/api/dump/load` accepts browser-uploaded bytes only (capped at 1 MB of hex); the server never reads files from a client-supplied path.
- **Random per-launch `SECRET_KEY`** (was a hard-coded string). Override with `AA_SECRET_KEY`.
- **MIDI init guard** — a failed rtmidi port construction falls back to file-only mode instead of crashing the app on launch.
- **Dump validation** — `validate_dump()` enforces the exact 145-frame multiset before Write All; partial captures are rejected with a clear error.

## Testing

`tools/uat_headless.py` is a 31-check regression harness that imports `app.py`'s real parse/encode functions and exercises every editing endpoint as a byte-level file round-trip. **No hardware, no browser, no server required.** Wire it into CI.

```bash
.venv/bin/python tools/uat_headless.py path/to/your-full-dump.syx
# -> RESULT: 31/31 checks passed
```

(No dump handy? Run it with no argument — it synthesizes a structurally-valid 145-frame dump and runs the same checks.)

Includes a regression test that a CMD5 save leaves the SysEx ON/OFF toggle (0xF6) untouched — guarding against the historical Custom-MIDI/SysEx-toggle corruption ever returning.

`tools/analyze_captures.py` is the offline RE evidence tool: point it at a baseline dump (plus optional capture files) and it re-derives and asserts every documented byte-layout claim against the data.

## Documentation

| Doc | Audience | Purpose |
|---|---|---|
| [docs/GETTING_STARTED.md](docs/GETTING_STARTED.md) | end users | install, first launch, first Read All / Write All, troubleshooting |
| [docs/USER_MANUAL.md](docs/USER_MANUAL.md) | end users | 9-section walkthrough — installation, every tab, common workflows, troubleshooting |
| [docs/REVERSE_ENGINEERING.md](docs/REVERSE_ENGINEERING.md) | developers | byte-level field map of the bulk-dump format |
| [docs/MANUAL_SUMMARY.md](docs/MANUAL_SUMMARY.md) | reference | extracts from the 77-page Rocktron manual |
| [docs/MIDI_SPEC.md](docs/MIDI_SPEC.md) | reference | the official MIDI Implementation Chart |

## Running

```bash
# Mac / Linux
./run.sh

# Windows
run.bat
```

The first run creates `.venv/` and installs `flask`, `flask-socketio`, `python-rtmidi`, `simple-websocket`. Open <http://localhost:5002>.

## Using it with a real device

1. Connect the All Access via USB-MIDI interface (the unit has 7-pin DIN in/out with phantom-power; a standard 5-pin MIDI interface works fine — the 7-pin extra pins just carry power).
2. Pick your **IN** and **OUT** ports from the dropdowns in the header. Both LEDs go green when ports are open.
3. **Read All** (cloud-down icon) — on the device do `2ND → SYSX → CTR STORE`. The 145 frames stream in; the editor parses + refreshes when the end-of-dump marker lands.
4. Edit any field in any tab. Each section's **Save** button splices your changes into the loaded dump bytes (non-destructive — every other byte preserved).
5. **Save .syx** (floppy icon) — downloads a backup copy of the in-memory dump.
6. **Write All** (yellow upload icon) — put the device on `SYSX → LOAD`, click Write All. Live progress in the toast + the server terminal. ~11 minutes for a full dump at the default rate (1 byte / 15 ms / 0 inter-frame).
7. **Power-cycle the device after Write All** — names + globals appear to be RAM-cached on this firmware and only refresh on boot.

### Tools

`tools/diff_dumps.py` — frame-aligned dump diff helper. Usage:

```bash
.venv/bin/python tools/diff_dumps.py baseline.syx edited.syx
```

Reports per-frame byte-level diffs with offset clusters — invaluable for further reverse engineering.

### Reference data

The byte map was reverse-engineered and validated against a real All Access unit over 7 targeted capture/diff cycles (T0–T6). The raw hardware captures are not distributed with the repo — capture your own baseline with **Read All → Save .syx** before editing anything.

## Acknowledgements

- Reverse-engineered from real hardware bulk dumps plus 7 targeted capture/diff cycles (T0-T6). The 77-page official manual documents features but never publishes the byte-level sysex format — every offset in the byte map came from inspecting actual dumps.
- Rocktron's MMA manufacturer ID `00 00 29` confirmed via the dump header.
- Foot-controller SVG hand-traced from a photo of the device; hit-targets bound to the editor's click handlers.

## Support

If this editor saved your All Access from the junk drawer, you can [buy me a coffee](https://buymeacoffee.com/sungle.spec) ☕ — it keeps the reverse engineering going.

## License

MIT — do what you want with it. Credit the repo if it saves you a weekend.
