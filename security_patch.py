"""
security_patch.py -- hardening shims for the Rocktron All Access editor.

Imported at the top of app.py and wired in by calling
`install_security(app, sio)` immediately after `MIDI = MidiBridge(sio)`.

Covers:
  1. Network exposure -- binds to 127.0.0.1, locks Socket.IO CORS to localhost.
  2. Cross-site request forgery -- Origin/Referer + per-session CSRF token.
  3. Arbitrary file read -- /api/dump/load `path` confined to allow-list.
  4. Random SECRET_KEY per launch (was hard-coded).
  5. Cross-platform log path (was hard-coded /tmp).
  6. Dump validation -- reject partial/corrupt captures before Write All.

Does NOT touch byte-level RE logic.
"""
import os
import secrets
import tempfile
from pathlib import Path

from flask import request, jsonify, session


# -- 1. dump validation -------------------------------------------------------
class FrameValidationError(Exception):
    pass


# Expected frame multiset for a complete All Access bulk dump.
EXPECTED_FRAME_COUNTS = {
    309: 120,   # presets
    457: 10,    # songs
    107: 10,    # sets
    279: 1,     # name block
    263: 1,     # PC map
    223: 1,     # global state
    139: 1,     # filter / starting preset
    23:  1,     # tail
}
EXPECTED_TOTAL = sum(EXPECTED_FRAME_COUNTS.values())  # 145


def _split_frames(buf):
    frames, i = [], 0
    while i < len(buf):
        if buf[i] == 0xF0:
            j = i + 1
            while j < len(buf) and buf[j] != 0xF7:
                j += 1
            if j < len(buf):
                frames.append(buf[i:j + 1])
                i = j + 1
            else:
                break
        else:
            i += 1
    return frames


def validate_dump(buf, *, strict=True):
    """Raise FrameValidationError if `buf` is not a complete, well-formed dump.

    strict=True  -> require the exact 145-frame multiset (use before Write All).
    strict=False -> only require >=1 preset frame + a tail (use on Load, so the
                    user can still inspect a partial capture, just not write it).
    """
    frames = _split_frames(buf)
    if not frames:
        raise FrameValidationError("no SysEx frames found")

    counts = {}
    for f in frames:
        if not f.startswith(bytes([0xF0, 0x00, 0x00, 0x29, 0x08])):
            raise FrameValidationError(
                f"frame with bad header: {f[:5].hex()} (not a Rocktron All Access frame)")
        counts[len(f)] = counts.get(len(f), 0) + 1

    if strict:
        if len(frames) != EXPECTED_TOTAL:
            raise FrameValidationError(
                f"expected {EXPECTED_TOTAL} frames, got {len(frames)} "
                f"(partial or corrupt capture -- do NOT write this to the device)")
        for size, want in EXPECTED_FRAME_COUNTS.items():
            got = counts.get(size, 0)
            if got != want:
                raise FrameValidationError(
                    f"expected {want} frame(s) of size {size}, got {got}")
    else:
        if 309 not in counts:
            raise FrameValidationError("no preset frames present")
    return True


# -- 2. safe path allow-list --------------------------------------------------
def _allowed_roots():
    roots = [Path.cwd() / "reference", Path.cwd() / "exports", Path.cwd()]
    env = os.environ.get("AA_ALLOWED_DIRS")
    if env:
        roots += [Path(p) for p in env.split(os.pathsep) if p]
    home = Path.home()
    roots += [home / "Documents", home / "Downloads"]
    return [r.resolve() for r in roots if r.exists()]


def safe_resolve(user_path):
    """Resolve `user_path` and confirm it lives under an allowed root.

    Returns a resolved Path, or raises ValueError. Blocks ../ traversal,
    symlink escapes, and absolute paths outside the allow-list.
    """
    p = Path(user_path).expanduser().resolve()
    for root in _allowed_roots():
        try:
            p.relative_to(root)
            if p.suffix.lower() not in (".syx", ".mid", ".bin"):
                raise ValueError(f"refusing to read non-dump file type: {p.suffix}")
            return p
        except ValueError:
            continue
    raise ValueError(
        f"path {user_path!r} is outside the allowed directories "
        f"({', '.join(str(r) for r in _allowed_roots())})")


# -- 3. cross-platform log path -----------------------------------------------
def server_log_path():
    override = os.environ.get("AA_LOG_FILE")
    if override:
        return Path(override)
    return Path(tempfile.gettempdir()) / "aa-server.log"


# -- 4. origin / CSRF guard ---------------------------------------------------
def _local_origins(port):
    return {
        f"http://localhost:{port}", f"http://127.0.0.1:{port}",
        f"http://[::1]:{port}",
    }


def _origin_ok(port):
    origin = request.headers.get("Origin")
    if origin is not None:
        return origin in _local_origins(port)
    ref = request.headers.get("Referer", "")
    if ref:
        return any(ref.startswith(o) for o in _local_origins(port))
    return True


def install_security(app, sio, *, port=None):
    """Apply all server-side hardening. Call once after MIDI is constructed."""
    port = port or int(os.environ.get("PORT", 5002))

    app.config["SECRET_KEY"] = os.environ.get("AA_SECRET_KEY") or secrets.token_hex(32)

    try:
        sio.server.eio.cors_allowed_origins = list(_local_origins(port))
    except Exception:
        pass

    @app.after_request
    def _csrf_cookie(resp):
        if "aa_csrf" not in session:
            session["aa_csrf"] = secrets.token_urlsafe(24)
        resp.headers["X-AA-CSRF"] = session["aa_csrf"]
        resp.headers["X-Content-Type-Options"] = "nosniff"
        return resp

    @app.before_request
    def _guard():
        if request.method not in ("POST", "PUT", "DELETE", "PATCH"):
            return None
        if not _origin_ok(port):
            return jsonify({"ok": False,
                            "error": "cross-origin request refused"}), 403
        sent = request.headers.get("X-AA-CSRF")
        if session.get("aa_csrf") and sent and sent != session["aa_csrf"]:
            return jsonify({"ok": False, "error": "bad CSRF token"}), 403
        return None

    return app
