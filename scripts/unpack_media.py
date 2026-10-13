import hashlib,json,sys,tarfile
from pathlib import Path
root=Path(__file__).resolve().parent.parent
out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True)
for row in json.loads((root/'Vendor/MediaSources/sources.json').read_text()):
 archive=root/'Vendor/MediaSources'/row['file']
 if hashlib.sha256(archive.read_bytes()).hexdigest()!=row['sha256']:raise SystemExit('Media source checksum mismatch')
 with tarfile.open(archive) as t:t.extractall(out,filter='data')
