"""
Rocktron All Access — Web Editor + Virtual Foot Controller

Run:  python app.py  →  http://localhost:5002
Mac:  pip3 install flask flask-socketio python-rtmidi
Win:  pip  install flask flask-socketio python-rtmidi

Protocol status (reverse-engineered — see docs/REVERSE_ENGINEERING.md):
  Manufacturer ID:  00 00 29 (Rocktron)
  Device ID:        08 (All Access)
  Bulk dump:        145 sysex frames per full dump
    • Frame 0         cmd 2A, 309 B  preset 1 (start-of-dump marker)
    • Frames 1..119   cmd 2B, 309 B  presets 2..120
    • Frames 120..122 cmd 2B, mixed  global tables (PC map, misc)
    • Frame 123       cmd 2B, 279 B  channel+switch NAME block
    • Frames 124..133 cmd 2B, 457 B  songs (15 songs × 30 B each, 10 blocks)
    • Frames 134..143 cmd 2B, 107 B  sets  (50 banks × 2 B each, 10 blocks)
    • Frame 144       cmd 2B, 23 B   tail globals + constant trailer of unknown meaning
  Payload fields have per-field companion-byte semantics (status/EOX/etc.).
  Do not assume a universal ASCII or MIDI packing convention.
"""
import os
import threading
import time
from collections import deque

from flask import Flask, jsonify, request, send_from_directory, Response, g
from werkzeug.exceptions import BadRequest
from flask_socketio import SocketIO, emit

try:
    if os.environ.get("AA_DISABLE_MIDI") == "1":
        raise ImportError("MIDI disabled by environment")
    import rtmidi
    HAS_RTMIDI = True
except ImportError:
    rtmidi = None
    HAS_RTMIDI = False

# Keep the historical app.py codec imports available to external tools.
from rocktron.codec import *  # noqa: F403

# ── MIDI I/O wrapper ─────────────────────────────────────────────────────────
class MidiBridge:
    """Thin wrapper around python-rtmidi that emits socket.io events."""

    def __init__(self, socketio):
        self.sio = socketio
        self.in_port = self.out_port = None
        self.in_name = self.out_name = None
        self._sysex_buf = bytearray()
        self._in_sysex = False
        self._monitor = deque(maxlen=500)
        self._monitor_lock = threading.Lock()
        # rtmidi can fail to construct on headless boxes (no ALSA), busy
        # CoreMIDI servers, or fresh installs without build tools. Treat any
        # failure the same as `not HAS_RTMIDI` so the editor still loads for
        # file-only work — exactly what HAS_RTMIDI was meant to enable.
        if HAS_RTMIDI:
            try:
                self.in_port  = rtmidi.MidiIn()
                self.out_port = rtmidi.MidiOut()
                self.in_port.ignore_types(sysex=False, timing=True, active_sense=True)
                self.in_port.set_callback(self._on_midi_in)
            except Exception as e:
                print(f"[MIDI] init failed ({e}); running file-only.", flush=True)
                self.in_port = self.out_port = None

    def list_ports(self):
        if not self.in_port or not self.out_port:
            return {'in': [], 'out': [], 'error': 'MIDI unavailable (file-only mode)'}
        try:
            return {
                'in': self.in_port.get_ports(),
                'out': self.out_port.get_ports(),
                'selected_in': self.in_name,
                'selected_out': self.out_name,
            }
        except Exception as e:
            return {'in': [], 'out': [], 'selected_in': None, 'selected_out': None,
                    'error': str(e)}

    def open_in(self, name):
        if not HAS_RTMIDI or self.in_port is None:
            return False
        ports = self.in_port.get_ports()
        if name not in ports:
            raise ValueError('unknown MIDI input port')
        self.in_name = None
        self.in_port.close_port()
        self.in_port = rtmidi.MidiIn()
        self.in_port.ignore_types(sysex=False, timing=True, active_sense=True)
        self.in_port.set_callback(self._on_midi_in)
        ports = self.in_port.get_ports()
        if name not in ports:
            raise ValueError('MIDI input port disappeared')
        self._sysex_buf.clear()
        self._in_sysex = False
        self.in_port.open_port(ports.index(name))
        self.in_name = name
        return True

    def open_out(self, name):
        if not HAS_RTMIDI or self.out_port is None:
            return False
        ports = self.out_port.get_ports()
        if name not in ports:
            raise ValueError('unknown MIDI output port')
        self.out_name = None
        self.out_port.close_port()
        self.out_port.open_port(ports.index(name))
        self.out_name = name
        return True

    def send(self, msg_bytes):
        if not MIDI_LOCK.acquire(blocking=False):
            return False
        try:
            if not HAS_RTMIDI or self.out_port is None or not self.out_name:
                return False
            self.out_port.send_message(list(msg_bytes))
            self._log_event('tx', msg_bytes)
            return True
        except Exception as e:
            self._log_event('err', str(e).encode())
            return False
        finally:
            MIDI_LOCK.release()

    def send_throttled(self, msg_bytes, chunk_size=1, byte_delay_s=0.015):
        """Legacy experimental byte pacing; not a verified SysEx transport."""
        if os.environ.get('AA_ENABLE_EXPERIMENTAL_WRITE') != '1':
            return False
        if chunk_size != 1 or byte_delay_s != 0.015:
            return False
        if not MIDI_LOCK.acquire(blocking=False):
            return False
        try:
            if not HAS_RTMIDI or self.out_port is None or not self.out_name:
                return False
            for b in msg_bytes:
                self.out_port.send_message([b])
                time.sleep(0.015)
            return True
        except Exception as e:
            self._log_event('err', str(e).encode())
            return False
        finally:
            MIDI_LOCK.release()

    def _on_midi_in(self, event, _data=None):
        msg, _delta = event
        # accumulate sysex
        if msg and msg[0] == 0xF0:
            self._in_sysex = True
            self._sysex_buf = bytearray(msg)
        elif self._in_sysex:
            self._sysex_buf.extend(msg)
        else:
            self._log_event('rx', bytes(msg))
            return
        if len(self._sysex_buf) > 500_000:
            self._sysex_buf.clear()
            self._in_sysex = False
            self._log_event('err', b'incoming SysEx exceeds size limit')
            return
        if self._sysex_buf and self._sysex_buf[-1] == 0xF7:
            payload = bytes(self._sysex_buf)
            self._in_sysex = False
            self._sysex_buf = bytearray()
            self._log_event('rx-sysex', payload)
            self.sio.emit('sysex-recv', {'hex': payload.hex(),
                                         'len': len(payload)})
            # Forward to capture-mode aggregator if active
            with STATE_LOCK:
                cb = STATE.get('capture_cb')
            if cb:
                cb(payload)

    def _log_event(self, kind, data):
        entry = {
            't': time.time(),
            'kind': kind,
            'hex': data.hex() if isinstance(data, (bytes, bytearray)) else data,
            'len': len(data) if isinstance(data, (bytes, bytearray)) else 0,
        }
        with self._monitor_lock:
            self._monitor.append(entry)
        try:
            self.sio.emit('monitor', entry)
        except Exception:
            pass

    def recent_monitor(self, n=200):
        with self._monitor_lock:
            return list(self._monitor)[-n:]

