"""Current official web-editor boundaries and local UI/preflight agreement.

The expected cases come from this turn's captured official creator clients and
research/平台视频字段限制-20261003.md. Synthetic disk files and an isolated HTTP
handler are used; no formal backend, account or platform request is opened.
"""
import copy
import http.client
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import draft_workbench as wb
from platform_limits import check_fields, check_text, measure, text_fields
from workbench_model import preflight_payload


class TextLimitsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='platform-text-limits-')
        self.video = Path(self.temp.name) / 'synthetic.mp4'
        self.video.write_bytes(b'local field-limit fixture')
        self.payload = {'video': str(self.video), 'title': 'T', 'description': '', 'tags': [], 'short_title': ''}

    def tearDown(self):
        self.temp.cleanup()

    def checked(self, platform, **changes):
        payload = {**self.payload, **changes}
        return check_fields(platform, text_fields(platform, payload))

    def assert_field(self, checked, field, expected_error):
        self.assertEqual(any(error['field'] == field for error in checked['errors']), expected_error, checked['errors'])

    def test_utf16_and_platform_weighting_distinguish_ascii_cjk_and_emoji(self):
        self.assertEqual(measure('A中😀'), 4)
        self.assertEqual(measure('A中é😀', 'latin1_width'), 8)
        self.assertEqual(measure('A中é😀', 'xhs_topic'), 15)
        self.assertEqual(measure('A^中😀', 'xhs_title'), 5)
        self.assertEqual(measure('^', 'xhs_title'), 1)
        self.assertEqual(measure('\ud83d'), 1)  # A JS UTF-16 unit, not Python's code-point count.

    def test_existing_caption_topics_are_not_appended_twice_or_miscounted(self):
        for platform in ('douyin', 'tencent', 'kuaishou'):
            payload = {**self.payload, 'description': '正文 #科普 #验收', 'tags': ['科普', '验收', '新话题']}
            fields = text_fields(platform, payload)
            expected = ('T\n' if platform != 'douyin' else '') + '正文 #科普 #验收\n#新话题'
            self.assertEqual(fields['caption'], expected)
            checked = preflight_payload(payload, [platform])
            self.assertEqual(checked['mappings'][platform]['caption'], expected)
            fragments = text_fields(platform, {**payload, 'description': 'C#topic https://example.invalid/#科普', 'tags': ['topic', '科普']})
            self.assertTrue(fragments['caption'].endswith('\n#topic #科普'))

    def test_douyin_title_30_utf16_and_caption_1000_include_topics(self):
        for title, invalid in [('中' * 30, False), ('中' * 31, True), ('😀' * 15, False), ('😀' * 15 + 'A', True)]:
            with self.subTest(title_units=measure(title)):
                self.assert_field(self.checked('douyin', title=title), 'title', invalid)
        for body, tags, count, invalid in [('A' * 1000, [], 1000, False),
                                          ('A' * 1001, [], 1001, True),
                                          ('A' * 997, ['t'], 1000, False),
                                          ('A' * 998, ['t'], 1001, True),
                                          ('😀' * 500, [], 1000, False)]:
            with self.subTest(count=count, tags=tags):
                checked = self.checked('douyin', title='中' * 30, description=body, tags=tags)
                self.assertEqual(checked['metrics']['description']['used'], count)
                self.assert_field(checked, 'description', invalid)

    def test_xhs_title_uses_its_20_width_boundary_not_a_20_ascii_limit(self):
        for title, used, invalid in [('A' * 40, 20, False), ('A' * 41, 21, True),
                                     ('中' * 20, 20, False), ('中' * 21, 21, True),
                                     ('😀' * 10, 20, False), ('😀' * 10 + 'A', 21, True),
                                     ('^' * 20, 20, False), ('^' * 21, 21, True)]:
            with self.subTest(title=title):
                checked = self.checked('xiaohongshu', title=title)
                self.assertEqual(checked['metrics']['title']['used'], used)
                self.assert_field(checked, 'title', invalid)

    def test_xhs_body_counts_editor_entities_and_ignores_breaks_bom(self):
        for content, used in [('&', 5), ('<', 4), ('>', 4), ('\r\n\ufeff', 0),
                              ('\u00a0', 1), ('&nbsp;', 10), ('<b>', 9), ('😀', 2),
                              ('"\'', 2), ('A\n中\r\n\ufeff😀 &<>', 18)]:
            with self.subTest(content=content):
                self.assertEqual(measure(content, 'xhs_body'), used)
        for content, invalid in [('&' * 200, False), ('&' * 200 + 'A', True),
                                  ('<' * 250, False), ('<' * 250 + 'A', True),
                                  ('中' * 1000 + '\n\r\ufeff', False)]:
            with self.subTest(content_length=len(content)):
                self.assert_field(self.checked('xiaohongshu', description=content), 'description', invalid)

    def test_xhs_body_includes_topic_nodes_and_empty_body_separator(self):
        for body, tags, used, invalid in [('A' * 998, ['t'], 1000, False),
                                         ('A' * 999, ['t'], 1001, True),
                                         ('A' * 1000, [], 1000, False),
                                         ('', ['t'], 3, False),
                                         ('', ['t', '中'], 6, False)]:
            with self.subTest(used=used, tags=tags):
                checked = self.checked('xiaohongshu', description=body, tags=tags)
                self.assertEqual(checked['metrics']['description']['used'], used)
                self.assert_field(checked, 'description', invalid)

    def test_xhs_new_topic_count_does_not_impose_an_unproven_existing_topic_limit(self):
        limits = self.checked('xiaohongshu')['limits']
        self.assertEqual(limits['new_tag_max'], 30)
        self.assertEqual(limits['new_tag_count'], 'xhs_topic')
        self.assertNotIn('tag_max', limits)
        for tag, used in [('A' * 30, 30), ('A' * 31, 31), ('中' * 15, 30),
                          ('中' * 16, 32), ('😀' * 3, 24), ('😀' * 4, 32),
                          ('é' * 7, 28), ('é' * 8, 32)]:
            with self.subTest(topic=tag):
                self.assertEqual(measure(tag, 'xhs_topic'), used)
        # Existing topics have no confirmed universal 30-unit name cap.
        existing_topic = '已有话题' + '中' * 20
        payload = {**self.payload, 'tags': [existing_topic]}
        self.assertGreater(measure(existing_topic, 'xhs_topic'), limits['new_tag_max'])
        self.assert_field(self.checked('xiaohongshu', tags=[existing_topic]), 'tags', False)
        self.assertFalse(check_text(payload, ['xiaohongshu'])['errors'])
        self.assertFalse(preflight_payload(payload, ['xiaohongshu'])['errors'])

    def test_bilibili_80_title_2000_description_and_20_topic_are_separate(self):
        for title, invalid in [('中' * 80, False), ('中' * 81, True), ('😀' * 40, False), ('😀' * 40 + 'A', True)]:
            with self.subTest(field='title', used=measure(title)):
                self.assert_field(self.checked('bilibili', title=title), 'title', invalid)
        for body, invalid in [('中' * 2000, False), ('中' * 2001, True), ('😀' * 1000, False), ('😀' * 1000 + 'A', True)]:
            with self.subTest(field='description', used=measure(body)):
                checked = self.checked('bilibili', description=body, tags=[f't{i}' for i in range(10)])
                self.assert_field(checked, 'description', invalid)
                self.assertEqual(checked['metrics']['description']['used'], measure(body))
        for tag, invalid in [('中' * 20, False), ('中' * 21, True), ('😀' * 10, False), ('😀' * 10 + 'A', True)]:
            with self.subTest(field='tags', used=measure(tag)):
                self.assert_field(self.checked('bilibili', tags=[tag]), 'tags', invalid)

    def test_tencent_2000_weighted_caption_includes_title_and_topics(self):
        for title, body, tags, used, invalid in [('A', 'A' * 1998, [], 2000, False),
                                               ('A', 'A' * 1999, [], 2001, True),
                                               ('A', '中' * 999, [], 2000, False),
                                               ('A', '中' * 1000, [], 2002, True),
                                               ('A', '😀' * 499 + 'aa', [], 2000, False),
                                               ('A', '😀' * 499 + 'aaa', [], 2001, True),
                                               ('中', '中' * 997, ['t'], 2000, False),
                                               ('中', '中' * 998, ['t'], 2002, True)]:
            with self.subTest(used=used, tags=tags):
                checked = self.checked('tencent', title=title, description=body, tags=tags)
                self.assertEqual(checked['metrics']['description']['used'], used)
                self.assert_field(checked, 'description', invalid)

    def test_tencent_short_title_is_optional_but_nonempty_requires_6_to_16_and_supported_chars(self):
        for short, invalid in [('', False), ('ABCDE', True), ('ABCDEF', False), ('中' * 16, False),
                               ('中' * 17, True), ('天气120℃', False), ('ABCDEF😀', True),
                               ('AB\u200bCDEF', True), ('ABCDEF!', True), ('《测试》12', False)]:
            with self.subTest(short_title=short):
                self.assert_field(self.checked('tencent', short_title=short), 'short_title', invalid)

    def test_kuaishou_500_caption_includes_title_linebreaks_and_topics(self):
        for title, body, tags, used, invalid in [('T', 'A' * 498, [], 500, False),
                                               ('T', 'A' * 499, [], 501, True),
                                               ('T', 'A' * 495, ['t'], 500, False),
                                               ('T', 'A' * 496, ['t'], 501, True),
                                               ('T', '😀' * 249, [], 500, False),
                                               ('A' * 500, '', [], 500, False)]:
            with self.subTest(used=used, tags=tags):
                checked = self.checked('kuaishou', title=title, description=body, tags=tags)
                self.assertEqual(checked['metrics']['description']['used'], used)
                self.assert_field(checked, 'description', invalid)

    def test_topic_quantity_boundaries_after_actual_topic_deduplication(self):
        for platform, maximum in [('douyin', 5), ('xiaohongshu', 10), ('bilibili', 10), ('kuaishou', 4)]:
            with self.subTest(platform=platform):
                self.assert_field(self.checked(platform, tags=[f't{i}' for i in range(maximum)]), 'tags', False)
                self.assert_field(self.checked(platform, tags=[f't{i}' for i in range(maximum + 1)]), 'tags', True)
                checked = self.checked(platform, tags=['#t0', 't0', '＃t0'])
                self.assertEqual(checked['metrics']['tags']['used'], 1)

    def test_only_stricter_positive_integer_live_capabilities_reduce_defaults(self):
        fields = text_fields('douyin', {**self.payload, 'description': 'A' * 600, 'tags': ['a', 'b', 'c', 'd']})
        stricter = check_fields('douyin', fields, {'caption_max': 500, 'tags_max': 3})
        self.assertEqual(stricter['metrics']['description']['max'], 500)
        self.assertEqual(stricter['metrics']['tags']['max'], 3)
        self.assert_field(stricter, 'description', True)
        self.assert_field(stricter, 'tags', True)
        for value in (True, False, 0, -1, 1001, '500', 500.0, None):
            with self.subTest(capability=value):
                checked = check_fields('douyin', fields, {'caption_max': value, 'tags_max': value})
                self.assertEqual(checked['metrics']['description']['max'], 1000)
                self.assertEqual(checked['metrics']['tags']['max'], 5)

    def test_tag_only_override_tracks_future_common_copy_and_title_changes(self):
        payload = {**self.payload, 'title': '初始标题', 'description': '初始文案', 'tags': ['通用'],
                   'platform_overrides': {'douyin': {'tags': ['独特']}}}
        original = copy.deepcopy(payload)
        first = preflight_payload(payload, ['douyin', 'bilibili'])
        self.assertFalse(first['errors'])
        self.assertEqual(first['mappings']['douyin']['caption'], '初始文案\n#独特')
        payload.update(title='更新标题', description='更新文案')
        second = preflight_payload(payload, ['douyin', 'bilibili'])
        self.assertEqual(second['mappings']['douyin']['title'], '更新标题')
        self.assertEqual(second['mappings']['douyin']['description'], '更新文案')
        self.assertEqual(second['mappings']['douyin']['caption'], '更新文案\n#独特')
        self.assertEqual(second['mappings']['bilibili']['tags'], ['通用'])
        self.assertEqual(payload['platform_overrides'], original['platform_overrides'])
        self.assertNotEqual(first['fingerprints']['douyin']['content_sha256'], second['fingerprints']['douyin']['content_sha256'])

    def test_kuaishou_feedback_reports_exact_numeric_topic_budget_for_repair_ui(self):
        payload={**self.payload,'tags':['a','b','c','d','e']}
        before=copy.deepcopy(payload)
        normal=check_text(payload,['kuaishou'])['feedback']['kuaishou']['tags']
        self.assertEqual((normal['used'],normal['max'],normal['error']),(5,4,True))
        strict=check_text(payload,['kuaishou'],{'kuaishou':{'tags_max':2}})['feedback']['kuaishou']['tags']
        self.assertEqual((strict['used'],strict['max'],strict['error']),(5,2,True))
        self.assertEqual(payload,before)

    def test_explicit_empty_copy_and_topics_override_and_oversize_payload_are_not_mutated(self):
        payload = {**self.payload, 'description': '通用正文', 'tags': ['通用'],
                   'platform_overrides': {'douyin': {'description': '', 'tags': []}}}
        original = copy.deepcopy(payload)
        checked = preflight_payload(payload, ['douyin', 'bilibili'])
        self.assertFalse(checked['errors'])
        self.assertEqual(checked['mappings']['douyin']['caption'], '')
        self.assertEqual(checked['mappings']['douyin']['tags'], [])
        self.assertEqual(checked['mappings']['bilibili']['description'], '通用正文')
        self.assertEqual(checked['mappings']['bilibili']['tags'], ['通用'])
        self.assertEqual(payload, original)
        oversized = {**self.payload, 'title': '😀' * 41, 'description': '&<>\n' * 1001,
                     'tags': [f't{i}' for i in range(11)]}
        original = copy.deepcopy(oversized)
        check_text(oversized, ['douyin', 'xiaohongshu', 'bilibili', 'tencent', 'kuaishou'])
        checked = preflight_payload(oversized, ['douyin', 'xiaohongshu', 'bilibili', 'tencent', 'kuaishou'])
        self.assertTrue(checked['errors'])
        self.assertEqual(oversized, original)
        self.assertEqual(checked['mappings']['bilibili']['title'], original['title'])
        self.assertEqual(checked['mappings']['bilibili']['description'], original['description'])
        self.assertEqual(checked['mappings']['bilibili']['tags'], original['tags'])


class TextLimitHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='text-limits-http-')
        self.root = Path(self.temp.name)
        self.video = self.root / 'synthetic.mp4'
        self.video.write_bytes(b'local http fixture')
        self.sessions = {}
        self.patches = [patch.object(wb, 'PRIVATE', self.root / 'private'), patch.object(wb, 'STORE_ROOT', None),
                        patch.object(wb, 'CURRENT', None), patch.object(wb, 'EDITOR', {}), patch.object(wb, 'LEDGER', {}),
                        patch.object(wb, 'ACCOUNTS', {}), patch.object(wb, 'HISTORY', []), patch.object(wb, 'ASSETS', set()),
                        patch.object(wb, 'account_file', return_value=self.root / 'unused-session.json'),
                        patch.object(wb, 'embedded_account_file', return_value=self.root / 'unused-embedded.json'),
                        patch.object(wb, 'current_sessions', side_effect=lambda: self.sessions)]
        for item in self.patches:
            item.start()
        class QuietHandler(wb.Handler):
            def log_message(self, *_):
                pass
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), QuietHandler)
        self.server.daemon_threads = True
        self.patches.extend([patch.object(wb, 'PORT', self.server.server_port),
                             patch.object(wb, 'URL', f'http://127.0.0.1:{self.server.server_port}')])
        for item in self.patches[-2:]:
            item.start()
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def post(self, route, data):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            body = json.dumps(data, ensure_ascii=False).encode()
            connection.request('POST', route, body, {'Content-Type': 'application/json',
                                                      'X-Workbench-Token': wb.TOKEN})
            response = connection.getresponse()
            result = json.loads(response.read())
            self.assertEqual(response.status, 200, result)
            return result['result']
        finally:
            connection.close()

    def test_ui_text_check_and_preflight_share_identical_platform_errors(self):
        variants = [
            {'title': 'T', 'description': 'A' * 999, 'tags': ['t']},
            {'title': 'A' * 41, 'description': '&' * 200 + 'A', 'tags': []},
            {'title': '中', 'description': '😀' * 499 + 'aaa', 'tags': ['话题']},
            {'title': 'T', 'description': '通用文案', 'tags': [f't{i}' for i in range(11)],
             'platform_overrides': {'douyin': {'tags': []}, 'xiaohongshu': {'description': '', 'tags': ['独特']}}},
            {'title': 'T', 'description': '正常正文', 'tags': ['已有话题' + '中' * 20]},
        ]
        platforms = ['douyin', 'xiaohongshu', 'bilibili', 'tencent', 'kuaishou']
        for variant in variants:
            with self.subTest(title=variant['title'], body_size=len(variant['description'])):
                data = {'browser': 'embedded', 'platforms': platforms,
                        'payload': {'video': str(self.video), **variant}}
                original = copy.deepcopy(data)
                ui = self.post('/api/text-check', data)
                preflight = self.post('/api/preflight', data)
                self.assertEqual(ui['errors'], preflight['errors'])
                self.assertEqual(data, original)
                for platform in platforms:
                    for field in ('title', 'description', 'tags'):
                        self.assertEqual(ui['feedback'][platform][field]['error'],
                                         any(error['platform'] == platform and error['field'] == field for error in preflight['errors']))

    def test_client_capabilities_cannot_relax_or_replace_live_editor_limits(self):
        payload = {'video': str(self.video), 'title': 'T', 'description': 'A' * 1001, 'tags': [],
                   'capabilities': {'douyin': {'caption_max': 999999}}}
        data = {'browser': 'embedded', 'platforms': ['douyin'], 'payload': payload,
                'capabilities': {'douyin': {'caption_max': 999999}}}
        ui = self.post('/api/text-check', data)
        preflight = self.post('/api/preflight', data)
        self.assertEqual(ui['errors'], preflight['errors'])
        self.assertTrue(any(error['field'] == 'description' for error in ui['errors']))
        self.assertIn('1001/1000', ui['feedback']['douyin']['description']['text'])

        self.sessions = {'douyin': {'status': 'login_verified', 'capabilities': {'caption_max': 500, 'tags_max': 3}}}
        data['payload'].update(description='A' * 600, tags=['a', 'b', 'c', 'd'])
        ui = self.post('/api/text-check', data)
        preflight = self.post('/api/preflight', data)
        self.assertEqual(ui['errors'], preflight['errors'])
        self.assertIn('/500', ui['feedback']['douyin']['description']['text'])
        self.assertIn('4/3', ui['feedback']['douyin']['tags']['text'])
        self.assertEqual({error['field'] for error in ui['errors']}, {'description', 'tags'})


if __name__ == '__main__':
    unittest.main(verbosity=2)
