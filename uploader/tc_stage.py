"""Private same-byte, same-name native input for a fresh normal TC upload.

841.d575b04c.js:864180 defines the cache key as
name:lastModified:size:type:md5; 870680 compares that full key exactly.
Only the private copy's mtime changes. No SDK, cache or original-file write.
"""
import asyncio
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit


class TCStageError(RuntimeError):
    pass


def _clock_path():
    # This small private counter carries no media/account data. It must remain
    # across tasks/processes so even same-millisecond imports get distinct keys.
    return Path(tempfile.gettempdir()) / '.publish-assistant-tc-stage-ms'


def _fresh_millisecond(source_ms):
    fd = os.open(_clock_path(), os.O_RDWR | os.O_CREAT, 0o600)
    with os.fdopen(fd, 'r+b') as stream:
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            stream.seek(0)
            value = stream.read(32)
            if value and not re.fullmatch(rb'[0-9]+\n?', value):
                raise TCStageError('tc_stage_clock_unverified')
            previous = int(value or b'0')
            selected = max(time.time_ns() // 1000000, previous + 1)
            if selected == source_ms:
                selected += 1
            if not 0 < selected < 2 ** 53:
                raise TCStageError('tc_stage_clock_unverified')
            stream.seek(0)
            stream.write((str(selected) + '\n').encode('ascii'))
            stream.truncate()
            stream.flush()
            return selected
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _signature(value):
    if not stat.S_ISREG(value.st_mode):
        raise TCStageError('tc_stage_source_unverified')
    return tuple(getattr(value, key) for key in
                 ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns', 'st_mode'))


def _cancelled(cancel):
    if cancel is not None and cancel.is_set():
        raise TCStageError('tc_stage_cancelled')


def _hash(path, signature, cancel=None):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        if _signature(os.fstat(stream.fileno())) != signature:
            raise TCStageError('tc_stage_source_changed')
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            _cancelled(cancel)
            digest.update(chunk)
        if _signature(os.fstat(stream.fileno())) != signature:
            raise TCStageError('tc_stage_source_changed')
    if _signature(path.stat()) != signature:
        raise TCStageError('tc_stage_source_changed')
    return digest.hexdigest()


class TCNativeStage:
    def __init__(self, source, path, directory, source_signature, staged_signature, digest, last_modified_ms):
        self.source, self.path, self._directory = source, path, directory
        self.source_signature, self.staged_signature = source_signature, staged_signature
        self.digest, self.last_modified_ms = digest, last_modified_ms
        self.closed = False
        self._lock = threading.Lock()
        self.record = None
        self._unwatch = None

    def assert_current(self):
        try:
            current = (not self.closed and _signature(self.source.stat()) == self.source_signature and
                       _signature(self.path.stat()) == self.staged_signature)
        except OSError:
            current = False
        if not current:
            raise TCStageError('tc_stage_source_changed')

    def verify(self):
        with self._lock:
            try:
                self.assert_current()
                if (_hash(self.source, self.source_signature) != self.digest or
                        _hash(self.path, self.staged_signature) != self.digest):
                    raise TCStageError('tc_stage_source_changed')
                self.assert_current()
            except OSError:
                raise TCStageError('tc_stage_source_changed') from None

    def close(self):
        with self._lock:
            if self.closed:
                return
            if self._unwatch is not None:
                self._unwatch()
            try:
                shutil.rmtree(self._directory)
            except FileNotFoundError:
                pass
            except OSError:
                raise TCStageError('tc_stage_cleanup_failed') from None
            if self.record is not None:
                try:
                    value=json.loads(self.record.read_text())
                    if value.get('directory')==self._directory.name:
                        self.record.unlink()
                except FileNotFoundError:
                    pass
                except (OSError, ValueError):
                    raise TCStageError('tc_stage_cleanup_failed') from None
            self.closed = True


def _create(source_path, expected, cancel, recovery_root=None, scope=None):
    directory = None
    created_record = None
    try:
        source = Path(source_path).expanduser().absolute()
        before = source.stat()
        signature = _signature(before)
        if (not isinstance(expected, dict) or expected.get('name') != source.name or
                type(expected.get('size')) is not int or expected['size'] <= 0 or
                expected['size'] != before.st_size or
                not re.fullmatch(r'[0-9a-f]{64}', str(expected.get('sha256')))):
            raise TCStageError('tc_stage_source_unverified')
        _cancelled(cancel)
        if recovery_root is not None:
            recovery_root.mkdir(parents=True,exist_ok=True,mode=0o700)
            if (recovery_root/'active.json').exists():
                raise TCStageError('tc_stage_recovery_already_present')
        directory = Path(tempfile.mkdtemp(prefix='sau-tc-native-',dir=recovery_root))
        staged = directory / source.name
        copied = hashlib.sha256()
        with source.open('rb') as original, os.fdopen(os.open(staged, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), 'wb') as output:
            if _signature(os.fstat(original.fileno())) != signature:
                raise TCStageError('tc_stage_source_changed')
            for chunk in iter(lambda: original.read(1024 * 1024), b''):
                _cancelled(cancel)
                output.write(chunk)
                copied.update(chunk)
            if _signature(os.fstat(original.fileno())) != signature:
                raise TCStageError('tc_stage_source_changed')
        if (copied.hexdigest() != expected['sha256'] or _hash(source, signature, cancel) != expected['sha256'] or
                staged.stat().st_size != before.st_size):
            raise TCStageError('tc_stage_source_changed')
        stamp = _fresh_millisecond(before.st_mtime_ns // 1000000)
        os.utime(staged, ns=(time.time_ns(), stamp * 1000000))
        staged_signature = _signature(staged.stat())
        if staged.stat().st_mtime_ns // 1000000 != stamp or _hash(staged, staged_signature, cancel) != expected['sha256']:
            raise TCStageError('tc_stage_private_file_unverified')
        _cancelled(cancel)
        if _signature(source.stat()) != signature:
            raise TCStageError('tc_stage_source_changed')
        stage = TCNativeStage(source, staged, directory, signature, staged_signature, expected['sha256'], stamp)
        if recovery_root is not None:
            record=recovery_root/'active.json'
            data={'version':1,'directory':directory.name,'source':str(source),'source_signature':list(signature),
                  'staged_signature':list(staged_signature),'sha256':expected['sha256'],'name':source.name,
                  'size':before.st_size,'last_modified_ms':stamp,'scope':scope}
            descriptor=os.open(record,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            created_record=record
            with os.fdopen(descriptor,'w') as stream:
                json.dump(data,stream,ensure_ascii=False,separators=(',',':'))
            stage.record=record
        directory = None
        created_record = None
        return stage
    except TCStageError:
        raise
    except (OSError, TypeError, ValueError):
        raise TCStageError('tc_stage_source_unverified') from None
    finally:
        if directory is not None:
            shutil.rmtree(directory, ignore_errors=True)
        if created_record is not None:
            created_record.unlink(missing_ok=True)


async def create_tc_native_stage(source_path, expected, *, recovery_root=None, scope=None):
    cancel = threading.Event()
    task = asyncio.create_task(asyncio.to_thread(_create, source_path, dict(expected or {}), cancel,
           Path(recovery_root) if recovery_root is not None else None,scope))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        cancel.set()
        # Even a second cancellation cannot leave a completed copy ownerless.
        def discard(done):
            try:
                done.result().close()
            except (Exception, asyncio.CancelledError):
                pass
        task.add_done_callback(discard)
        try:
            await asyncio.shield(task)
        except (Exception, asyncio.CancelledError):
            pass
        raise


async def release_tc_native_stage(stage):
    if stage is not None:
        await asyncio.shield(asyncio.create_task(asyncio.to_thread(stage.close)))


def tc_stage_prepared_page(page):
    try:
        value=urlsplit(page.url)
        return value.scheme=='https' and value.netloc=='channels.weixin.qq.com' and value.path.rstrip('/')=='/platform/post/create'
    except (AttributeError,TypeError,ValueError):
        return False


def stage_recovery_roots(page):
    runtime=getattr(page,'_sau_checkpoint_runtime',None)
    if runtime is None:
        return []
    paths=[getattr(runtime,'resume',None),getattr(runtime,'output',None)]
    return list(dict.fromkeys(Path(path).parent/'.tencent-native-input' for path in paths if path is not None))


def _restore(root, source_path, expected, scope):
    record=root/'active.json'
    if not record.exists():
        return None
    try:
        info=record.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size>16384 or (os.name!='nt' and info.st_mode&0o077):
            raise TCStageError('tc_stage_recovery_unverified')
        data=json.loads(record.read_text())
        directory=data['directory']
        if (data.get('version')!=1 or data.get('scope')!=scope or
                not isinstance(directory,str) or not re.fullmatch(r'sau-tc-native-[a-z0-9_]+',directory) or
                data['source']!=str(Path(source_path).expanduser().absolute()) or
                any(data.get(key)!=expected.get(key) for key in ('name','size','sha256'))):
            raise TCStageError('tc_stage_recovery_unverified')
        stage=TCNativeStage(Path(data['source']),root/directory/data['name'],root/directory,
           tuple(data['source_signature']),tuple(data['staged_signature']),data['sha256'],data['last_modified_ms'])
        stage.record=record
        if stage._directory.is_symlink() or stage.path.is_symlink():
            raise TCStageError('tc_stage_recovery_unverified')
        stage.verify()
        return stage
    except (OSError,KeyError,TypeError,ValueError):
        raise TCStageError('tc_stage_recovery_unverified') from None


async def restore_tc_native_stage(page, source_path, expected, scope):
    runtime=getattr(page,'_sau_checkpoint_runtime',None)
    if runtime is not None and getattr(runtime,'resume',None) is not None:
        # ExecutionCheckpoint.restore owns HMAC validation and actual media /
        # account restore. The display wrapper must retain that SAME identity;
        # this recovery file never authorizes rebasing a job or nonce.
        from uploader.draft_evidence import IDENTITY_KEYS
        session=getattr(page,'_sau_draft_evidence',None)
        proof=getattr(runtime,'proof',None)
        identity=proof.get('identity') if isinstance(proof,dict) else None
        asset=proof.get('assets',{}).get('video') if isinstance(proof,dict) else None
        if (session is None or getattr(session,'_checkpoint_restored_at',None) is None or
                getattr(session,'closed',True) or getattr(session,'platform',None)!='tencent' or
                not isinstance(identity,dict) or identity.get('platform_id')!='tencent' or
                any(key not in identity or identity[key]!=session.expected.get(key) for key in IDENTITY_KEYS) or
                not isinstance(asset,dict) or any(asset.get(key)!=expected.get(key) for key in ('name','size','sha256')) or
                scope!={key:identity.get(key) for key in ('job_id','receipt_nonce')}):
            raise TCStageError('tc_stage_resume_scope_unverified')
    for root in stage_recovery_roots(page):
        stage=await asyncio.to_thread(_restore,root,source_path,expected,scope)
        if stage is not None:
            existing=getattr(page,'_sau_tc_native_stage',None)
            if existing is not None and not existing.closed and existing.path==stage.path:
                await asyncio.to_thread(existing.verify)
                return existing
            return stage
    return None


def watch_tc_native_stage(page,stage):
    # When this task is alive, page departure invalidates the retained File.
    # A failed worker's on-disk recovery remains private after worker exit.
    if stage._unwatch is not None:
        stage._unwatch()
    def clean(*_args):
        if not stage.closed:
            asyncio.create_task(release_tc_native_stage(stage))
    def navigated(frame):
        if frame is page.main_frame and not tc_stage_prepared_page(page):
            clean()
    page.on('close',clean)
    page.on('framenavigated',navigated)
    def unwatch():
        if stage._unwatch is unwatch:
            page.remove_listener('close',clean)
            page.remove_listener('framenavigated',navigated)
            stage._unwatch=None
    stage._unwatch=unwatch
    return unwatch
