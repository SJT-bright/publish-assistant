import unittest

from uploader.dy_editor import normalize_douyin_editor_text


class DouyinEditorTextNormalizationTests(unittest.TestCase):
    def test_removes_editor_zero_width_spaces_but_preserves_emoji_zwj(self):
        expected = "视频👩‍❤️‍💋‍👩\n#城市漫步"
        actual_dom_text = "视\u200b频👩‍❤️‍💋‍👩\u200b\n#城市漫步\n"

        normalized = normalize_douyin_editor_text(actual_dom_text)

        self.assertEqual(normalized, expected)
        self.assertIn("\u200d", normalized)
        self.assertNotIn("\u200b", normalized)

    def test_normalizes_crlf_and_trailing_lf_without_trimming_spaces(self):
        value = "  第一行  \r\n第二行  \n\n"

        self.assertEqual(
            normalize_douyin_editor_text(value),
            "  第一行  \n第二行  ",
        )

    def test_different_body_line_breaks_and_topics_remain_different(self):
        expected = normalize_douyin_editor_text("第一行\n第二行\n#城市漫步\n")
        different_body = normalize_douyin_editor_text("第一行\n改过的第二行\n#城市漫步\n")
        different_line_breaks = normalize_douyin_editor_text("第一行第二行\n#城市漫步\n")
        different_topic = normalize_douyin_editor_text("第一行\n第二行\n#周末探店\n")

        self.assertNotEqual(expected, different_body)
        self.assertNotEqual(expected, different_line_breaks)
        self.assertNotEqual(expected, different_topic)

    def test_crlf_and_lf_are_equivalent_but_internal_blank_lines_remain(self):
        crlf = normalize_douyin_editor_text("上\r\n\r\n下\r\n")
        lf = normalize_douyin_editor_text("上\n\n下\n")

        self.assertEqual(crlf, "上\n\n下")
        self.assertEqual(crlf, lf)

    def test_non_text_readback_fails_closed(self):
        with self.assertRaises(TypeError):
            normalize_douyin_editor_text(None)


if __name__ == "__main__":
    unittest.main()