# ── app state ────────────────────────────────────────────────────────────────
STATE = {
    'dump': None,          # parsed dump dict
    'dump_bytes': None,    # raw bytes for writeback
    'loaded_from': None,
    'global_config': {     # seeded from manual defaults + MUSE spreadsheet
        'bank_mode': 'BANK',
        'bank_size': 5,
        'bank_style': 'FIRST',
        'midi_rx_channel': 1,
        'preset_start': 1,
    },
    'virtual_fc': {
        'bank': 0,
        'sw_state': [False]*15,
        'ped1': 0,
        'ped2': 0,
    },
    'capture_cb': None,        # callback while capture mode is active
    'capture_frames': [],      # accumulated raw sysex frames during capture
    'capture_target': None,    # 'all' | 'preset:N'
}
STATE_LOCK = threading.RLock()
# Held for an entire output operation, including the full background bulk job.
MIDI_LOCK = threading.Lock()
WRITE_STATUS = {'busy': False, 'kind': None, 'done': False, 'error': None}
WRITE_WARNING = ('Experimental, unverified transport: single-byte RtMidi calls are not '
                 'complete SysEx messages and may be rejected or misrouted by the backend. '
                 'Device receipt and single-preset targeting are not verified.')


def _parse_validated(buf):
    from rocktron.validation import validate_dump, FrameValidationError
    validate_dump(buf, strict=False)
    parsed = parse_dump(buf)
    try:
        validate_dump(buf, strict=True)
        parsed['complete'] = True
    except FrameValidationError:
        parsed['complete'] = False
    return parsed


def _json_object():
    d = request.get_json(force=True)
    if not isinstance(d, dict):
        raise ValueError('JSON body must be an object')
    return d


def _integer(value, low, high, field):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f'{field} must be an integer in {low}..{high}')
    return value


def _array(value, maximum, field):
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError(f'{field} must be an array of at most {maximum} items')
    return value


def _text(value, maximum, field):
    if not isinstance(value, str) or len(value) > maximum or not value.isascii() or any(ord(c) < 32 for c in value):
        raise ValueError(f'{field} must be printable ASCII of at most {maximum} characters')
    return value


