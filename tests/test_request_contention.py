"""Real HTTP admission checks, with no production mutations or provider calls."""
import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

import MomentumLabV2 as app


class RequestContentionTests(unittest.TestCase):
    def test_busy_requests_fail_promptly_without_execution_and_retry_works(self):
        executed = []

        class Handler(app.Handler):
            def handle_post(self):
                self.rfile.read(int(self.headers.get('Content-Length', 0)))
                executed.append(self.path)
                self.json({'ok': True})

            def log_message(self, *args):
                pass

        lock = threading.RLock()
        with patch.object(app, '_REQUEST_LOCK', lock), \
             patch.object(app, 'job_snapshot', return_value={'status': 'idle'}), \
             patch('momentumlab.review_jobs.snapshot', return_value={'status': 'idle'}):
            server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = 'http://127.0.0.1:' + str(server.server_port)
            try:
                with lock:
                    for path in ('/api/strategy-grid', '/api/live/detail', '/api/live/preview',
                                 '/api/config', '/api/completeness/apply-review'):
                        with self.subTest(path=path):
                            with self.assertRaises(HTTPError) as caught:
                                urlopen(Request(base + path, data=b'{}'), timeout=2)
                            self.assertEqual(caught.exception.code, 409)
                            body = json.load(caught.exception)
                            self.assertEqual(body['code'], 'APPLICATION_BUSY')
                            self.assertIn('not started or queued', body['error'])
                    with urlopen(base + '/api/calculation-status', timeout=2) as response:
                        self.assertTrue(json.load(response)['ok'])
                    self.assertEqual(executed, [])
                with urlopen(Request(base + '/api/strategy-grid', data=b'{}'), timeout=2) as response:
                    self.assertTrue(json.load(response)['ok'])
                self.assertEqual(executed, ['/api/strategy-grid'])
                self.assertFalse(app._CALCULATION_PROGRESS['running'])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


if __name__ == '__main__':
    unittest.main()
