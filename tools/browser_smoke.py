#!/usr/bin/env python3
"""Optional Chromium regression check. Requires Playwright; sends no hardware MIDI.

Run with the project dependencies and Playwright installed, then:
    python tools/browser_smoke.py [--executable /path/to/chromium]
The script owns a temporary file-only server and stops it even on failure.
"""
import argparse
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from rocktron import codec
from tools.uat_headless import build_synthetic_dump


def main():
    from playwright.sync_api import sync_playwright
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--executable')
    parser.add_argument('--screenshot', help='optional path for the final browser view')
    args = parser.parse_args()
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    origin = f'http://127.0.0.1:{port}'
    env = dict(os.environ, AA_DISABLE_MIDI='1', AA_ENABLE_EXPERIMENTAL_WRITE='0', PORT=str(port))
    env['AA_BIND'] = '127.0.0.1'
    with tempfile.TemporaryDirectory(prefix='rocktron-browser-') as tmp:
        log = open(Path(tmp)/'server.log','w+')
        server = subprocess.Popen([sys.executable, str(ROOT/'app.py')],cwd=ROOT,env=env,stdout=log,stderr=log)
        try:
            for attempt in range(100):
                try:
                    urllib.request.urlopen(origin,timeout=0.5).close()
                    break
                except OSError:
                    if server.poll() is not None:
                        log.seek(0)
                        raise RuntimeError(log.read())
                    time.sleep(0.1)
            else:
                raise RuntimeError('temporary server did not start')
            original = bytearray(build_synthetic_dump())
            original[codec.CMD_REGION_OFF] = 99  # unknown command must survive saves
            for i in range(codec.SYSX_COUNT):
                original[codec.SYSX_OFF+i*2+1] = 1
            fixture = Path(tmp)/'fixture.syx'
            fixture.write_bytes(original)
            with sync_playwright() as p:
                options = {'headless':True}
                if args.executable:
                    options['executable_path'] = args.executable
                browser = p.chromium.launch(**options)
                try:
                    page = browser.new_page(viewport={'width':1280,'height':1000})
                    errors = []
                    page.on('pageerror',lambda err:errors.append(str(err)))
                    page.route('**/*',lambda route:route.continue_() if route.request.url.startswith(origin) else route.abort())
                    page.goto(origin)
                    with page.expect_file_chooser() as chooser:
                        page.locator('button[aria-label="Load .syx"]').click()
                    chooser.value.set_files(str(fixture))
                    page.wait_for_function('state.presets.length === 120')
                    assert page.locator('#btn-write-all').is_disabled()
                    page.locator('[data-tab="songs"]').click()
                    assert page.locator('[data-song-slot]').count() == 15
                    page.locator('[data-tab="presets"]').click()
                    page.locator('#preset-list .preset-row').first.click()
                    assert page.locator('#cmd-list .cmd-row').count() == 5
                    assert page.locator('#sysex-grid input').first.input_value() == ''
                    page.get_by_role('button',name='Save SysEx',exact=True).click()
                    page.wait_for_function("document.getElementById('toast').textContent.includes('SysEx saved')")
                    assert page.request.get(origin+'/api/dump/save').body() == bytes(original), 'EOX save changed bytes'
                    page.get_by_role('button',name='Save custom MIDI',exact=True).click()
                    page.wait_for_function("document.getElementById('toast').textContent.includes('custom MIDI saved')")
                    saved = page.request.get(origin+'/api/dump/save').body()
                    assert saved[codec.CMD_REGION_OFF] == 99
                    assert saved == bytes(original), 'unknown command save changed bytes'
                    page.reload()
                    page.wait_for_function('state.presets.length === 120 && state.songs.length === 150')
                    page.locator('[data-tab="channels"]').click()
                    assert page.locator('#switch-type-grid select').count() == 15
                    page.locator('[data-tab="global"]').click()
                    assert page.locator('#g-bank-mode').is_disabled()
                    assert page.locator('#g-bank-style').is_disabled()
                    assert page.locator('#g-bank-style').input_value() == ''
                    page.get_by_role('button',name='Save (UI only)',exact=True).click()
                    page.wait_for_function("document.getElementById('toast').textContent === 'Global saved'")
                    assert not errors, errors
                    if args.screenshot:
                        page.screenshot(path=args.screenshot, full_page=True)
                    print('Browser smoke passed: file picker, reload hydration, 15 songs slots, EOX/unknown-byte saves, write gate, no JS exceptions')
                finally:
                    browser.close()
        finally:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()
            log.close()


if __name__=='__main__':
    main()