def _validate_fields(d):
    endpoint = request.endpoint
    if endpoint == 'dump_load':
        if not isinstance(d.get('hex'), str):
            raise ValueError('hex must be a string')
        if 'filename' in d: _text(d['filename'], 255, 'filename')
    elif endpoint == 'midi_open':
        if not any(k in d for k in ('in_port', 'out_port')):
            raise ValueError('in_port or out_port required')
        for k in ('in_port', 'out_port'):
            if k in d and d[k] is not None and (not isinstance(d[k], str) or not d[k]):
                raise ValueError(f'{k} must be a nonempty port name or null')
    elif endpoint == 'preset_rename':
        _text(d.get('name'), NAME_CHARS, 'name')
    elif endpoint == 'preset_pc':
        for slot in _array(d.get('slots'), 16, 'slots'):
            if not isinstance(slot, dict): raise ValueError('slot must be an object')
            _integer(slot.get('ch'), 1, 16, 'ch')
            _integer(slot.get('value'), 0, 127, 'value')
            if 'active' in slot and type(slot['active']) is not bool: raise ValueError('active must be boolean')
    elif endpoint == 'preset_ia':
        bits = _array(d.get('bits'), 15, 'bits')
        if len(bits) != 15 or any(type(v) is not bool for v in bits): raise ValueError('bits must contain 15 booleans')
    elif endpoint == 'preset_cmd':
        for cmd in _array(d.get('cmds'), 5, 'cmds'):
            if not isinstance(cmd, dict): raise ValueError('command must be an object')
            _integer(cmd.get('idx'), 1, 5, 'idx')
            if 'type_byte' in cmd: _integer(cmd['type_byte'], 0, 127, 'type_byte')
            if 'type_byte' not in cmd and 'type_label' in cmd and cmd['type_label'] not in CMD_LABEL_TO_TYPE: raise ValueError('unknown type_label')
            for k, lo, hi in [('channel',1,16),('data1',0,127),('data2',0,127)]:
                if k in cmd: _integer(cmd[k],lo,hi,k)
    elif endpoint in ('song_save', 'set_save', 'pcmap_save'):
        key, count, hi = {'song_save':('slots',15,120), 'set_save':('banks',50,150), 'pcmap_save':('map',128,120)}[endpoint]
        for v in _array(d.get(key),count,key):
            if v is not None: _integer(v,1,hi,key)
    elif endpoint == 'names_save':
        if not set(d) <= {'channel_names','switch_names'}: raise ValueError('unknown names field')
        groups = []
        if 'channel_names' in d: groups.append((d['channel_names'],16,'channel_names'))
        if 'switch_names' in d:
            if not isinstance(d['switch_names'],dict) or not set(d['switch_names']) <= {'pedals','switches'}: raise ValueError('invalid switch_names object')
            for k,n in [('pedals',2),('switches',15)]:
                if k in d['switch_names']: groups.append((d['switch_names'][k],n,k))
        for values,n,k in groups:
            for v in _array(values,n,k): _text(v,4,k)
    elif endpoint == 'preset_sysex':
        if 'bytes' in d:
            for v in _array(d['bytes'],30,'bytes'):
                if v is not None: _integer(v,0,127,'byte')
        if 'on' in d and type(d['on']) is not bool: raise ValueError('on must be boolean')
    elif endpoint in ('vfc_send','vfc_preset'):
        _integer(d.get('channel',1),1,16,'channel')
        if endpoint == 'vfc_preset': _integer(d.get('pc',0),0,127,'pc')
        else:
            if d.get('type') not in ('pc','cc','note-on','note-off'): raise ValueError('unknown MIDI type')
            _integer(d.get('num'),0,127,'num')
            if d['type'] != 'pc': _integer(d.get('val'),0,127,'val')
    elif endpoint in ('dump_globals_save','global_config'):
        if endpoint == 'dump_globals_save' and {'operating_mode', 'bank_style'} & d.keys():
            raise ValueError('operating_mode and bank_style writes are unsupported: protocol locations are unverified')
        enums = {'operating_mode':('BANK','SONG','REMOTE'), 'bank_mode':('BANK','SONG','REMOTE'), 'bank_style':('FIRST','CURNT','OFF'), 'pc_status':('OFF','ON','MAP')}
        allowed = {'bank_mode','bank_size','bank_style','midi_rx_channel','preset_start'} if endpoint == 'global_config' else {'operating_mode','bank_size','bank_style','remote_title','midi_rx_channel','pc_status','switch_types'}
        if not set(d) <= allowed: raise ValueError('unknown configuration field')
        for k,v in d.items():
            if k in enums and v not in enums[k]: raise ValueError(f'invalid {k}')
            if k == 'bank_size' and (type(v) is not int or v not in (1,5,10,15)): raise ValueError('invalid bank_size')
            if k == 'remote_title': _integer(v,0,127,k)
            if k == 'preset_start': _integer(v,0,1,k)
            if k == 'midi_rx_channel' and v != 'OMNI': _integer(v,1,16,k)
            if k == 'switch_types':
                if not isinstance(v,dict): raise ValueError('switch_types must be an object')
                for sw,t in v.items():
                    if sw not in [str(i) for i in range(1,16)] or t not in SWITCH_TYPE_BYTES: raise ValueError('invalid switch type')
    elif endpoint == 'dump_capture':
        target = d.get('target','all')
        if target != 'all': raise ValueError('only full-dump capture is supported')


# ── Flask + SocketIO ─────────────────────────────────────────────────────────
app = Flask(__name__, static_folder='static', template_folder='templates')
_AA_PORT = int(os.environ.get('PORT', 5002))
sio = SocketIO(app, async_mode='threading',
               cors_allowed_origins=[f'http://localhost:{_AA_PORT}',
                                     f'http://127.0.0.1:{_AA_PORT}'])
MIDI = MidiBridge(sio)

# Hardening: random per-launch SECRET_KEY, Origin+CSRF guard, and dump
# validation. See security_patch.py.
from security_patch import install_security
install_security(app, sio, port=_AA_PORT)

@app.before_request
def _runtime_request():
    if request.path.startswith('/api/') and request.method in ('POST','PUT','PATCH','DELETE'):
        STATE_LOCK.acquire()
        g.aa_state_locked = True
        # Older pacing clients may send no JSON; all data endpoints require objects.
        if request.endpoint not in ('dump_send','preset_send','dump_capture_cancel','writeback_pacing'):
            _validate_fields(_json_object())


@app.teardown_request
def _runtime_release(_error):
    if g.pop('aa_state_locked', False):
        STATE_LOCK.release()


@app.errorhandler(ValueError)
@app.errorhandler(TypeError)
@app.errorhandler(KeyError)
@app.errorhandler(IndexError)
@app.errorhandler(BadRequest)
def _bad_input(error):
    return jsonify({'ok': False, 'error': str(error)}), 400


@app.route('/api/write/status')
@app.route('/api/dump/send/status')
def write_status():
    return jsonify(_write_status())


def _write_status():
    with STATE_LOCK:
        return dict(WRITE_STATUS, enabled=os.environ.get('AA_ENABLE_EXPERIMENTAL_WRITE') == '1',
                    warning=WRITE_WARNING, verified=False)


@app.route('/')
def index():
    resp = send_from_directory('templates', 'index.html')
    # Always serve fresh — the UI iterates frequently and we don't want
    # the browser to hold onto stale JS/SVG between development reloads.
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    resp.headers['Pragma']  = 'no-cache'
    resp.headers['Expires'] = '0'
    return resp

@app.route('/static/<path:p>')
def static_file(p):
    return send_from_directory('static', p)

