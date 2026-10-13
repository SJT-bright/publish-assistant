import unittest

from uploader.ks_caption import decode_ks_caption


class KSCaptionTests(unittest.TestCase):
    def test_actual_editor_paragraphs_and_escaped_literal_text(self):
        self.assertEqual(decode_ks_caption('标题<div>第一行</div><div>第二行 #科普 #验收</div>'),
                         '标题\n第一行\n第二行 #科普 #验收')
        self.assertEqual(decode_ks_caption('&lt;literal&gt;<div>A &amp; B<br>C</div>'),
                         '<literal>\nA & B\nC')
        self.assertEqual(decode_ks_caption('纯文字 #科普'), '纯文字 #科普')

    def test_unknown_or_malformed_markup_never_discards_content(self):
        for value in ('A<script>B</script>', '<div data-x="1">A</div>',
                      '<div>A', 'A</div>', '<span is-tag="true">#科普</span>',
                      '<!-- hidden -->A', None):
            with self.subTest(value=value):
                self.assertIsNone(decode_ks_caption(value))
