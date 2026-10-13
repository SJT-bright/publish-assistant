import os,subprocess,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from runtime_support import open_private_read,private_stat_ok,process_alive,stop_runner,runner_command,media_tool
from draft_execution import _checkpoint_atomic_create,_checkpoint_private_bytes

class NativeRuntimeTests(unittest.TestCase):
 def test_binary_checkpoint_key_and_exclusive_proof(self):
  with tempfile.TemporaryDirectory() as tmp:
   path=Path(tmp)/'key';content=bytes(range(32));path.write_bytes(content);path.chmod(0o600)
   self.assertEqual(_checkpoint_private_bytes(path,exact_size=32,max_size=32),content)
   proof=Path(tmp)/'proof';_checkpoint_atomic_create(proof,{'name':'中文验证'})
   self.assertIn('中文验证',_checkpoint_private_bytes(proof).decode())
   with self.assertRaises(FileExistsError):_checkpoint_atomic_create(proof,{'name':'changed'})
 def test_private_reader_rejects_symbolic_link(self):
  with tempfile.TemporaryDirectory() as tmp:
   key=Path(tmp)/'key';key.write_bytes(b'secret');key.chmod(0o600);link=Path(tmp)/'link'
   try:link.symlink_to(key)
   except OSError:self.skipTest('OS user has no symlink privilege')
   from uploader.draft_evidence import CheckpointError
   with self.assertRaises(CheckpointError):_checkpoint_private_bytes(link)
 def test_process_probe_and_cancel_owned_runner(self):
  process=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'],start_new_session=True)
  try:
   self.assertTrue(process_alive(process.pid));stop_runner(process);process.wait(timeout=10)
   self.assertFalse(process_alive(process.pid))
  finally:
   if process.poll() is None:process.kill();process.wait()
 def test_runner_uses_current_python_and_frozen_dispatch(self):
  self.assertEqual(runner_command()[0],sys.executable)
  with patch.object(sys,'frozen',True,create=True):self.assertEqual(runner_command(),[sys.executable,'runner'])
 def test_missing_packaged_tool_does_not_use_unrelated_system_tool(self):
  with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'SAU_MEDIA_TOOLS':tmp}):
   with self.assertRaises(ValueError):media_tool('ffprobe')
if __name__=='__main__':unittest.main()