@app.route('/api/midi/ports')
def midi_ports():
    return jsonify(MIDI.list_ports())

@app.route('/api/midi/open', methods=['POST'])
def midi_open():
    d = _json_object()
    if not MIDI_LOCK.acquire(blocking=False):
        return jsonify({'ok': False, 'error': 'MIDI output busy'}), 409
    try:
        # Check both names before closing either existing connection.
        ports = MIDI.list_ports()
        if 'error' in ports and any(d.get(k) is not None for k in ('in_port', 'out_port')):
            return jsonify({'ok': False, 'error': ports['error']}), 503
        for key, direction in [('in_port', 'in'), ('out_port', 'out')]:
            if key in d and d[key] is not None and d[key] not in ports[direction]:
                raise ValueError(f'unknown {direction} port')
        # Omitted keys preserve the selection; explicit null closes that port.
        for key, port_attr, name_attr in [('in_port', 'in_port', 'in_name'),
                                          ('out_port', 'out_port', 'out_name')]:
            if key in d and d[key] is None:
                setattr(MIDI, name_attr, None)
                port = getattr(MIDI, port_attr)
                if port is not None:
                    port.close_port()
                if key == 'in_port':
                    MIDI._sysex_buf.clear()
                    MIDI._in_sysex = False
        in_ok = MIDI.open_in(d['in_port']) if d.get('in_port') is not None else True
        out_ok = MIDI.open_out(d['out_port']) if d.get('out_port') is not None else True
        return jsonify({'ok': in_ok and out_ok, 'in_ok': in_ok, 'out_ok': out_ok,
                        'ports': MIDI.list_ports()}), (200 if in_ok and out_ok else 503)
    except ValueError:
        raise
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 503
    finally:
        MIDI_LOCK.release()

@app.route('/api/dump/load', methods=['POST'])
def dump_load():
    """Load a .syx bulk dump uploaded from the browser file picker."""
    from rocktron.validation import validate_dump, FrameValidationError
    d = _json_object()
    if not d.get('hex'):
        return jsonify({'ok': False, 'error': 'need hex payload'}), 400
    hex_str = d['hex']
    # Real dump is ~87 KB of hex; anything wildly larger is either a
    # mistake or an attempt to exhaust memory. Cap at 1 MB hex.
    if len(hex_str) > 1_000_000:
        return jsonify({'ok': False, 'error': 'hex payload too large'}), 413
    buf = bytes.fromhex(hex_str)
    src = d.get('filename', 'upload')

    # Soft validation on load: a partial capture is still inspectable, but
    # the response carries `complete=false` so the UI can block Write All.
    try:
        validate_dump(buf, strict=False)
    except FrameValidationError as e:
        return jsonify({'ok': False, 'error': str(e)}), 400
    try:
        validate_dump(buf, strict=True)
        complete = True
    except FrameValidationError:
        complete = False

    parsed = parse_dump(buf)
    parsed['complete'] = complete
    with STATE_LOCK:
        STATE['dump'] = parsed
        STATE['dump_bytes'] = buf
        STATE['loaded_from'] = src
        STATE['capture_cb'] = None
        STATE['capture_frames'] = []
        STATE['capture_target'] = None
    return jsonify(_dump_payload(parsed, src))


def _dump_payload(parsed, src):
    """Slim JSON-safe view of a parsed dump for client responses."""
    return {
        'ok': True,
        'loaded_from': src,
        'presets': parsed['presets'],
        'channel_names': parsed['channel_names'],
        'switch_names': parsed['switch_names'],
        'pc_map': parsed['pc_map'],
        'songs': parsed['songs'],
        'sets':  parsed['sets'],
        'globals': parsed['globals'],
        'frame_count': parsed['frame_count'],
        'complete': parsed.get('complete', False),
        'has_dump': True,
        'write': _write_status(),
    }


@app.route('/api/dump/status')
def dump_status():
    with STATE_LOCK:
        d = STATE['dump']
        src = STATE.get('loaded_from')
    if not d:
        return jsonify({'has_dump': False, 'write': _write_status()})
    return jsonify(_dump_payload(d, src))

@app.route('/api/dump/save')
def dump_save():
    """Stream the current in-memory dump back as a .syx download.

    Filename is derived from a timestamp so successive saves don't
    overwrite each other. Browser will Save As… into the user's
    Downloads folder.
    """
    with STATE_LOCK:
        buf = STATE.get('dump_bytes')
    if not buf:
        return jsonify({'ok': False, 'error': 'no dump loaded'}), 400
    ts = time.strftime('%Y%m%d-%H%M%S')
    fname = f'rocktron-{ts}.syx'
    return Response(
        bytes(buf),
        mimetype='application/octet-stream',
        headers={'Content-Disposition': f'attachment; filename="{fname}"'},
    )

@app.route('/api/dump/capture', methods=['POST'])
def dump_capture():
    """Enter capture mode — collect incoming sysex frames into a buffer.

    The All Access manual documents no remote dump-request message, so the
    user must initiate the dump from the device (SYSX page 1 → DUMP). This
    endpoint primes the backend to accumulate frames as they arrive and
    auto-parse once the end-of-dump marker (23-byte tail) lands.
    """
    target = _json_object().get('target', 'all')

    with STATE_LOCK:
        STATE['capture_frames'] = []
        STATE['capture_target'] = target

    def on_frame(payload):
        # Only count Rocktron All Access frames
        if not payload.startswith(SYX_HEADER):
            return
        with STATE_LOCK:
            if STATE['capture_cb'] is not on_frame:
                return
            frames = STATE['capture_frames']
            frames.append(payload)
            if len(frames) > 145 or sum(map(len, frames)) > 500_000:
                STATE['capture_cb'] = None
                STATE['capture_frames'] = []
                sio.emit('capture-error', {'error': 'capture exceeds supported dump size'})
                return
            count = len(frames)
        sio.emit('capture-progress', {'count': count, 'last_len': len(payload)})
        # End-of-dump tail = 23-byte frame ending with the …F7 marker
        if len(payload) == 23:
            _finalize_capture(on_frame)

    with STATE_LOCK:
        STATE['capture_cb'] = on_frame
    return jsonify({'ok': True, 'target': target,
                    'message': 'Capture armed. Initiate DUMP on the device '
                               '(SYSX page 1 → DUMP, then CTR STORE).'})

