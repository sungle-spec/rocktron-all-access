"""Loopback host/origin guards and bounded JSON requests for the local editor.

Protocol validation is in rocktron.validation; imports below preserve the old
security_patch API. No server-side dump-path reading is supported.
"""
import os
import secrets
from urllib.parse import urlsplit

from flask import request, jsonify, session
from werkzeug.exceptions import HTTPException

from rocktron.validation import (FrameValidationError, EXPECTED_FRAME_COUNTS,
                                 EXPECTED_TOTAL, validate_dump)


def _local_origins(port):
    return {f'http://localhost:{port}', f'http://127.0.0.1:{port}', f'http://[::1]:{port}'}


def _origin_ok(port):
    allowed = _local_origins(port)
    origin = request.headers.get('Origin')
    if origin is not None:
        return origin in allowed
    ref = request.headers.get('Referer')
    if ref:
        parsed = urlsplit(ref)
        return f'{parsed.scheme}://{parsed.netloc}' in allowed
    # Headerless local CLI requests are intentional. Browser cross-site requests
    # are rejected by Origin/Referer and Fetch Metadata, including form POSTs.
    return request.headers.get('Sec-Fetch-Site') not in ('cross-site', 'same-site')


def install_security(app, sio, *, port=None):
    port = port or int(os.environ.get('PORT', 5002))
    app.config['SECRET_KEY'] = os.environ.get('AA_SECRET_KEY') or secrets.token_hex(32)
    app.config['MAX_CONTENT_LENGTH'] = 1_100_000
    app.config['SESSION_COOKIE_SAMESITE'] = 'Strict'
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    try:
        sio.server.eio.cors_allowed_origins = list(_local_origins(port))
    except AttributeError:
        pass

    @app.before_request
    def _guard():
        # Host validation also closes DNS-rebinding access to unauthenticated GETs.
        if urlsplit('http://' + request.host).hostname not in ('localhost', '127.0.0.1', '::1'):
            return jsonify(ok=False, error='non-loopback host refused'), 403
        if request.method in ('POST', 'PUT', 'DELETE', 'PATCH'):
            if not _origin_ok(port):
                return jsonify(ok=False, error='cross-origin request refused'), 403
            sent = request.headers.get('X-AA-CSRF')
            if sent is not None and not secrets.compare_digest(str(sent), str(session.get('aa_csrf', ''))):
                return jsonify(ok=False, error='bad CSRF token'), 403

    @app.after_request
    def _headers(resp):
        if 'aa_csrf' not in session:
            session['aa_csrf'] = secrets.token_urlsafe(24)
        resp.headers['X-AA-CSRF'] = session['aa_csrf']
        resp.headers['X-Content-Type-Options'] = 'nosniff'
        resp.headers['Referrer-Policy'] = 'same-origin'
        return resp

    @app.errorhandler(HTTPException)
    def _http_error(error):
        if request.path.startswith('/api/'):
            return jsonify(ok=False, error=error.description), error.code
        return error

    return app
