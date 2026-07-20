# Rocktron All Access — Web Editor User Manual

Welcome. This guide assumes you can plug a USB-MIDI interface into your computer and click around a web page; no MIDI-protocol knowledge is required. If anything below is unclear, the Editor's **About** tab also has a quick reference.

---

## 1. What this editor does

The Rocktron All Access (1994) is a 3 × 5 footswitch MIDI controller that holds 120 presets, 150 songs, and 10 sets. Editing it from the front panel works but is slow — every parameter takes several button presses, and the 14-segment display shows only fragments at a time.

This editor lets you:

- **See your whole rig at a glance.** All 120 preset names, channel mappings, IA switches, songs, and sets are on-screen.
- **Edit fast.** Type a new name, click a checkbox, save. No multi-page menu hunting.
- **Round-trip safely.** When you save, the editor **splices** your edits into the original dump bytes — every other byte is preserved exactly. There's no risk of accidentally rewriting a field we haven't decoded yet.
- **Sanity-check live.** The Virtual Foot Controller mirrors what the real unit would do: press a preset and watch the LCD, switch LEDs, and MIDI activity log to confirm the right PC + CC chain fires.

---

## 2. One-time setup

1. **Hardware.** Connect the All Access to a USB-MIDI interface (any class-compliant unit: M-Audio Uno, Roland UM-One, MOTU FastLane, etc.). The All Access has 7-pin DIN; you can use a 5-pin DIN cable — pins 1 and 3 (the extra ones) just carry phantom power, the MIDI signals are on the standard 4 inner pins.

2. **Power the All Access.** Either via the included 9 V AC adaptor or via phantom power from a compatible Rocktron rack unit.

3. **Start the editor:**
   ```bash
   git clone https://github.com/sungle-spec/rocktron-all-access.git
   cd rocktron-all-access
   ./run.sh         # mac/linux
   run.bat          # windows
   ```
   First run installs Flask, flask-socketio, python-rtmidi into `.venv/`. Afterwards it's instant.

4. **Open the page** in your browser: <http://localhost:5002>

5. **Pick your MIDI ports.** In the header you'll see two dropdowns labeled `IN` and `OUT` with red LEDs next to them. Open the dropdowns, pick your USB MIDI Interface on each side. The LEDs go green when the port is open.

---

## 3. The toolbar

Across the top of the page, after the tab icons:

| Icon | What it does |
|---|---|
| ![Load] **Load .syx** | Opens a file picker. Pick a `.syx` bulk dump from disk — usually one you saved earlier or that came from the device. |
| ![Save] **Save .syx** | Downloads the editor's *current* in-memory dump (with your edits) to `~/Downloads/rocktron-YYYYMMDD-HHMMSS.syx`. Use this as a backup before/after editing. |
| ![Read] **Read All** | Captures a fresh dump from the device. Click this, then on the device do `SYSX → DUMP → CTR STORE`. The editor accumulates frames until the 23-byte end marker, then auto-parses. |
| ![Write] **Write All** (yellow) | Sends the editor's current dump back to the device. **The device must be on `SYSX → LOAD` first.** The transfer is paced at the manual's 1-byte-per-15 ms spec; a full dump takes ~11 minutes. |

**Always** save a backup with **Save .syx** before doing **Write All** — you can roll back if anything looks off.

---

## 4. The tabs

### 4.1 Presets (default tab)

The left column lists all 120 presets. Click one to edit it. The right column has five editor sections:

#### Name (13 chars max)
The on-device title. A-Z, 0-9, and the `.` character (the device renders `.` as a space). Type a new name, click **Save name**.

