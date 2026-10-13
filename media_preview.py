"""Bounded local video streaming for the registered material preview route."""
import os
import re
from pathlib import Path

from material_probe import media_info


CHUNK_SIZE = 64 * 1024
VIDEO_MIME = {
    '.mp4': 'video/mp4', '.m4v': 'video/mp4', '.mov': 'video/quicktime',
    '.webm': 'video/webm', '.avi': 'video/x-msvideo', '.mkv': 'video/x-matroska',
}


def describe_video(path):
    """Return the metadata needed by the player, using the existing probe."""
    info = media_info(path)
    return {'duration': info['duration'], 'width': info['width'],
            'height': info['height'], 'bytes': info['bytes'],
            'codec': info.get('codec_name', '')}


def _byte_range(value, size):
    match = re.fullmatch(r'bytes=(\d*)-(\d*)', value.strip(), re.IGNORECASE)
    if not match or size == 0:
        raise ValueError('Invalid byte range')
    first, last = match.groups()
    if not first:
        if not last or int(last) <= 0:
            raise ValueError('Invalid suffix range')
        return max(0, size - int(last)), size - 1
    start = int(first)
    end = int(last) if last else size - 1
    if start >= size or start > end:
        raise ValueError('Range outside file')
    return start, min(end, size - 1)


def _headers(handler, status, length, mime, content_range=None):
    handler.send_response(status)
    handler.send_header('Content-Type', mime)
    handler.send_header('Content-Length', str(length))
    handler.send_header('Accept-Ranges', 'bytes')
    handler.send_header('Cache-Control', 'private, no-store')
    handler.send_header('X-Content-Type-Options', 'nosniff')
    if content_range is not None:
        handler.send_header('Content-Range', content_range)
    handler.end_headers()


def stream_video(handler, path, head=False):
    """Stream an already authorized file; the caller owns the asset whitelist.

    A single range is supported so native video seeking does not buffer the
    entire source. Invalid and multiple ranges are rejected with an empty 416.
    """
    source = Path(path)
    try:
        stream = source.open('rb')
    except (FileNotFoundError, IsADirectoryError, PermissionError):
        handler.send_error(404)
        return
    with stream:
        size = os.fstat(stream.fileno()).st_size
        mime = VIDEO_MIME.get(source.suffix.lower(), 'application/octet-stream')
        value = handler.headers.get('Range')
        start, end = 0, size - 1
        if value is not None:
            try:
                start, end = _byte_range(value, size)
            except (ValueError, OverflowError):
                _headers(handler, 416, 0, mime, 'bytes */' + str(size))
                return
        length = max(0, end - start + 1)
        _headers(handler, 206 if value is not None else 200, length, mime,
                 f'bytes {start}-{end}/{size}' if value is not None else None)
        if head or length == 0:
            return
        stream.seek(start)
        try:
            while length:
                block = stream.read(min(CHUNK_SIZE, length))
                if not block:
                    break
                handler.wfile.write(block)
                length -= len(block)
        except (BrokenPipeError, ConnectionResetError):
            # Seeking or selecting a different video commonly closes a request.
            return
