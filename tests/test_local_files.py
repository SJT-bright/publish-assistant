"""Disk-path uploads in isolated Chromium CDP targets, including real OOPIFs.

Run: .venv/bin/python -m unittest discover -s tests -p test_local_files.py -v
No formal app, profile, account, upload API or backend is opened.
"""
import asyncio
import json
import socket
import subprocess
import tempfile
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from patchright.async_api import async_playwright
from uploader.local_files import set_local_files


class Fixture(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        port = self.server.server_port
        input_html = '<input id="video" type="file" accept="video/mp4">'
        if self.path == '/':
            html = input_html + f'<iframe id="same" src="/same"></iframe><iframe id="cross" src="http://localhost:{port}/cross"></iframe>'
        elif self.path == '/cross':
            html = input_html + '<iframe id="nested" src="/nested"></iframe>'
        else:
            html = input_html
        data = ('<!doctype html><meta charset="utf-8">' + html).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class LocalFilesTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='local-files-cdp-')
        self.addAsyncCleanup(self.cleanup)
        self.folder = Path(self.temp.name)
        self.video = self.folder / '真实本机大视频.mp4'
        # Sparse disk fixture: a real 55 MiB File, without a memory payload.
        with self.video.open('wb') as stream:
            stream.write(b'local-cdp-fixture')
            stream.truncate(55 * 1024 * 1024 + 171)
        self.small = self.folder / 'small.mp4'
        self.small.write_bytes(b'small fixture')
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Fixture)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.p = await async_playwright().start()
        port = free_port()
        self.log = (self.folder / 'chromium.log').open('w')
        self.process = subprocess.Popen([
            self.p.chromium.executable_path, '--headless=new', '--no-first-run',
            '--site-per-process', f'--remote-debugging-port={port}',
            '--remote-debugging-address=127.0.0.1',
            '--user-data-dir=' + str(self.folder / 'profile'), 'about:blank',
        ], stdout=self.log, stderr=self.log)
        endpoint = None
        for _ in range(300):
            if self.process.poll() is not None:
                raise AssertionError('Isolated Chromium exited: ' + (self.folder / 'chromium.log').read_text())
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{port}/json/version', timeout=.3) as response:
                    endpoint = json.load(response)['webSocketDebuggerUrl']
                    break
            except (OSError, ValueError):
                await asyncio.sleep(.05)
        self.assertIsNotNone(endpoint, 'Isolated Chromium CDP did not start: ' + (self.folder / 'chromium.log').read_text())
        self.browser = await self.p.chromium.connect_over_cdp(endpoint)
        self.page = self.browser.contexts[0].pages[0]
        self.page._sau_local_files = True
        await self.page.goto(f'http://127.0.0.1:{self.server.server_port}/')

    async def cleanup(self):
        if getattr(self, 'p', None):
            await self.p.stop()
        if getattr(self, 'process', None) and self.process.poll() is None:
            self.process.terminate()
            try:
                await asyncio.to_thread(self.process.wait, 5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                await asyncio.to_thread(self.process.wait, 5)
        if getattr(self, 'log', None):
            self.log.close()
        if getattr(self, 'server', None):
            await asyncio.to_thread(self.server.shutdown)
            self.server.server_close()
            self.thread.join(timeout=2)
        self.temp.cleanup()

    async def assert_disk_file(self, locator):
        metadata = await locator.evaluate("""async input => {
            const file = input.files[0];
            return {name:file.name, size:file.size,
                    prefix:await file.slice(0,17).text(), count:input.files.length};
        }""")
        self.assertEqual(metadata, {'name': self.video.name, 'size': self.video.stat().st_size,
                                    'prefix': 'local-cdp-fixture', 'count': 1})

    async def test_original_transport_rejects_over_50_mib_and_cdp_uses_disk(self):
        locator = self.page.locator('#video')
        with self.assertRaisesRegex(Exception, 'Cannot transfer files larger than 50[Mm][Bb]'):
            await locator.set_input_files(self.video)
        await locator.evaluate("""input => {
            input.hidden=true;
            window.uploadEvents=[];
            for(const name of ['input','change']) input.addEventListener(name, event=>uploadEvents.push({type:event.type,trusted:event.isTrusted}));
        }""")
        # This catches an accidental future read_bytes/read payload fallback.
        with patch.object(Path, 'read_bytes', side_effect=AssertionError('Video must not be buffered')):
            await set_local_files(self.page, locator, self.video)
        await self.assert_disk_file(locator)
        self.assertFalse(await locator.evaluate("input=>input.getAttributeNames().some(name=>name.startsWith('data-sau-file-'))"))
        self.assertEqual(await self.page.evaluate('window.uploadEvents'), [{'type':'input','trusted':True},{'type':'change','trusted':True}])
        # Prove Chromium retained the native disk path, rather than a
        # memory-backed File that merely copied its name and length.
        session = await self.page.context.new_cdp_session(self.page)
        try:
            result = await session.send('Runtime.evaluate', {'expression': "document.getElementById('video').files[0]"})
            info = await session.send('DOM.getFileInfo', {'objectId': result['result']['objectId']})
            self.assertEqual(info['path'], str(self.video.resolve()))
        finally:
            await session.detach()

    async def test_same_process_iframe_uses_its_exact_input(self):
        locator = self.page.frame_locator('#same').locator('#video')
        await set_local_files(self.page, locator, self.video)
        await self.assert_disk_file(locator)
        self.assertEqual(await self.page.locator('#video').evaluate('input=>input.files.length'), 0)

    async def test_real_oopif_and_nested_frame_use_their_own_targets(self):
        cross = self.page.frame_locator('#cross')
        handle = await self.page.locator('#cross').element_handle()
        frame = await handle.content_frame()
        session = await self.page.context.new_cdp_session(frame)
        info = await session.send('Target.getTargetInfo')
        self.assertEqual(info['targetInfo']['type'], 'iframe', 'Fixture must actually use an OOPIF')
        await session.detach()
        locator = cross.locator('#video')
        await set_local_files(self.page, locator, self.video)
        await self.assert_disk_file(locator)
        nested = cross.frame_locator('#nested').locator('#video')
        await set_local_files(self.page, nested, self.video)
        await self.assert_disk_file(nested)
        self.assertEqual(await self.page.locator('#video').evaluate('input=>input.files.length'), 0)
        self.assertEqual(await self.page.frame_locator('#same').locator('#video').evaluate('input=>input.files.length'), 0)

    async def test_rejects_ambiguous_non_file_cross_page_and_bad_paths(self):
        await self.page.evaluate('''() => {
            const duplicate=document.createElement('input');duplicate.type='file';document.body.append(duplicate);
            const text=document.createElement('input');text.id='text';document.body.append(text);
        }''')
        with self.assertRaisesRegex(ValueError, '唯一'):
            await set_local_files(self.page, self.page.locator('input[type=file]'), self.video)
        with self.assertRaisesRegex(ValueError, '输入框'):
            await set_local_files(self.page, self.page.locator('#text'), self.video)
        other = await self.browser.contexts[0].new_page()
        await other.set_content('<input id="other" type="file">')
        with self.assertRaisesRegex(ValueError, '不属于'):
            await set_local_files(self.page, other.locator('#other'), self.video)
        await other.close()
        for source in (self.folder / 'missing.mp4', self.folder, {'name': 'buffer.mp4', 'buffer': b'memory'}):
            with self.subTest(source_type=type(source).__name__):
                with self.assertRaises(ValueError):
                    await set_local_files(self.page, self.page.locator('#video'), source)
        self.assertEqual(await self.page.locator('#video').evaluate('input=>input.files.length'), 0)

    async def test_multiple_and_clear_and_regular_transport_fallback(self):
        locator = self.page.locator('#video')
        with self.assertRaisesRegex(ValueError, '单个'):
            await set_local_files(self.page, locator, [self.video, self.small])
        await locator.evaluate('input=>input.multiple=true')
        await set_local_files(self.page, locator, [self.video, self.small])
        self.assertEqual(await locator.evaluate('input=>Array.from(input.files, f=>f.name)'), [self.video.name, self.small.name])
        await set_local_files(self.page, locator, [])
        self.assertEqual(await locator.evaluate('input=>input.files.length'), 0)
        del self.page._sau_local_files
        await set_local_files(self.page, locator, self.small)
        self.assertEqual(await locator.evaluate('input=>input.files[0].name'), self.small.name)

    async def test_page_can_remove_input_when_native_upload_starts(self):
        locator = self.page.locator('#video')
        await locator.evaluate("""input => input.addEventListener('change', () => {
            window.uploadedFile={name:input.files[0].name,size:input.files[0].size};
            input.remove();
        })""")
        await set_local_files(self.page, locator, self.video)
        self.assertEqual(await self.page.evaluate('window.uploadedFile'), {'name':self.video.name,'size':self.video.stat().st_size})
        self.assertEqual(await locator.count(), 0)

    async def test_shadow_root_and_specific_locator_among_duplicate_inputs(self):
        await self.page.evaluate("""() => {
            const host=document.createElement('section');host.id='shadow-host';document.body.append(host);
            host.attachShadow({mode:'open'}).innerHTML='<input type="file" id="shadow-video" hidden accept="video/mp4">';
        }""")
        with self.assertRaisesRegex(ValueError, '唯一'):
            await set_local_files(self.page, self.page.locator('input[type=file]'), self.video)
        shadow = self.page.locator('#shadow-host #shadow-video')
        await set_local_files(self.page, shadow, self.video)
        await self.assert_disk_file(shadow)
        self.assertEqual(await self.page.locator('#video').evaluate('input=>input.files.length'), 0)

    async def test_file_chooser_element_handle_can_use_disk_transport(self):
        async with self.page.expect_file_chooser() as event:
            await self.page.locator('#video').evaluate('input=>input.click()')
        chooser = await event.value
        await set_local_files(self.page, chooser.element, self.video)
        await self.assert_disk_file(self.page.locator('#video'))
        # Caller-owned handles remain usable after this helper returns.
        self.assertEqual(await chooser.element.evaluate('input=>input.files[0].size'), self.video.stat().st_size)


if __name__ == '__main__':
    unittest.main(verbosity=2)
