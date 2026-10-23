import unittest
from unittest.mock import call, patch

from PIL import ImageFont

from agent_review.stress_path_render import _font


class StressPathFontTests(unittest.TestCase):
    def test_cjk_and_engineering_labels_render_at_requested_size(self):
        for bold in (False, True):
            with self.subTest(bold=bold):
                font = _font(30, bold=bold)
                self.assertEqual(font.size, 30)
                self.assertNotEqual(bytes(font.getmask("中")), bytes(font.getmask("文")))
                missing = bytes(font.getmask("\U0010ffff"))
                for char in "中文R1WΩ≤≥±·−⁻⁶²×℃":
                    self.assertTrue(bytes(font.getmask(char)) != missing, f"Missing glyph: {char}")
                self.assertGreater(font.getbbox("分析关系")[3] - font.getbbox("分析关系")[1], 15)

    def test_partial_host_font_uses_complete_unicode_fallback(self):
        class PartialCJKFont:
            def getmask(self, text):
                return text.encode() if text in "中文" else b"missing"
        partial = PartialCJKFont()
        real_load = ImageFont.truetype

        def load(source, size):
            if source.endswith("/unifont.otf"):
                return real_load(source, size)
            return partial

        with patch("agent_review.stress_path_render.ImageFont.truetype", side_effect=load):
            font = _font(30)
        self.assertIsNot(font, partial)
        self.assertEqual(font.size, 30)
        missing = bytes(font.getmask("\U0010ffff"))
        self.assertTrue(all(bytes(font.getmask(char)) != missing for char in "中文R1WΩ≤≥±·−⁻⁶²×℃"))

    def test_windows_font_remains_available_at_requested_size(self):
        for bold, alias in ((False, "msyh.ttc"), (True, "msyhbd.ttc")):
            with self.subTest(bold=bold):
                class Font:
                    def getmask(self, text):
                        return text.encode()
                loaded = Font()
                with patch("agent_review.stress_path_render.ImageFont.truetype", return_value=loaded) as load:
                    self.assertIs(_font(27, bold=bold), loaded)
                load.assert_called_once_with(alias, 27)

    def test_unavailable_fonts_fall_back_with_requested_size(self):
        fallback = object()
        with patch("agent_review.stress_path_render.ImageFont.truetype", side_effect=OSError("unavailable")), \
                patch("agent_review.stress_path_render.ImageFont.load_default", return_value=fallback) as load:
            self.assertIs(_font(29), fallback)
        load.assert_called_once_with(size=29)

    def test_older_pillow_fixed_size_fallback_remains_safe(self):
        fallback = object()
        with patch("agent_review.stress_path_render.ImageFont.truetype", side_effect=OSError("unavailable")), \
                patch("agent_review.stress_path_render.ImageFont.load_default",
                      side_effect=[TypeError("unexpected keyword size"), fallback]) as load:
            self.assertIs(_font(29), fallback)
        self.assertEqual(load.call_args_list, [call(size=29), call()])


if __name__ == "__main__":
    unittest.main()