@app.route('/api/dump/capture/cancel', methods=['POST'])
def dump_capture_cancel():
    with STATE_LOCK:
        STATE['capture_cb'] = None
        STATE['capture_frames'] = []
        STATE['capture_target'] = None
    return jsonify({'ok': True})

def _finalize_capture(expected_cb=None):
    with STATE_LOCK:
        if expected_cb is not None and STATE['capture_cb'] is not expected_cb:
            return
        frames = STATE['capture_frames']
        target = STATE['capture_target']
        STATE['capture_cb'] = None
        STATE['capture_frames'] = []
        STATE['capture_target'] = None
        if not frames:
            return
        buf = b''.join(frames)
        try:
            parsed = _parse_validated(buf)
        except ValueError as e:
            sio.emit('capture-error', {'error': str(e)})
            return
        STATE['dump'] = parsed
        STATE['dump_bytes'] = buf
        STATE['loaded_from'] = f'capture ({len(frames)} frames)'
        payload = _dump_payload(parsed, STATE['loaded_from'])
        payload.update(preset_count=len(parsed['presets']), target=target)
    sio.emit('capture-done', payload)

def _start_write(buf, kind):
    if os.environ.get('AA_ENABLE_EXPERIMENTAL_WRITE') != '1':
        return jsonify({'ok': False, 'error': 'Hardware writes disabled; set AA_ENABLE_EXPERIMENTAL_WRITE=1 to opt in.',
                        'warning': WRITE_WARNING, 'verified': False}), 403
    if not MIDI_LOCK.acquire(blocking=False):
        return jsonify({'ok': False, 'error': 'MIDI output busy'}), 409
    if not HAS_RTMIDI or MIDI.out_port is None or not MIDI.out_name:
        MIDI_LOCK.release()
        return jsonify({'ok': False, 'error': 'MIDI output unavailable or not selected'}), 503
    port = MIDI.out_port
    WRITE_STATUS.update(busy=True, kind=kind, done=False, error=None,
                        bytes_sent=0, bytes_total=len(buf), sent=0,
                        total=len(split_frames(buf)), verified=False)

    def publish():
        try:
            sio.emit('dump-progress', _write_status())
        except Exception:
            pass

    def run():
        started = time.monotonic()
        last_progress = 0
        sent_frames = 0
        try:
            # Preserve the old byte pacing only for explicit experiments.
            # RtMidi message boundaries make this unsafe on some backends;
            # no replacement transport or hardware receipt is claimed here.
            for i, b in enumerate(buf):
                port.send_message([b])
                time.sleep(0.015)
                sent_frames += b == 0xF7
                now = time.monotonic()
                with STATE_LOCK:
                    WRITE_STATUS.update(bytes_sent=i+1, sent=sent_frames,
                                        elapsed_s=round(now-started, 1))
                if now-last_progress >= 0.1:
                    last_progress = now
                    publish()
            with STATE_LOCK:
                WRITE_STATUS.update(done=True)
        except Exception as e:
            with STATE_LOCK:
                WRITE_STATUS.update(error=str(e), done=False)
        finally:
            with STATE_LOCK:
                WRITE_STATUS['busy'] = False
                MIDI_LOCK.release()
            publish()

    try:
        threading.Thread(target=run, daemon=False).start()
    except Exception:
        WRITE_STATUS.update(busy=False, error='could not start write worker')
        MIDI_LOCK.release()
        raise
    return jsonify({'ok': True, 'frames': len(split_frames(buf)), 'bytes': len(buf),
                    'warning': WRITE_WARNING, 'verified': False,
                    'pacing': {'chunk_size': 1, 'byte_delay_s': 0.015, 'inter_frame_s': 0.0}})


@app.route('/api/dump/send', methods=['POST'])
def dump_send():
    from rocktron.validation import validate_dump
    buf = STATE['dump_bytes']
    if not buf:
        raise ValueError('no dump loaded')
    validate_dump(buf, strict=True)
    return _start_write(buf, 'bulk')


@app.route('/api/writeback-pacing', methods=['GET', 'POST'])
def writeback_pacing():
    return jsonify(dict(_write_status(), chunk_size=1, byte_delay_s=0.015,
                        inter_frame_s=0.0, locked=True))


@app.route('/api/preset/<int:num>/send', methods=['POST'])
def preset_send(num):
    buf = STATE['dump_bytes']
    dump = STATE['dump']
    if not buf or not dump:
        raise ValueError('no dump loaded')
    if not 1 <= num <= len(dump['presets']):
        raise ValueError('preset out of range')
    from rocktron.validation import validate_dump
    validate_dump(buf, strict=True)
    frames = [(o,n) for o,n in split_frames(buf) if n == PRESET_FRAME_LEN]
    off,n = frames[num-1]
    return _start_write(buf[off:off+n], 'preset')

