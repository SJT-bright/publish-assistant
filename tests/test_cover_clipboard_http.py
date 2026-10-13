"""Registered-image conversion over a real local HTTP connection, no profiles."""
import http.client
import io
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode
from http.server import ThreadingHTTPServer
from PIL import Image
import draft_workbench as wb


class CoverClipboardHTTPTests(unittest.TestCase):
    def test_registered_jpg_webp_png_decode_and_unregistered_path_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths=[]
            for ext in ('jpg', 'webp', 'png'):
                path=Path(tmp)/('cover.'+ext)
                Image.new('RGB', (120,160), '#4488cc').save(path)
                paths.append(path)
            with patch.object(wb, 'ASSETS', set(map(str, paths))):
                server=ThreadingHTTPServer(('127.0.0.1',0),wb.Handler)
                thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
                try:
                    with patch.object(wb,'PORT',server.server_port):
                        for path in paths+[Path(tmp)/'unregistered.png']:
                            client=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
                            client.request('GET','/asset?'+urlencode({'path':str(path),'clipboard':'1'}))
                            response=client.getresponse();data=response.read()
                            if path in paths:
                                self.assertEqual(response.status,200)
                                self.assertEqual(response.getheader('Content-Type'),'image/png')
                                with Image.open(io.BytesIO(data)) as image:self.assertEqual(image.size,(120,160))
                            else:self.assertEqual(response.status,404)
                            client.close()
                finally:server.shutdown();server.server_close();thread.join()


if __name__=='__main__':unittest.main()
