"""Exercise real Flask routes with MIDI construction disabled, never hardware."""
import os
os.environ['AA_DISABLE_MIDI'] = '1'

import unittest
from unittest.mock import Mock, patch

import app as server
from rocktron import codec
from tools.uat_headless import build_synthetic_dump


class APITests(unittest.TestCase):
    def setUp(self):
        self.client = server.app.test_client()
        self.buf = build_synthetic_dump()
        with server.STATE_LOCK:
            server.STATE.update(dump=None, dump_bytes=None, loaded_from=None,
                                capture_cb=None, capture_frames=[], capture_target=None)
        self.env = patch.dict(os.environ, {'AA_DISABLE_MIDI':'1', 'AA_ENABLE_EXPERIMENTAL_WRITE':'0'})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def load(self, buf=None):
        r = self.client.post('/api/dump/load', json={'hex': (buf or self.buf).hex(), 'filename':'test.syx'})
        self.assertEqual(200, r.status_code, r.get_json())
        return r.get_json()

    def test_load_save_actual_http_roundtrip(self):
        data = self.load()
        self.assertTrue(data['complete'])
        self.assertEqual(self.buf, self.client.get('/api/dump/save').data)

    def test_bad_upload_json_returns_400(self):
        for body in ([], None, {'hex':123}, {'hex':'nothex'}, {'hex':'f'}, {'path':'/etc/passwd'}):
            with self.subTest(body=body):
                r = self.client.post('/api/dump/load', json=body)
                self.assertEqual(400,r.status_code, r.data[:100])
                self.assertFalse(r.get_json()['ok'])

    def test_host_origin_and_referer_checks(self):
        for headers in ({'Host':'attacker.invalid'}, {'Origin':'https://attacker.invalid'},
                        {'Referer':'http://localhost:5002.attacker.invalid/test'},
                        {'Sec-Fetch-Site':'cross-site'}):
            with self.subTest(headers=headers):
                r = self.client.post('/api/dump/load', json={'hex':self.buf.hex()}, headers=headers)
                self.assertEqual(403,r.status_code)
        self.assertEqual(403, self.client.get('/api/dump/status', headers={'Host':'attacker.invalid'}).status_code)

    def test_csrf_mismatch_refused_and_token_works(self):
        token = self.client.get('/api/dump/status').headers['X-AA-CSRF']
        r = self.client.post('/api/dump/load', json={'hex': self.buf.hex()}, headers={'X-AA-CSRF':'wrong'})
        self.assertEqual(403,r.status_code)
        r = self.client.post('/api/dump/load', json={'hex': self.buf.hex()}, headers={'X-AA-CSRF':token})
        self.assertEqual(200,r.status_code)

    def test_request_size_bounded_before_hex_parsing(self):
        r = self.client.post('/api/dump/load', data='x'*1_100_001, content_type='application/json')
        self.assertEqual(413,r.status_code)
        self.assertFalse(r.get_json()['ok'])

    def test_invalid_pc_channel_cannot_corrupt_frame(self):
        self.load()
        for channel in (0,17,-1,True,1.5,'1'):
            r = self.client.post('/api/preset/1/pc',json={'slots':[{'ch':channel, 'value':43}]})
            self.assertEqual(400,r.status_code,r.get_json())
        self.assertEqual(self.buf,self.client.get('/api/dump/save').data)

    def test_unknown_cmd_byte_preserved_on_data_edit(self):
        self.load()
        r = self.client.post('/api/preset/1/cmd',json={'cmds':[{'idx':1,'type_byte':99,'channel':4,'data1':17}]})
        self.assertEqual(200,r.status_code,r.get_json())
        self.assertEqual(99,r.get_json()['preset']['cmd_slots'][0]['type_byte'])

    def test_partial_dump_stays_partial_after_rename(self):
        partial = self.buf[:-23]
        self.assertFalse(self.load(partial)['complete'])
        r = self.client.post('/api/preset/1/rename', json={'name':'PARTIAL'})
        self.assertEqual(200,r.status_code,r.get_json())
        self.assertFalse(self.client.get('/api/dump/status').get_json()['complete'])
        self.assertEqual(400,self.client.post('/api/dump/send').status_code)
        self.assertEqual(400,self.client.post('/api/preset/1/send').status_code)

    def test_capture_partial_does_not_replace_with_complete_flag(self):
        self.load()
        self.assertEqual(200,self.client.post('/api/dump/capture',json={'target':'all'}).status_code)
        server.STATE['capture_cb'](self.buf[:309])
        server.STATE['capture_cb'](self.buf[-23:])
        data = self.client.get('/api/dump/status').get_json()
        self.assertFalse(data['complete'])
        self.assertEqual(1,len(data['presets']))

    def test_missing_song_frame_returns_400_not_500(self):
        self.load(self.buf[:309])
        r = self.client.post('/api/song/150',json={'slots':[1]})
        self.assertEqual(400,r.status_code)
        self.assertEqual(self.buf[:309],self.client.get('/api/dump/save').data)

    def test_short_pcmap_update_does_not_clear_other_mappings(self):
        self.load()
        before=self.client.get('/api/dump/status').get_json()['pc_map']
        r=self.client.post('/api/pcmap',json={'map':[50]})
        self.assertEqual(200,r.status_code,r.get_json())
        after=r.get_json()['pc_map']
        self.assertEqual(49,after[0])
        self.assertEqual(before[1:],after[1:])

    def test_partial_name_update_preserves_other_names(self):
        self.load()
        before=self.client.get('/api/dump/status').get_json()
        r=self.client.post('/api/names',json={'switch_names':{'pedals':['PEDA']}})
        self.assertEqual(200,r.status_code,r.get_json())
        data=r.get_json()
        self.assertEqual(before['channel_names'],data['channel_names'])
        self.assertEqual(before['switch_names']['switches'],data['switch_names']['switches'])
        self.assertEqual(before['switch_names']['pedals'][1],data['switch_names']['pedals'][1])

    def test_last_song_slot_api_edit_preserves_neighbours(self):
        self.load()
        r=self.client.post('/api/song/150',json={'slots':[None]*14+[117]})
        self.assertEqual(200,r.status_code,r.get_json())
        saved=self.client.get('/api/dump/save').data
        changes=[i for i,(a,b) in enumerate(zip(self.buf,saved)) if a!=b]
        song_frames=[(o,n) for o,n in codec.split_frames(self.buf) if n==457]
        self.assertEqual([song_frames[-1][0]+454],changes)
        self.assertEqual(116,r.get_json()['song']['slots'][14])

    def test_experimental_writes_disabled_by_default(self):
        self.load()
        for url in ('/api/dump/send','/api/preset/1/send'):
            r=self.client.post(url)
            self.assertEqual(403,r.status_code,r.get_json())
        status=self.client.get('/api/write/status').get_json()
        self.assertFalse(status['verified'])
        self.assertFalse(status['enabled'])

    def test_vfc_send_failure_not_reported_success(self):
        r=self.client.post('/api/vfc/send',json={'type':'pc','channel':1,'num':2})
        self.assertGreaterEqual(r.status_code,400)
        self.assertFalse(r.get_json()['ok'])

    def test_unknown_port_not_created_virtually(self):
        r=self.client.post('/api/midi/open',json={'out_port':'nonexistent'})
        self.assertGreaterEqual(r.status_code,400)
        self.assertFalse(r.get_json()['ok'])

    def test_mutating_requests_do_not_lose_parallel_edits(self):
        from concurrent.futures import ThreadPoolExecutor
        self.load()
        def edit(ch):
            with server.app.test_client() as client:
                return client.post('/api/preset/1/pc',json={'slots':[{'ch':ch,'value':ch+50}]}).status_code
        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual([200]*16,list(pool.map(edit,range(1,17))))
        slots=self.client.get('/api/dump/status').get_json()['presets'][0]['pc_slots']
        self.assertEqual(list(range(51,67)),[slot['value'] for slot in slots])

    def test_busy_output_refuses_second_write_vfc_and_port_switch(self):
        self.load()
        port=Mock()
        with patch.dict(os.environ,{'AA_ENABLE_EXPERIMENTAL_WRITE':'1'}), \
             patch.object(server,'HAS_RTMIDI',True), \
             patch.object(server.MIDI,'out_port',port), \
             patch.object(server.MIDI,'out_name','test output'), \
             patch.object(server.threading,'Thread'):
            try:
                self.assertEqual(200,self.client.post('/api/dump/send').status_code)
                self.assertEqual(409,self.client.post('/api/dump/send').status_code)
                self.assertEqual(409,self.client.post('/api/preset/1/send').status_code)
                self.assertEqual(409,self.client.post('/api/vfc/send',json={'type':'pc','num':1}).status_code)
                self.assertEqual(409,self.client.post('/api/midi/open',json={'out_port':'test output'}).status_code)
                port.send_message.assert_not_called()
            finally:
                server.WRITE_STATUS['busy']=False
                if server.MIDI_LOCK.locked():
                    server.MIDI_LOCK.release()

    def test_unsupported_globals_explicitly_rejected(self):
        self.load()
        for edits in ({'operating_mode':'REMOTE'},{'bank_style':'OFF'}):
            r=self.client.post('/api/dump-globals',json=edits)
            self.assertEqual(400,r.status_code,r.get_json())
        self.assertEqual(self.buf,self.client.get('/api/dump/save').data)

    def test_static_assets_available(self):
        for url in ('/','/static/editor.js','/static/editor.css'):
            response = self.client.get(url)
            try:
                self.assertEqual(200,response.status_code,url)
            finally:
                response.close()


if __name__=='__main__':
    unittest.main()
