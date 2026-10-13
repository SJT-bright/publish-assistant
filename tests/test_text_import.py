"""Text import contracts exercised with real local files only."""
import tempfile
import unittest
from pathlib import Path

from text_import import MAX_TEXT_BYTES, normalize_tags, read_text_field


class TextImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, name, content, encoding='utf-8'):
        path = self.folder / name
        path.write_bytes(content.encode(encoding) if isinstance(content, str) else content)
        return path

    def test_hash_and_legacy_lists_share_ordered_unicode_normalization(self):
        self.assertEqual(normalize_tags('#生活#日常，＃日常 #旅行；（咖啡）'),
                         ['生活', '日常', '旅行', '咖啡'])
        self.assertEqual(normalize_tags([' #生活#日常', '生活', 'cafe\u0301', 'café', '_日常_', 'C++']),
                         ['生活', '日常', 'café', '_日常_', 'C++'])
        self.assertEqual(normalize_tags('...开箱!!!、；  # #'), ['开箱'])

    def test_limits_are_reported_later_without_dropping_user_tags(self):
        tags = ['标签' + str(i) for i in range(15)]
        self.assertEqual(normalize_tags(' '.join('#' + tag for tag in tags)), tags)

    def test_utf8_bom_title_and_body_keep_internal_newlines(self):
        path = self.write('我的文案.MD', '\n\n  标题  \r\n\r\n正文第一段\r\n  缩进第二行\r\n', 'utf-8-sig')
        self.assertEqual(read_text_field(path, 'title'), {'value': '标题', 'filename': '我的文案.MD'})
        self.assertEqual(read_text_field(path, 'description')['value'], '标题  \n\n正文第一段\n  缩进第二行')

    def test_gb18030_tags_and_empty_file(self):
        path = self.write('标签.text', '＃生活#日常，生活；旅行', 'gb18030')
        self.assertEqual(read_text_field(path, 'tags')['value'], ['生活', '日常', '旅行'])
        path = self.write('空.txt', '\r\n  \n')
        self.assertEqual(read_text_field(path, 'title')['value'], '')
        self.assertEqual(read_text_field(path, 'description')['value'], '')

    def test_file_size_boundary_and_binary_rejection(self):
        path = self.write('边界.txt', b'a' * MAX_TEXT_BYTES)
        self.assertEqual(len(read_text_field(path, 'description')['value']), MAX_TEXT_BYTES)
        path.write_bytes(b'a' * (MAX_TEXT_BYTES + 1))
        with self.assertRaisesRegex(ValueError, '256 KiB'):
            read_text_field(path, 'description')
        path.write_bytes(b'hello\x00world')
        with self.assertRaisesRegex(ValueError, '二进制'):
            read_text_field(path, 'title')

    def test_unsupported_missing_and_invalid_fields(self):
        path = self.write('图片.png', b'just text')
        with self.assertRaisesRegex(ValueError, 'TXT'):
            read_text_field(path, 'title')
        with self.assertRaisesRegex(ValueError, '字段'):
            read_text_field(path, 'unrecognized')
        with self.assertRaisesRegex(ValueError, '找不到'):
            read_text_field(self.folder / '不存在.txt', 'description')
        with self.assertRaisesRegex(ValueError, '找不到'):
            read_text_field(self.folder / 'folder.txt', 'description')
        path = self.write('损坏.txt', b'\xff')
        with self.assertRaisesRegex(ValueError, '编码'):
            read_text_field(path, 'description')


if __name__ == '__main__':
    unittest.main()