def _splice_preset(num, **edits):
    """Apply field edits to preset `num` and re-parse the dump.

    `edits` are forwarded as kwargs to rebuild_preset_frame (name,
    pc_slots, ia_bits, cmd_slots, sysex_bytes). Returns the updated
    preset dict, or raises ValueError on bad state.
    """
    with STATE_LOCK:
        dump = STATE['dump']
        buf  = STATE['dump_bytes']
    if not dump or not buf:
        raise ValueError('no dump loaded')
    if not (1 <= num <= len(dump['presets'])):
        raise ValueError('preset out of range')

    frames = split_frames(buf)
    preset_frame_indices = [i for i, (_, n) in enumerate(frames)
                            if n == PRESET_FRAME_LEN]
    fi = preset_frame_indices[num - 1]
    off, n = frames[fi]
    orig = buf[off:off+n]
    new  = rebuild_preset_frame(orig, **edits)
    new_buf = bytearray(buf)
    new_buf[off:off+n] = new

    parsed = _parse_validated(bytes(new_buf))
    with STATE_LOCK:
        STATE['dump_bytes'] = bytes(new_buf)
        STATE['dump'] = parsed
    return parsed['presets'][num - 1]


@app.route('/api/preset/<int:num>/rename', methods=['POST'])
def preset_rename(num):
    d = _json_object()
    new_name = (d.get('name') or '').upper()[:NAME_CHARS]
    try:
        preset = _splice_preset(num, name=new_name)
    except ValueError as e:
        return jsonify({'ok': False, 'error': str(e)}), 400
    return jsonify({'ok': True, 'preset': preset})


@app.route('/api/preset/<int:num>/pc', methods=['POST'])
def preset_pc(num):
    """Update one or more PC slots on a preset.

    Body: {"slots": [{"ch":1, "value":12, "active":true}, ...]}
    """
    d = _json_object()
    slots = d.get('slots') or []
    try:
        preset = _splice_preset(num, pc_slots=slots)
    except ValueError as e:
        return jsonify({'ok': False, 'error': str(e)}), 400
    return jsonify({'ok': True, 'preset': preset})


@app.route('/api/preset/<int:num>/ia', methods=['POST'])
def preset_ia(num):
    """Update the IA on/off bitmap for a preset.

    Body: {"bits": [false,...false (15 bools, SW1..SW15)]}
    """
    d = _json_object()
    bits = d.get('bits') or [False] * 15
    while len(bits) < 15:
        bits.append(False)
    try:
        preset = _splice_preset(num, ia_bits=bits)
    except ValueError as e:
        return jsonify({'ok': False, 'error': str(e)}), 400
    return jsonify({'ok': True, 'preset': preset})


@app.route('/api/preset/<int:num>/cmd', methods=['POST'])
def preset_cmd(num):
    """Update one or more custom MIDI command slots.

    Body: {"cmds": [{"idx":1, "type_label":"N ON>", "channel":1, "data1":60, "data2":100}, ...]}
    """
    d = _json_object()
    cmds = d.get('cmds') or []
    try:
        preset = _splice_preset(num, cmd_slots=cmds)
    except ValueError as e:
        return jsonify({'ok': False, 'error': str(e)}), 400
    return jsonify({'ok': True, 'preset': preset})


def _splice_frame_by_match(matcher, new_bytes):
    """Find the first frame in the dump satisfying matcher(frame_bytes) and
    replace it with new_bytes. Re-parses + persists. Returns the updated parsed dump.
    """
    with STATE_LOCK:
        buf = STATE['dump_bytes']
    if not buf:
        raise ValueError('no dump loaded')
    frames = split_frames(buf)
    target = None
    for i, (off, n) in enumerate(frames):
        if matcher(buf[off:off+n]):
            target = (i, off, n)
            break
    if target is None:
        raise ValueError('matching frame not found')
    _, off, n = target
    if len(new_bytes) != n:
        raise ValueError(f'frame length mismatch: got {len(new_bytes)}, expected {n}')
    new_buf = bytearray(buf)
    new_buf[off:off+n] = new_bytes
    parsed = _parse_validated(bytes(new_buf))
    with STATE_LOCK:
        STATE['dump_bytes'] = bytes(new_buf)
        STATE['dump'] = parsed
    return parsed


def _splice_song_frame(frame_index_in_songs, new_bytes):
    """Same as above but for the Nth (0-indexed) song frame (size 457)."""
    with STATE_LOCK:
        buf = STATE['dump_bytes']
    if not buf:
        raise ValueError('no dump loaded')
    frames = split_frames(buf)
    song_indices = [i for i, (_, n) in enumerate(frames) if n == SONG_FRAME_LEN]
    if frame_index_in_songs >= len(song_indices):
        raise ValueError('song frame index out of range')
    fi = song_indices[frame_index_in_songs]
    off, n = frames[fi]
    new_buf = bytearray(buf)
    new_buf[off:off+n] = new_bytes
    parsed = _parse_validated(bytes(new_buf))
    with STATE_LOCK:
        STATE['dump_bytes'] = bytes(new_buf)
        STATE['dump'] = parsed
    return parsed


def _splice_set_frame(set_idx, new_bytes):
    """Splice the Nth set frame (107 B). set_idx is 0-indexed."""
    with STATE_LOCK:
        buf = STATE['dump_bytes']
    if not buf:
        raise ValueError('no dump loaded')
    frames = split_frames(buf)
    set_indices = [i for i, (_, n) in enumerate(frames) if n == SET_FRAME_LEN]
    if set_idx >= len(set_indices):
        raise ValueError('set frame index out of range')
    fi = set_indices[set_idx]
    off, n = frames[fi]
    new_buf = bytearray(buf)
    new_buf[off:off+n] = new_bytes
    parsed = _parse_validated(bytes(new_buf))
    with STATE_LOCK:
        STATE['dump_bytes'] = bytes(new_buf)
        STATE['dump'] = parsed
    return parsed


