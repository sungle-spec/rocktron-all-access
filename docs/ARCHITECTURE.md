# Architecture

The editor runs as a local, single-process Flask application. All clients share
one in-memory document. Closing the process discards unsaved edits. File saves
are browser downloads, not writes back to the source filename.

## Source layout

| Path | Responsibility |
|---|---|
| `rocktron/codec.py` | Pure parse/splice functions and field constants; no Flask or MIDI imports |
| `rocktron/validation.py` | Supported framing, byte limits, command checks, canonical full-dump order |
| `app.py` | HTTP/Socket.IO endpoints, current document, capture aggregation, MIDI bridge |
| `security_patch.py` | Local Host/Origin/Referer guards, optional session token check, body limit, JSON errors |
| `templates/index.html` | Markup, controller SVG, existing inline event bindings |
| `static/editor.css` | Extracted editor styles |
| `static/editor.js` | Classic browser script; hydration, renderers, API calls, virtual controller |
| `static/vendor/` | Socket.IO 4.7.5 browser client and its MIT license, served locally |
| `tests/` | Real Flask-client tests, protocol regression tests, safe-export tests |
| `tools/uat_headless.py` | Legacy pure-codec regression harness; no framework stubs or hardware |
| `tools/analyze_captures.py`, `tools/diff_dumps.py` | Shared structural parser plus corpus/aligned-frame reports |
| `tools/export_public.py` | Explicit public-source manifest, exports into a new directory only |
| `docs/archive/` | Historical plans and reviews, preserved with supersession notices |
| `reference/` | Private capture evidence and reference material; excluded from public exports |

`app.py` re-exports codec names for compatibility with earlier integrations.
New code should import `rocktron.codec` directly. The legacy security module name
is retained for compatibility; it no longer contains path-based file reading or
an unused server-log-path feature.

## State and concurrency

`STATE_LOCK` is re-entrant. A mutating API request holds it from validation through
commit, preventing concurrent read-modify-write operations from losing edits.
Incoming capture callbacks also hold this lock when changing document state.

`MIDI_LOCK` serializes hardware output and port changes. Bulk workers take an
immutable byte snapshot and retain the selected output handle. While a worker owns
the output, another write, virtual-controller send, or port change is refused.
Progress and errors remain visible via `/api/write/status` after a browser reconnect.
A completed worker means host calls returned; it does not mean the device received
or stored the data.

## Validation and file editing

The inspection path accepts structurally valid incomplete dumps containing a
preset. Full completeness requires canonical frame order, header and command
checks, payload bytes below `0x80`, and no discarded prefix/trailing bytes.
Every reparse recomputes completeness, so editing a partial capture cannot make
it writable. Preset identities in partial positional captures can be ambiguous.

Edits splice selected bytes into the original stream. Omitted PC-map and name
entries are preserved. `null` song/set entries mean preserve, not OFF. SysEx
`null` values represent EOX companion flags; untouched flagged bytes survive a
save. Unknown switch types and command bytes are retained instead of assigned
plausible defaults. Operating Mode and Bank Style remain unconfirmed and their
HTTP writes are rejected.

## Hardware boundary

`AA_DISABLE_MIDI=1` avoids constructing MIDI ports, for tests and offline work.
Bulk and single-preset sends require `AA_ENABLE_EXPERIMENTAL_WRITE=1`.
The existing one-byte RtMidi transport remains experimental: sleeping between
message API calls does not establish physical byte pacing. Single-preset targeting
is unresolved because the dump is position-based. The refactor does not introduce
a transport workaround or a factory-reset command.

Live virtual-controller MIDI is separate from dump writing and remains available
when a valid output is selected. Optional Google Fonts may be fetched for styling;
the Socket.IO client is local so network loss does not prevent editor startup.

## Verification

Use the project environment with dependencies installed:

```bash
.venv/bin/python -m unittest discover -s tests -v
python3 tools/uat_headless.py
python3 tools/analyze_captures.py path/to/baseline.syx path/to/capture.syx
node --check static/editor.js
```

CI runs the Python suite, legacy harness, and JavaScript syntax check on Python
3.9 and 3.12 on pinned Ubuntu 22.04 (the rolling Ubuntu 26.04 image does not
provide Python 3.9). Actual UI behavior and hardware acceptance need separate checks.
The audit includes a local Chromium smoke test with MIDI disabled; this is not
physical-device verification.
