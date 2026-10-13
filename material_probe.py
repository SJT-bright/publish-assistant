"""Local media inspection and cached cover extraction. No network access."""
import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path
from runtime_support import media_tool, subprocess_options


def media_info(video):
    tool = media_tool('ffprobe')
    if not Path(tool).is_file():
        raise ValueError('本机视频检查组件不可用，上传前已停止。')
    result = subprocess.run([tool, '-v', 'error', '-select_streams', 'v:0', '-show_entries',
                             'stream=codec_name,width,height:format=duration,size', '-of', 'json', str(video)],
                            capture_output=True, text=True, encoding='utf-8', timeout=20, **subprocess_options())
    if result.returncode:
        raise ValueError('视频无法读取或已损坏，请重新导出。')
    data = json.loads(result.stdout)
    streams = data.get('streams') or []
    if not streams or not streams[0].get('width') or not streams[0].get('height'):
        raise ValueError('文件中没有可读取的视频画面。')
    duration = float(data.get('format', {}).get('duration', 0))
    if duration <= 0:
        raise ValueError('视频时长无法确认。')
    return {**streams[0], 'duration': duration, 'bytes': int(data.get('format', {}).get('size', 0))}


def validate_image(path):
    if not path:
        return
    from PIL import Image
    try:
        with Image.open(path) as image:
            image.verify()
    except Exception as exc:
        raise ValueError('封面图片无法读取：' + Path(path).name) from exc


def generate_cover(video, output_dir, video_sha256):
    """Extract once per video; preserve original media and manual covers."""
    info = media_info(video)
    tool = media_tool('ffmpeg')
    if not Path(tool).is_file():
        raise ValueError('本机封面生成组件不可用。')
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = directory / (video_sha256 + '.jpg')
    if target.exists():
        validate_image(target)
        return str(target)
    temporary = directory / (video_sha256 + '.' + uuid.uuid4().hex + '.tmp.jpg')
    try:
        subprocess.run([tool, '-hide_banner', '-loglevel', 'error', '-nostdin', '-ss',
                        str(min(1.0, info['duration'] / 3)), '-i', str(video), '-frames:v', '1',
                        '-vf', 'scale=1280:1280:force_original_aspect_ratio=decrease', '-q:v', '3',
                        '-y', str(temporary)], capture_output=True, check=True, timeout=30, **subprocess_options())
        validate_image(temporary)
        os.chmod(temporary, 0o600)
        temporary.replace(target)
        return str(target)
    except (subprocess.SubprocessError, OSError) as exc:
        raise ValueError('自动封面生成失败；原视频保持完整。') from exc
    finally:
        temporary.unlink(missing_ok=True)