@app.route('/api/song/<int:num>', methods=['POST'])
def song_save(num):
    """Update one song's preset slot list.

    Body: {"slots": [preset_num or null, ...]}   (up to 15 slots, SW1-15)
    """
    if not (1 <= num <= 150):
        return jsonify({'ok': False, 'error': 'song out of range'}), 400
    d = _json_object()
    slots = d.get('slots') or []
    indices = [None if v is None else max(0, min(119, int(v) - 1)) for v in slots]

    frame_index = (num - 1) // 15
    song_pos    = (num - 1) % 15

    with STATE_LOCK:
        buf = STATE['dump_bytes']
    if not buf:
        return jsonify({'ok': False, 'error': 'no dump loaded'}), 400
    frames = split_frames(buf)
    song_frame_indices = [i for i, (_, n) in enumerate(frames) if n == SONG_FRAME_LEN]
    if frame_index >= len(song_frame_indices):
        raise ValueError('song frame missing from partial dump')
    fi = song_frame_indices[frame_index]
    off, n = frames[fi]
    orig = buf[off:off+n]
    new = encode_song(orig, song_pos, indices)
    parsed = _splice_song_frame(frame_index, new)
    return jsonify({'ok': True, 'song': parsed['songs'][num - 1]})


@app.route('/api/set/<int:num>', methods=['POST'])
def set_save(num):
    """Update one set's 50 bank slots.

    Body: {"banks": [song_num (1..150) or null, ...]}
    """
    if not (1 <= num <= 10):
        return jsonify({'ok': False, 'error': 'set out of range'}), 400
    d = _json_object()
    banks = d.get('banks') or []
    indices = [None if v is None else max(0, min(149, int(v) - 1)) for v in banks]

    with STATE_LOCK:
        buf = STATE['dump_bytes']
    if not buf:
        return jsonify({'ok': False, 'error': 'no dump loaded'}), 400
    frames = split_frames(buf)
    set_frame_indices = [i for i, (_, n) in enumerate(frames) if n == SET_FRAME_LEN]
    if num > len(set_frame_indices):
        raise ValueError('set frame missing from partial dump')
    fi = set_frame_indices[num - 1]
    off, n = frames[fi]
    orig = buf[off:off+n]
    new = encode_set(orig, indices)
    parsed = _splice_set_frame(num - 1, new)
    return jsonify({'ok': True, 'set': parsed['sets'][num - 1]})


@app.route('/api/pcmap', methods=['POST'])
def pcmap_save():
    """Update the PC-map table (incoming PC1..128 → preset).

    Body: {"map": [preset_num (1..120) or null, ...] (up to 128)}
    """
    d = _json_object()
    m = d.get('map') or []
    indices = [None if v is None else max(0, min(119, int(v) - 1)) for v in m]

    with STATE_LOCK:
        buf = STATE['dump_bytes']
    if not buf:
        return jsonify({'ok': False, 'error': 'no dump loaded'}), 400
    frames = split_frames(buf)
    pcmap_idx = next((i for i, (_, n) in enumerate(frames) if n == PC_MAP_FRAME_LEN), None)
    if pcmap_idx is None:
        return jsonify({'ok': False, 'error': 'no PC-map frame in dump'}), 400
    off, n = frames[pcmap_idx]
    orig = buf[off:off+n]
    new = encode_pc_map(orig, indices)
    new_buf = bytearray(buf); new_buf[off:off+n] = new
    parsed = _parse_validated(bytes(new_buf))
    with STATE_LOCK:
        STATE['dump_bytes'] = bytes(new_buf)
        STATE['dump'] = parsed
    return jsonify({'ok': True, 'pc_map': parsed['pc_map']})


@app.route('/api/dump-globals', methods=['POST'])
def dump_globals_save():
    """Splice global edits into the dump's 223 B state frame and 23 B tail.

    Body: {"operating_mode":..., "bank_size":..., "bank_style":...,
           "remote_title":..., "midi_rx_channel":...}
    """
    d = _json_object()
    with STATE_LOCK:
        buf = STATE['dump_bytes']
    if not buf:
        return jsonify({'ok': False, 'error': 'no dump loaded'}), 400
    frames = split_frames(buf)

    state_idx = next((i for i, (_, n) in enumerate(frames) if n == GLOBAL_STATE_FRAME_LEN), None)
    tail_idx  = next((i for i, (_, n) in enumerate(frames) if n == TAIL_FRAME_LEN), None)
    if state_idx is None and tail_idx is None:
        return jsonify({'ok': False, 'error': 'global frames not found'}), 400

    state_orig = buf[frames[state_idx][0]:frames[state_idx][0] + frames[state_idx][1]] if state_idx is not None else None
    tail_orig  = buf[frames[tail_idx][0]:frames[tail_idx][0] + frames[tail_idx][1]]   if tail_idx  is not None else None
    new_state, new_tail = encode_globals(state_orig, tail_orig, d)

    new_buf = bytearray(buf)
    if new_state is not None:
        off, n = frames[state_idx]
        new_buf[off:off+n] = new_state
    if new_tail is not None:
        off, n = frames[tail_idx]
        new_buf[off:off+n] = new_tail
    parsed = _parse_validated(bytes(new_buf))
    with STATE_LOCK:
        STATE['dump_bytes'] = bytes(new_buf)
        STATE['dump'] = parsed
    return jsonify({'ok': True, 'globals': parsed['globals']})