#### PC slots — 16 channels
Each preset can send a Program Change on each of 16 MIDI channels when recalled. Each cell:
- **CH<n>** + your custom channel name (e.g. `LOOP`, `HEAD`, `DIEZ`)
- **value** field — the PC number to send (0-127, or 1-128 if the device's "Starting Preset" is 1)
- **active** checkbox — when unchecked, that channel slot is "OFF" and no PC is sent on that channel

After editing, click **Save PC slots** to write all 16 in one batch.

#### Instant-access switches — on/off (CTR STORE)
15 toggle pills (SW1-SW15). Click any pill to flip its on/off state. When the preset is recalled, each IA switch's stored on/off state determines whether the device sends the configured ON-value or OFF-value CC for that switch. Click **Save IA bitmap** to commit.

> The actual CC# and channel each IA switch sends are configured in MIDI P2/P3 on the device (and read by the editor in the *Channels & Switches* tab). The IA bitmap here just stores the on/off recall state per preset.

#### Custom MIDI string — up to 5 commands per preset
Each preset can broadcast up to 5 arbitrary MIDI commands when recalled. Per row:
- **CMD<n>** label
- **Type** dropdown — one of `NONE`, `NOFF>` (Note Off), `N ON>` (Note On), `KPRS>` (Key Pressure), `C CH>` (Control Change), `P CH>` (Program Change), and others. The `>` means "needs additional data" — `NONE` and `STOP` etc. don't.
- **Channel** byte — 0-127, what channel to send on
- **Data 1** — note number / CC# / program number depending on type
- **Data 2** — velocity / value (where applicable)

All five CMD slots are editable — the full 18-type list from the manual's CUSTOM P1 page, MIDI channel 1-16, and both data bytes. Click **Save custom MIDI**.

#### SysEx string — 30 bytes + ON/OFF
- The **Send sysex on preset recall** checkbox (top) is the per-preset enable flag. When OFF, the bytes below are stored but not transmitted on recall.
- 30 byte cells (B01..B30), each 0-127.

Click **Save SysEx** to write both the toggle and the bytes.

### 4.2 Global Config

The 6 SETUP / MIDI globals from the manual:

| Field | Manual page | Notes |
|---|---|---|
| **Bank Mode** | SETUP P1 | BANK / SONG / REMOTE — writes, but the on-device effect isn't hardware-confirmed yet |
| **Bank Size** | SETUP P2 | 1, 5, 10, or 15 — determines how many of SW1-15 are preset switches vs IA |
| **Bank Style** | SETUP P3 | Editing is disabled — the byte previously assumed for this turned out to be SW1's switch type; the real location is unknown |
| **MIDI Receive Ch** | MIDI P7 | 1-16, or check OMNI |
| **Starting Preset #** | SETUP P6 | 0 or 1 — what number the first preset shows as |
| **Remote Title #** | SETUP P10 | 0-255, used in REMOTE mode |
| **Program Change Status** | MIDI P5 | OFF / ON / MAP |

Two save options:
- **Save to dump** — splices the values into the dump bytes; you must run **Write All** afterwards to push to device.
- **Save (UI only)** — stores in browser storage; doesn't touch the dump.

### 4.3 Channels & Switches

Edit the 4-character custom names the device shows on its display:

- **MIDI Channels (CH1-CH16)** — usually named after the rack gear (`LOOP`, `HEAD`, `DIEZ`, etc.)
- **Foot Switches (SW1-SW15)** — labels like `VOX1`, `MARS`, `FUZZ`
- **Pedal Inputs (PED1, PED2)**
- **Switch Type** (LATCH / MOMENTARY / HOLD, per switch, MIDI P4 on the device) — writes into the dump via **Save switch types to dump**, separate from the names save.
- **IA Routing** (UI-only) — sets the channel + CC# the *virtual* foot controller transmits when you click an IA switch in the Virtual Foot Controller tab. Doesn't affect the real device.

Click **Save names to dump** to write all names back into the 279-byte name block.

### 4.4 Songs

Each song is a custom bank of presets (SW1-15). The device uses only the first Bank-Size slots (5 at Bank Size 5, 15 at Bank Size 15), but all 15 are stored, so the editor exposes all 15. Inactive slots are dimmed.

1. Pick a song from the **Song** dropdown (1-150).
2. For each of the 15 slots (SW1-15), pick a preset.
3. Click **Save song**.

### 4.5 Sets

Sets organise songs into 50-bank arrangements. 10 sets total.

1. Pick a set from the **Set** dropdown (1-10).
2. For each of the 50 banks, pick a song.
3. Click **Save set**.

### 4.6 PC Map

The 128-row table mapping incoming Program Change numbers to on-device presets. Active when MIDI P5 (Program Change Status) is set to `MAP`.

- Each cell: pick a preset (1-120) or `— off —`.
- **Save PC map** — writes all 128 slots back.
- **Reset to identity** — restores PC1→PR1, PC2→PR2, … (default).

### 4.7 Virtual Foot Controller

A photorealistic on-screen All Access. Mirrors what the real device would do.

- **Click a preset switch** (SW1-5 in default Bank Size 5) — recalls that preset, the LCD updates with the preset name, the editor sends the PC chain on ch1, and the IA switch LEDs update to show that preset's stored IA states.
- **Click an IA switch** (SW6-15) — toggles it on/off, sends the configured CC.
- **BANK +/-** — cycle through the 24 banks (with Bank Size 5).
- **2ND** — toggles secondary functions (visual only).

Below the All Access SVG:

- The **MIDI activity log** shows every PC/CC the editor has sent or received from the device, labelled with the loaded dump's channel names.
- **Expression pedals** — two sliders mirroring PED1/PED2 that send live CC as you drag.
- **Quick send** — fire any single PC or CC (channel / number / value) for testing.

### 4.8 MIDI Monitor

Raw MIDI traffic, including whole-sysex frames as they arrive. Useful for debugging.

### 4.9 About

Quick reference of capabilities + workflow + documentation pointers.

---

## 5. Typical workflows

### 5.1 First-time setup — back up your rig

1. Open the editor, pick your IN and OUT MIDI ports.
2. Click **Read All** (cloud-down icon).
3. On the device: `2ND → SYSX → CTR STORE`. Wait 10 seconds.
4. The editor refreshes: 120 preset names, channel/switch names, songs, sets, globals all populate.
5. Click **Save .syx**. The browser downloads `rocktron-YYYYMMDD-HHMMSS.syx`. Move it somewhere safe — this is your full backup.

### 5.2 Rename a preset

1. Click the preset in the left column.
2. Edit the **Name** field (max 13 chars).
3. Click **Save name** — the change is now in the in-memory dump.
4. To push to device: device on `SYSX → LOAD`, click **Write All**.

### 5.3 Reassign which presets a song plays

1. Go to the **Songs** tab.
2. Pick the song from the dropdown.
3. For each active slot (SW1-15), pick the preset you want.
4. Click **Save song**.
5. **Write All** when ready.

### 5.4 Test a preset without touching the real device

1. Go to **Virtual Foot Controller**.
2. Click a preset switch (SW1-5 in default config).
3. Watch the LCD and the IA switch LEDs update.
4. The on-screen MIDI activity log shows the PC + CC chain that gets sent. If you have a MIDI receiver patched to your OUT port, it will react in real time.

### 5.5 Restore from backup

1. Click **Load .syx**, pick the backup file.
2. The editor parses it; all tabs refresh.
3. **Write All** with the device on `SYSX → LOAD`.

---

## 6. Troubleshooting

**MIDI ports don't show up.** Restart the editor (`./run.sh`) — the rtmidi enumeration sometimes needs a fresh process to see hot-plugged USB MIDI interfaces. Also try unplugging and replugging the USB-MIDI interface.

**"Read All" never completes.** Did you run `SYSX → DUMP → CTR STORE` on the device? The unit only sends a dump when the front panel triggers it; there's no remote dump-request command in the All Access protocol. Also check the IN port LED is green.

**"Write All" produces a Buffer Overflow on the device.** Or only PR1 commits and the rest are ignored. The default pacing now matches the manual's literal "1 byte every 15 ms" spec — chunk size = 1, byte delay = 15 ms, inter-frame pause = 0. If you've changed those values, restore them via Globals tab → **Write All — pacing (advanced)** → set Chunk = 1, Byte delay = 15, Inter-frame = 0 → Apply pacing.

If your USB-MIDI interface adds jitter on top of our timing, raise byte delay to 18-20 ms (slows the dump from ~11 min to ~14 min but tolerates timing slop). Don't raise the inter-frame pause — that causes the device to commit only the first frame and ignore the rest, which manifests as "PR1 restored, PR2+ unchanged".

Full dump time at default pacing: **~11 minutes** (43,647 bytes × 15 ms = 654 s).

**Edits don't persist after Read All.** This is correct — `Read All` always re-reads from the device, overwriting in-memory state. Save your edits with **Save .syx** before reading if you want to keep them.

**Preset name shows a leading space.** The All Access stores 13 characters and pads with spaces. The editor displays them as-is. To remove the leading space, edit the field and re-save.

**Some IA switch LEDs don't light when I click a preset on the Virtual Foot Controller.** With Bank Size = 5, SW1-5 are preset switches and SW6-15 are IAs — the LEDs you see represent IA on/off states. SW6-15 update from the preset's stored bitmap. If a switch has been remapped to a preset slot via Bank Size, it won't show an IA state.

**Switch types (LATCH/MOM/HOLD) now editable.** Fully reverse-engineered as of 2026-07-20 — set them in the Channels & Switches tab under **Switch Type** and click **Save switch types to dump**, then **Write All**.

---

## 7. Limitations / not-yet-supported

| Feature | Reason |
|---|---|
| Per-preset PER-PR overrides for IA channel/CC#/ON/OFF values (UI write) | Byte layout is fully decoded (read-only in the parsed dump); a write-back editor panel hasn't been built yet |
| Operating Mode (BANK/SONG/REMOTE) — on-device effect | The editor writes it, but hardware testing found the target byte didn't change when switching to REMOTE; unconfirmed whether the write path works at all |
| Bank Style (FIRST/CURNT/OFF) | The byte previously assumed for this turned out to be SW1's switch type; real location unknown, editing disabled |
| MIDI Filter mask editing (BLOC/MERG per type per channel) | Message-type cells are resolved; the 16 per-channel cells aren't confirmed yet |
| Remote dump-request from editor | Not in the All Access protocol — must be initiated from the device |
| Realtime preset recall pre-edit (commit-to-device-from-edit-only) | Editor-side splice, not on-device — Write All to push |

For status of each see [REVERSE_ENGINEERING.md](REVERSE_ENGINEERING.md).

---

## 8. Where things live on disk

```
rocktron-all-access/
├── app.py                       Flask backend
├── security_patch.py            Security hardening helpers
├── templates/index.html         Single-page UI
├── docs/
│   ├── GETTING_STARTED.md       Install & first-run guide
│   ├── USER_MANUAL.md           This file
│   ├── REVERSE_ENGINEERING.md   Byte map
│   ├── HARDWARE_TEST_PLAN.md    Remaining device-session test plan
│   ├── MANUAL_SUMMARY.md        Protocol extracts from the manual
│   └── MIDI_SPEC.md             MIDI Implementation Chart
└── tools/
    ├── uat_headless.py          47-check regression harness (no hardware)
    └── diff_dumps.py            Frame-aligned dump diff helper
```

Your own dumps live wherever you save them — the browser downloads to `~/Downloads` and the file picker loads from anywhere.

---

## 9. Acknowledgements

Reverse-engineered from real hardware bulk dumps over 6 iterative capture/diff cycles. The 77-page official Rocktron manual documents features but never publishes the byte-level sysex format; everything in the byte map came from inspecting actual dumps.

MIT license — credit the repo if it saves you a weekend.
