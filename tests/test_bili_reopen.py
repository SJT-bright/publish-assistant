"""Bili normal observed-link navigation; isolated local responses only."""
import unittest

from patchright.async_api import async_playwright
from uploader.bili_reopen import bili_open_draft_management, bili_reopen_normal

MANAGEMENT='https://member.bilibili.com/platform/upload-manager/article?group=draft'
REOPEN='https://member.bilibili.com/platform/upload/video/frame?type=draft&draftId=91'


class BiliNormalReopenTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pw=await async_playwright().start()
        self.browser=await self.pw.chromium.launch(headless=True,channel='chromium',args=['--disable-gpu'])
        self.context=await self.browser.new_context();self.page=await self.context.new_page();self.documents={};self.requests=[]
        async def route(r):
            self.requests.append(r.request.url)
            if r.request.url not in self.documents:return await r.abort()
            await r.fulfill(content_type='text/html;charset=utf-8',body='<!doctype html><meta charset="utf-8">'+self.documents[r.request.url])
        await self.context.route('**/*',route)

    async def asyncTearDown(self):
        await self.browser.close();await self.pw.stop()

    async def test_normal_content_entry_draft_tab_then_unique_exact_id_link(self):
        start='https://member.bilibili.com/platform/upload/video/frame'
        article='https://member.bilibili.com/platform/upload-manager/article'
        self.documents={start:'<a href="'+article+'">内容管理</a>',article:'<a href="'+MANAGEMENT+'">草稿</a>',
                        MANAGEMENT:'<a href="'+REOPEN+'">标题</a><a href="'+REOPEN+'">编辑</a>',REOPEN:'正常重开'}
        await self.page.goto(start)
        self.assertTrue(await bili_open_draft_management(self.page,timeout=2))
        self.assertTrue(await bili_reopen_normal(self.page,{'id':'91'}))
        self.assertEqual(self.requests,[start,article,MANAGEMENT,REOPEN])

    async def test_wrong_id_hidden_link_and_ambiguous_distinct_routes_never_navigate(self):
        variants=['<a href="'+REOPEN.replace('91','92')+'">同名标题</a>',
                  '<a style="display:none" href="'+REOPEN+'">编辑</a>',
                  '<a href="'+REOPEN+'">编辑</a><a href="'+REOPEN+'&from=other">编辑</a>']
        for document in variants:
            with self.subTest(document=document):
                self.documents={MANAGEMENT:document};await self.page.goto(MANAGEMENT)
                before=list(self.requests)
                self.assertFalse(await bili_reopen_normal(self.page,{'id':'91'}))
                self.assertEqual(self.requests,before)

    async def test_duplicate_normal_management_controls_stop_without_click(self):
        start='https://member.bilibili.com/platform/upload/video/frame'
        self.documents={start:'<button onclick="throw Error(\'clicked\')">内容管理</button><button>内容管理</button>'}
        await self.page.goto(start);before=list(self.requests)
        self.assertFalse(await bili_open_draft_management(self.page,timeout=.5))
        self.assertEqual(self.requests,before)


if __name__=='__main__':unittest.main()
