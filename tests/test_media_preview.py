"""Real local HTTP range responses for the video player; no user assets."""
import http.client
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import media_preview


class MediaPreviewHTTP(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.source = Path(cls.temp.name) / 'video.mp4'
        cls.content = bytes(range(256)) * 1024
        cls.source.write_bytes(cls.content)
        cls.empty = Path(cls.temp.name) / 'empty.mp4'
        cls.empty.write_bytes(b'')
        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'
            def do_GET(self):
                media_preview.stream_video(self, cls.empty if self.path == '/empty' else cls.source)
            def do_HEAD(self):
                media_preview.stream_video(self, cls.source, head=True)
            def log_message(self, *args):
                pass
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.server.daemon_threads = True
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.temp.cleanup()

    def fetch(self, value=None, method='GET', path='/video'):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            connection.request(method, path, headers={} if value is None else {'Range': value})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def test_complete_file_and_security_headers(self):
        status, headers, body = self.fetch()
        self.assertEqual(status, 200)
        self.assertEqual(body, self.content)
        self.assertEqual(headers['Content-Length'], str(len(body)))
        self.assertEqual(headers['Content-Type'], 'video/mp4')
        self.assertEqual(headers['Accept-Ranges'], 'bytes')
        self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(headers['Cache-Control'], 'private, no-store')
        self.assertNotIn('Content-Range', headers)

    def test_bounded_open_and_suffix_ranges(self):
        for value, start, end in [('bytes=0-1023', 0, 1023),
                                  ('bytes=1024-', 1024, len(self.content) - 1),
                                  ('bytes=-500', len(self.content) - 500, len(self.content) - 1),
                                  ('bytes=100-999999', 100, len(self.content) - 1),
                                  ('bytes=-999999', 0, len(self.content) - 1)]:
            with self.subTest(range=value):
                status, headers, body = self.fetch(value)
                self.assertEqual(status, 206)
                self.assertEqual(body, self.content[start:end + 1])
                self.assertEqual(headers['Content-Range'], f'bytes {start}-{end}/{len(self.content)}')
                self.assertEqual(int(headers['Content-Length']), end - start + 1)

    def test_invalid_ranges_rejected(self):
        for value in ['bytes=0-2,5-8', 'bytes=999999-', 'bytes=50-20', 'bytes=-0',
                      'bytes=-', 'items=0-5', 'bytes=a-b', 'bytes=-1-5', '']:
            with self.subTest(range=value):
                status, headers, body = self.fetch(value)
                self.assertEqual(status, 416)
                self.assertEqual(headers['Content-Range'], f'bytes */{len(self.content)}')
                self.assertEqual(headers['Content-Length'], '0')
                self.assertEqual(body, b'')

    def test_empty_file_with_and_without_range(self):
        status, headers, body = self.fetch(path='/empty')
        self.assertEqual((status, headers['Content-Length'], body), (200, '0', b''))
        status, headers, body = self.fetch('bytes=0-', path='/empty')
        self.assertEqual((status, headers['Content-Range'], body), (416, 'bytes */0', b''))

    def test_head_has_same_range_headers_and_no_body(self):
        status, headers, body = self.fetch('bytes=10-19', method='HEAD')
        self.assertEqual((status, headers['Content-Length'], body), (206, '10', b''))
        self.assertEqual(headers['Content-Range'], f'bytes 10-19/{len(self.content)}')

    def test_disconnect_during_stream_is_tolerated(self):
        class ClosedWriter:
            def write(self, value):
                raise BrokenPipeError()
        class Handler:
            headers = {}
            wfile = ClosedWriter()
            def send_response(self, value): pass
            def send_header(self, name, value): pass
            def end_headers(self): pass
        media_preview.stream_video(Handler(), self.source)

    def test_describe_uses_existing_probe(self):
        with patch.object(media_preview, 'media_info', return_value={
                'duration': 1.2, 'width': 320, 'height': 180, 'bytes': 1024, 'codec_name': 'h264'}) as probe:
            self.assertEqual(media_preview.describe_video(self.source), {
                'duration': 1.2, 'width': 320, 'height': 180, 'bytes': 1024, 'codec': 'h264'})
            probe.assert_called_once_with(self.source)


if __name__ == '__main__':
    unittest.main()
