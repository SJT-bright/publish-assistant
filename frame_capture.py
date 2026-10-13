"""Capture a selected local video frame without changing the source material."""
import json
import math
import os
import shutil
import subprocess
import time
import uuid
from fractions import Fraction
from pathlib import Path

from material_probe import media_info, validate_image
from runtime_support import media_tool, subprocess_options


DIMENSIONS = {'16:9': (1280, 720), '4:3': (1200, 900),
              '3:4': (900, 1200), '9:16': (720, 1280)}
ROLE_RATIOS = {'common': {'original', *DIMENSIONS},
               'landscape': {'original', '16:9', '4:3'},
               'portrait': {'original', '3:4', '9:16'}}


def _filter(ratio):
    if ratio == 'original':
        return "scale=w='min(1920,iw)':h='min(1920,ih)':force_original_aspect_ratio=decrease,setsar=1"
    width, height = DIMENSIONS[ratio]
    return (f'[0:v]split=2[bg][fg];'
            f'[bg]scale={width}:{height}:force_original_aspect_ratio=increase,'
            f'crop={width}:{height},gblur=sigma=20:steps=3[background];'
            f'[fg]scale={width}:{height}:force_original_aspect_ratio=decrease[foreground];'
            f'[background][foreground]overlay=(W-w)/2:(H-h)/2,setsar=1[out]')


def _remaining(deadline):
    seconds = deadline - time.monotonic()
    if seconds <= 0:
        raise ValueError('封面截取超时，请选择更早的画面后重试。')
    return seconds


def _probe(source, entries, deadline, interval=None):
    tool = media_tool('ffprobe')
    if not Path(tool).is_file():
        raise ValueError('本机视频检查组件不可用。')
    command = [tool, '-v', 'error', '-select_streams', 'v:0']
    if interval is not None:
        command += ['-read_intervals', interval]
    command += ['-show_entries', entries, '-of', 'json', str(source)]
    result = subprocess.run(command, capture_output=True, text=True, check=True,
                            timeout=min(10, _remaining(deadline)))
    return json.loads(result.stdout)


def _tail_frame_time(source, requested, deadline):
    """Confirm the available tail frame only when normal seeking has no frame.

    Stream duration prevents a longer audio track from sending the probe beyond
    the video. Frame timestamps, rather than an assumed constant frame period,
    handle both low frame rates and VFR. The interval is local to the requested
    tail; failed or ambiguous probes reject the capture instead of guessing.
    """
    metadata = _probe(source, 'stream=start_time,duration,avg_frame_rate:format=start_time', deadline)
    streams = metadata.get('streams') or []
    if not streams:
        raise ValueError('视频尾部画面无法确认，请选择更早的画面。')
    stream = streams[0]
    try:
        start = float(metadata.get('format', {}).get('start_time', 0))
    except (TypeError, ValueError):
        start = 0.0
    if not math.isfinite(start):
        raise ValueError('视频尾部画面无法确认，请选择更早的画面。')
    target = requested
    try:
        video_duration = float(stream.get('duration', 'nan'))
        video_start = float(stream.get('start_time', start))
        if math.isfinite(video_duration) and math.isfinite(video_start) and video_duration > 0:
            target = min(target, video_start - start + video_duration)
    except (TypeError, ValueError):
        pass
    try:
        rate = float(Fraction(stream.get('avg_frame_rate', '0/1')))
    except (TypeError, ValueError, ZeroDivisionError, OverflowError):
        rate = 0.0
    window = max(1.0, min(5.0, 3 / rate)) if rate > 0 else 2.0
    first = max(0.0, target - window) + start
    last = target + start + 0.01
    frames = _probe(source, 'frame=best_effort_timestamp_time', deadline,
                    f'{first:.9f}%{last:.9f}').get('frames') or []
    candidates = []
    for frame in frames:
        try:
            seconds = float(frame['best_effort_timestamp_time']) - start
        except (KeyError, TypeError, ValueError):
            continue
        if math.isfinite(seconds) and -0.000001 <= seconds <= requested + 0.000001:
            candidates.append(max(0.0, seconds))
    if not candidates:
        raise ValueError('视频尾部画面无法确认，请选择更早的画面。')
    return max(candidates)


def capture_frame(video, outdir, timestamp, role, ratio='original'):
    """Save a unique JPEG of the selected frame and return its actual metadata.

    Explicit ratios place the complete foreground on a blurred fill. Original
    ratio preserves the source composition and limits only the maximum size.
    """
    deadline = time.monotonic() + 30
    if not isinstance(role, str) or not isinstance(ratio, str) or role not in ROLE_RATIOS or ratio not in ROLE_RATIOS[role]:
        raise ValueError('封面方向与比例不匹配，请重新选择。')
    if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)):
        raise ValueError('截取时间必须是有效的秒数。')
    try:
        seconds = float(timestamp)
    except (OverflowError, ValueError) as exc:
        raise ValueError('截取时间必须是有效的秒数。') from exc
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError('截取时间必须是有效的秒数。')
    source = Path(video).expanduser().resolve()
    if not source.is_file():
        raise ValueError('视频文件不存在，请重新导入。')
    info = media_info(source)
    duration = float(info['duration'])
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError('视频时长无法确认。')
    if seconds > duration:
        raise ValueError('截取时间超出了视频时长。')
    tool = media_tool('ffmpeg')
    if not Path(tool).is_file():
        raise ValueError('本机封面截取组件不可用。')
    directory = Path(outdir).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(directory, 0o700)
    unique = uuid.uuid4().hex
    target = directory / f'frame-{role}-{unique}.jpg'
    temporary = directory / f'.frame-{unique}.tmp.jpg'
    command = [tool, '-hide_banner', '-loglevel', 'error', '-nostdin',
               '-ss', format(seconds, '.9f'), '-i', str(source)]
    if ratio == 'original':
        command += ['-vf', _filter(ratio)]
    else:
        command += ['-filter_complex', _filter(ratio), '-map', '[out]']
    command += ['-frames:v', '1', '-pix_fmt', 'yuvj420p', '-q:v', '2', '-y', str(temporary)]
    try:
        subprocess.run(command, capture_output=True, check=True, timeout=_remaining(deadline), **subprocess_options())
        if not temporary.is_file() or temporary.stat().st_size == 0:
            seconds = _tail_frame_time(source, seconds, deadline)
            # ffprobe decimal timestamps can round up by a fraction of a
            # microsecond; seeking just before them preserves that exact frame.
            command[command.index('-ss') + 1] = format(max(0.0, seconds - 0.000001), '.9f')
            subprocess.run(command, capture_output=True, check=True, timeout=_remaining(deadline), **subprocess_options())
        validate_image(temporary)
        from PIL import Image
        with Image.open(temporary) as image:
            width, height = image.size
        os.chmod(temporary, 0o600)
        temporary.replace(target)
        return {'path': str(target), 'time': seconds, 'width': width, 'height': height,
                'role': role, 'ratio': ratio}
    except ValueError as exc:
        raise ValueError(str(exc) + ' 原视频和已有封面保持完整。') from exc
    except (subprocess.SubprocessError, OSError) as exc:
        raise ValueError('封面截取失败；原视频和已有封面保持完整。') from exc
    finally:
        temporary.unlink(missing_ok=True)
