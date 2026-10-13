"""Selected-frame capture against a real isolated H264 video."""
import hashlib
import math
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageStat

import frame_capture
from material_probe import media_info


class FrameCapture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ffmpeg = shutil.which('ffmpeg') or '/opt/homebrew/bin/ffmpeg'
        if not Path(cls.ffmpeg).is_file():
            raise unittest.SkipTest('ffmpeg unavailable')
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.video = cls.root / 'selected-frame.mp4'
        # Two distinct scenes prove the chosen time is used, not a fixed cover.
        subprocess.run([cls.ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin',
                        '-f', 'lavfi', '-i', 'color=c=red:s=320x180:r=25:d=1',
                        '-f', 'lavfi', '-i', 'color=c=blue:s=320x180:r=25:d=1',
                        '-filter_complex', '[0:v][1:v]concat=n=2:v=1:a=0[v]', '-map', '[v]',
                        '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-movflags', '+faststart',
                        '-y', str(cls.video)], check=True, capture_output=True, timeout=30)
        cls.original_hash = hashlib.sha256(cls.video.read_bytes()).hexdigest()
        cls.duration = media_info(cls.video)['duration']

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.output = self.root / self.id().split('.')[-1]

    def tearDown(self):
        self.assertEqual(hashlib.sha256(self.video.read_bytes()).hexdigest(), self.original_hash)

    def test_chosen_times_are_different_scenes_and_original_is_preserved(self):
        first = frame_capture.capture_frame(self.video, self.output, 0.3, 'common')
        second = frame_capture.capture_frame(self.video, self.output, 1.5, 'common')
        self.assertNotEqual(first['path'], second['path'])
        self.assertEqual((first['time'], second['time']), (0.3, 1.5))
        self.assertEqual((first['width'], first['height']), (320, 180))
        with Image.open(first['path']) as image:
            red = ImageStat.Stat(image.convert('RGB')).mean
        with Image.open(second['path']) as image:
            blue = ImageStat.Stat(image.convert('RGB')).mean
        self.assertGreater(red[0], red[2] + 200)
        self.assertGreater(blue[2], blue[0] + 200)
        self.assertEqual(stat.S_IMODE(self.output.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(Path(first['path']).stat().st_mode), 0o600)
        self.assertFalse(list(self.output.glob('*.tmp.jpg')))

    def test_all_horizontal_and_portrait_ratios(self):
        for role, ratio, dimensions in [('landscape', '16:9', (1280, 720)),
                                        ('landscape', '4:3', (1200, 900)),
                                        ('portrait', '3:4', (900, 1200)),
                                        ('portrait', '9:16', (720, 1280))]:
            with self.subTest(ratio=ratio):
                result = frame_capture.capture_frame(self.video, self.output, 0.3, role, ratio)
                self.assertEqual((result['width'], result['height']), dimensions)
                self.assertEqual((result['role'], result['ratio']), (role, ratio))
                with Image.open(result['path']) as image:
                    self.assertEqual(image.size, dimensions)

    def test_subject_stays_complete_on_blurred_portrait_fill(self):
        # A centered foreground with a marked edge verifies fitting instead of cropping.
        source = self.root / 'marked-frame.mp4'
        subprocess.run([self.ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin',
                        '-f', 'lavfi', '-i', 'color=c=red:s=320x180:r=25:d=0.4',
                        '-vf', 'drawbox=x=0:y=0:w=40:h=ih:color=lime:t=fill,drawbox=x=280:y=0:w=40:h=ih:color=blue:t=fill',
                        '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-y', str(source)],
                       check=True, capture_output=True, timeout=30)
        result = frame_capture.capture_frame(source, self.output, 0.1, 'portrait', '9:16')
        with Image.open(result['path']) as image:
            left = image.getpixel((20, 640))
            right = image.getpixel((700, 640))
            self.assertGreater(left[1], left[0] + 100)
            self.assertGreater(right[2], right[0] + 100)

    def test_invalid_times_and_ratios_never_create_output(self):
        for timestamp in [True, False, None, '0.3', -0.1, math.nan, math.inf, -math.inf, self.duration + 0.01]:
            with self.subTest(timestamp=timestamp):
                with self.assertRaises(ValueError):
                    frame_capture.capture_frame(self.video, self.output, timestamp, 'common')
        for role, ratio in [('unknown', 'original'), ('portrait', '16:9'), ('landscape', '9:16'), ('common', '1:1')]:
            with self.subTest(role=role, ratio=ratio):
                with self.assertRaises(ValueError):
                    frame_capture.capture_frame(self.video, self.output, 0.3, role, ratio)
        self.assertFalse(self.output.exists())

    def test_exact_duration_is_clamped_to_decodable_end(self):
        result = frame_capture.capture_frame(self.video, self.output, self.duration, 'common')
        self.assertAlmostEqual(result['time'], self.duration - 1 / 25)
        self.assertTrue(Path(result['path']).is_file())

    def test_scrubber_close_to_end_also_returns_the_final_scene(self):
        result = frame_capture.capture_frame(self.video, self.output, self.duration - 0.001, 'common')
        self.assertAlmostEqual(result['time'], self.duration - 1 / 25)
        with Image.open(result['path']) as image:
            blue = ImageStat.Stat(image.convert('RGB')).mean
        self.assertGreater(blue[2], blue[0] + 200)

    def test_low_frame_rates_use_confirmed_last_frame_time(self):
        for fps in (10, 1):
            with self.subTest(fps=fps):
                source = self.root / f'tail-{fps}fps.mp4'
                subprocess.run([self.ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin',
                                '-f', 'lavfi', '-i', f'color=c=red:s=320x180:r={fps}:d=1',
                                '-f', 'lavfi', '-i', f'color=c=blue:s=320x180:r={fps}:d=1',
                                '-filter_complex', '[0:v][1:v]concat=n=2:v=1:a=0[v]', '-map', '[v]',
                                '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-y', str(source)],
                               check=True, capture_output=True, timeout=30)
                original = hashlib.sha256(source.read_bytes()).hexdigest()
                duration = media_info(source)['duration']
                for requested in (duration - 0.001, duration):
                    result = frame_capture.capture_frame(source, self.output, requested, 'common')
                    self.assertAlmostEqual(result['time'], duration - 1 / fps)
                    with Image.open(result['path']) as image:
                        blue = ImageStat.Stat(image.convert('RGB')).mean
                    self.assertGreater(blue[2], blue[0] + 200)
                self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), original)

    def test_audio_longer_than_video_uses_video_end(self):
        source = self.root / 'long-audio.mp4'
        subprocess.run([self.ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin',
                        '-i', str(self.video), '-f', 'lavfi', '-i', 'sine=frequency=440:duration=5',
                        '-map', '0:v', '-map', '1:a', '-c:v', 'copy', '-c:a', 'aac',
                        '-y', str(source)], check=True, capture_output=True, timeout=30)
        result = frame_capture.capture_frame(source, self.output, 4.5, 'common')
        self.assertAlmostEqual(result['time'], 1.96)
        with Image.open(result['path']) as image:
            blue = ImageStat.Stat(image.convert('RGB')).mean
        self.assertGreater(blue[2], blue[0] + 200)

    def test_variable_frame_rate_uses_timestamp_not_average_period(self):
        source = self.root / 'variable-fps.mp4'
        subprocess.run([self.ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin',
                        '-i', str(self.video), '-vf', "select='eq(n,0)+eq(n,3)+eq(n,25)+eq(n,40)'",
                        '-fps_mode', 'vfr', '-c:v', 'libx264', '-bf', '0', '-pix_fmt', 'yuv420p',
                        '-y', str(source)], check=True, capture_output=True, timeout=30)
        duration = media_info(source)['duration']
        result = frame_capture.capture_frame(source, self.output, duration - 0.001, 'common')
        self.assertAlmostEqual(result['time'], 1.6)
        with Image.open(result['path']) as image:
            blue = ImageStat.Stat(image.convert('RGB')).mean
        self.assertGreater(blue[2], blue[0] + 200)

    def test_unconfirmed_tail_rejects_instead_of_guessing(self):
        with patch.object(frame_capture.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)):
            with patch.object(frame_capture, 'media_info', return_value={'duration': 2.0}):
                with patch.object(frame_capture, '_probe', side_effect=[
                        {'streams': [{'duration': '2', 'avg_frame_rate': '1/1'}], 'format': {'start_time': '0'}},
                        {'frames': []}]):
                    with self.assertRaises(ValueError) as failure:
                        frame_capture.capture_frame(self.video, self.output, 2.0, 'common')
        self.assertIn('尾部画面无法确认', str(failure.exception.__cause__))
        self.assertEqual(list(self.output.iterdir()), [])

    def test_missing_video_is_rejected(self):
        with self.assertRaisesRegex(ValueError, '不存在'):
            frame_capture.capture_frame(self.root / 'missing.mp4', self.output, 0.3, 'common')

    def test_failure_cleans_temporary_image_and_keeps_existing_cover(self):
        self.output.mkdir(mode=0o700)
        manual = self.output / 'manual.jpg'
        Image.new('RGB', (40, 30), 'green').save(manual)
        original = manual.read_bytes()
        def fail(command, **kwargs):
            Path(command[-1]).write_bytes(b'partial frame')
            raise subprocess.TimeoutExpired(command, 30)
        with patch.object(frame_capture.subprocess, 'run', side_effect=fail):
            # Avoid patching the metadata probe, which uses the same subprocess module.
            with patch.object(frame_capture, 'media_info', return_value={'duration': self.duration}):
                with self.assertRaisesRegex(ValueError, '保持完整'):
                    frame_capture.capture_frame(self.video, self.output, 0.3, 'common')
        self.assertEqual(manual.read_bytes(), original)
        self.assertEqual(list(self.output.iterdir()), [manual])


if __name__ == '__main__':
    unittest.main()