@app.route('/api/names', methods=['POST'])
def names_save():
    """Splice channel + switch + pedal names back into the 279 B name block.

    Body: {"channel_names": [16 strings], "switch_names": {pedals:[2], switches:[15]}}
    """
    d = _json_object()
    with STATE_LOCK:
        buf = STATE['dump_bytes']
    if not buf:
        return jsonify({'ok': False, 'error': 'no dump loaded'}), 400
    frames = split_frames(buf)
    name_idx = next((i for i, (_, n) in enumerate(frames) if n == NAME_BLOCK_LEN), None)
    if name_idx is None:
        return jsonify({'ok': False, 'error': 'no name block in dump'}), 400
    off, n = frames[name_idx]
    orig = buf[off:off+n]
    channels, switches = decode_name_block(orig)
    # Merge prefixes into decoded originals so omitted groups/items survive.
    for i, name in enumerate(d.get('channel_names', [])):
        channels[i] = name
    for key, values in d.get('switch_names', {}).items():
        for i, name in enumerate(values):
            switches[key][i] = name
    encoded = encode_name_block(orig, channels, switches)
    new = bytearray(orig)
    selected = list(range(len(d.get('channel_names', []))))
    selected += [16+i for i in range(len(d.get('switch_names', {}).get('pedals', [])))]
    selected += [32-i for i in range(len(d.get('switch_names', {}).get('switches', [])))]
    for slot in selected:
        start = 6 + slot * 8
        new[start:start+8] = encoded[start:start+8]
    new_buf = bytearray(buf); new_buf[off:off+n] = new
    parsed = _parse_validated(bytes(new_buf))
    with STATE_LOCK:
        STATE['dump_bytes'] = bytes(new_buf)
        STATE['dump'] = parsed
    return jsonify({'ok': True, 'channel_names': parsed['channel_names'],
                    'switch_names': parsed['switch_names']})


@app.route('/api/preset/<int:num>/sysex', methods=['POST'])
def preset_sysex(num):
    """Update the 30-byte SysEx string and/or ON/OFF toggle for a preset.

    Body: {"bytes": [int...] (optional), "on": bool (optional)}
    """
    d = _json_object()
    edits = {}
    if 'bytes' in d: edits['sysex_bytes'] = d['bytes'] or []
    if 'on' in d:    edits['sysex_on']    = bool(d['on'])
    if not edits:
        return jsonify({'ok': False, 'error': 'no edits provided'}), 400
    try:
        preset = _splice_preset(num, **edits)
    except ValueError as e:
        return jsonify({'ok': False, 'error': str(e)}), 400
    return jsonify({'ok': True, 'preset': preset})

@app.route('/api/vfc/send', methods=['POST'])
def vfc_send():
    """Virtual foot controller — send raw MIDI.

    Body: {"channel": 1-16, "type": "pc"|"cc", "num": 0-127, "val": 0-127}
    """
    d = _json_object()
    if MIDI_LOCK.locked():
        return jsonify({'ok': False, 'error': 'MIDI output busy'}), 409
    ch = max(1, min(16, int(d.get('channel', 1)))) - 1
    if d['type'] == 'pc':
        sent = MIDI.send(bytes([0xC0 | ch, int(d['num']) & 0x7F]))
    elif d['type'] == 'cc':
        sent = MIDI.send(bytes([0xB0 | ch, int(d['num']) & 0x7F, int(d['val']) & 0x7F]))
    elif d['type'] == 'note-on':
        sent = MIDI.send(bytes([0x90 | ch, int(d['num']) & 0x7F, int(d['val']) & 0x7F]))
    elif d['type'] == 'note-off':
        sent = MIDI.send(bytes([0x80 | ch, int(d['num']) & 0x7F, int(d['val']) & 0x7F]))
    else:
        return jsonify({'ok': False, 'error': 'unknown type'}), 400
    if not sent:
        return jsonify({'ok': False, 'error': 'MIDI output unavailable or send failed'}), 503
    return jsonify({'ok': True})

@app.route('/api/vfc/preset', methods=['POST'])
def vfc_preset():
    """Recall a preset on a given channel via PC."""
    d = _json_object()
    if MIDI_LOCK.locked():
        return jsonify({'ok': False, 'error': 'MIDI output busy'}), 409
    ch = max(1, min(16, int(d.get('channel', 1)))) - 1
    pc = max(0, min(127, int(d.get('pc', 0))))
    sent = MIDI.send(bytes([0xC0 | ch, pc]))
    if not sent:
        return jsonify({'ok': False, 'error': 'MIDI output unavailable or send failed'}), 503
    return jsonify({'ok': True})

@app.route('/api/monitor')
def monitor():
    return jsonify(MIDI.recent_monitor())

@app.route('/api/global-config', methods=['GET', 'POST'])
def global_config():
    if request.method == 'GET':
        with STATE_LOCK:
            return jsonify(dict(STATE['global_config']))
    d = _json_object()
    with STATE_LOCK:
        STATE['global_config'].update(d)
    return jsonify(STATE['global_config'])

@sio.on('connect')
def on_connect():
    emit('hello', {'midi': MIDI.in_port is not None and MIDI.out_port is not None,
                   'write': _write_status(),
                   'ports': MIDI.list_ports(),
                   'has_dump': STATE['dump'] is not None})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5002))
    # Loopback only by default — this editor streams SysEx straight to your
    # rig and has no auth. Set AA_BIND=0.0.0.0 to expose on the LAN (only
    # behind something that does auth — never expose it raw).
    host = os.environ.get('AA_BIND', '127.0.0.1')
    print(f"Rocktron All Access editor — http://localhost:{port}")
    print(f"  bind: {host}  (AA_BIND=0.0.0.0 to expose on the LAN — not recommended)")
    print(f"  python-rtmidi: {'yes' if HAS_RTMIDI else 'NO — pip install python-rtmidi'}")
    sio.run(app, host=host, port=port, allow_unsafe_werkzeug=True)
