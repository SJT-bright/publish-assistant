"""Borrow the App's exact platform page. Never launch or close another browser."""
import asyncio
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from runtime_support import process_alive

ROOT = Path(__file__).resolve().parent.parent
DESCRIPTOR = Path(os.environ.get('SAU_EMBEDDED_DESCRIPTOR', ROOT / '.draft-workbench/embedded-browser.json'))
PRIVATE = Path(os.environ.get('SAU_WORKBENCH_PRIVATE', ROOT / '.draft-workbench'))

def embedded_account_file(platform):
    if platform not in ('douyin', 'xiaohongshu', 'tencent', 'bilibili', 'kuaishou', 'toutiao', 'xigua'):
        raise ValueError('未知平台。')
    folder = PRIVATE / 'accounts'
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    return folder / f'{platform}.json'

def bridge_request(action, data):
    try:
        descriptor = json.loads(DESCRIPTOR.read_text(encoding='utf-8'))
        if not process_alive(descriptor['pid']):
            raise ValueError('内嵌浏览器已退出。')
        url = descriptor['url']
        if not url.startswith('http://127.0.0.1:'):
            raise ValueError('无效的内嵌浏览器地址。')
        request = urllib.request.Request(url + action, data=json.dumps(data).encode(), headers={
            'Content-Type': 'application/json', 'X-Embedded-Token': descriptor['token']}, method='POST')
        with urllib.request.urlopen(request, timeout=100) as response:
            return json.load(response)
    except urllib.error.HTTPError as e:
        raise RuntimeError(json.loads(e.read()).get('error', '内嵌浏览器连接失败。')) from e
    except (OSError, ValueError, KeyError) as e:
        raise RuntimeError('应用内浏览器未连接，请重新打开「发布助手」App。') from e

class EmbeddedBrowser:
    def __init__(self, platform, action):
        self.platform = platform
        self.action = action
        self.lease = None
        self.browser = self.context = self.page = None
        self._publication_permit_requested = False

    async def connect(self, playwright):
        info = await asyncio.to_thread(bridge_request, '/prepare', {'platform': self.platform, 'action': self.action})
        self.lease = info['lease']
        try:
            self.browser = await playwright.chromium.connect_over_cdp(info['endpoint'], timeout=30000)
            # Partitioned Electron sessions may share the CDP default context. Match the target ID,
            # never its title/URL or the first page, and never call context.new_page/storage_state.
            for context in self.browser.contexts:
                for page in context.pages:
                    cdp = await context.new_cdp_session(page)
                    try:
                        target = await cdp.send('Target.getTargetInfo')
                        if target['targetInfo']['targetId'] == info['targetId']:
                            self.page, self.context = page, context
                            page._sau_local_files = True
                            page._sau_embedded_handle = self
                            return self
                    finally:
                        await cdp.detach()
            raise RuntimeError('没有连接到 App 内的指定平台页面，任务已停止。')
        except BaseException:
            await self.release()
            raise

    async def release(self):
        # The app owns this browser/context/page. Playwright.stop only disconnects the client.
        if self.lease:
            lease = self.lease
            try:
                if self._publication_permit_requested:
                    await self.revoke_publication()
            finally:
                try:
                    await asyncio.to_thread(bridge_request, '/finished', {'lease': lease})
                finally:
                    self.lease = None
                    if getattr(self.page, '_sau_embedded_handle', None) is self:
                        self.page._sau_embedded_handle = None

    async def allow_publication(self):
        """Request a short final-submit permit for this exact publish lease."""
        if (self.action != 'publish' or not self.lease or self.page is None or
                getattr(self.page, '_sau_embedded_handle', None) is not self):
            raise RuntimeError('当前内嵌任务没有最终投稿权限。')
        # Even an uncertain bridge response needs an explicit revoke attempt.
        self._publication_permit_requested = True
        result = await asyncio.to_thread(bridge_request, '/publish-permit',
                                        {'lease': self.lease, 'platform': self.platform})
        if not isinstance(result, dict) or result.get('ok') is not True:
            raise RuntimeError('应用未授予本次任务的最终投稿权限。')
        return True

    async def revoke_publication(self):
        """Restore protection on this lease without touching other pages."""
        if not self._publication_permit_requested:
            return True
        if not self.lease:
            raise RuntimeError('最终投稿权限所属会话已失效。')
        result = await asyncio.to_thread(bridge_request, '/publish-revoke',
                                        {'lease': self.lease, 'platform': self.platform})
        if not isinstance(result, dict) or result.get('ok') is not True:
            raise RuntimeError('应用未确认恢复本次任务的投稿保护。')
        self._publication_permit_requested = False
        return True

    def mark_login(self, account=None, status='login_verified'):
        file = embedded_account_file(self.platform)
        previous = {}
        if file.is_file():
            try: previous = json.loads(file.read_text(encoding='utf-8'))
            except (OSError, ValueError): pass
        data = {**previous, 'status': status, 'browser': 'embedded',
                'platform': self.platform, 'checked_at': time.time()}
        if status == 'login_verified':
            data.update(verified_at=time.time(), account=account or {})
        temporary = file.with_suffix('.tmp')
        temporary.write_text(json.dumps(data))
        os.chmod(temporary, 0o600)
        temporary.replace(file)
