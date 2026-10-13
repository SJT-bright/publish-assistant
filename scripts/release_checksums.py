import hashlib
from pathlib import Path
root=Path(__file__).resolve().parent.parent/'release-assets'
items=sorted(p for p in root.iterdir() if p.is_file() and (p.suffix in ('.exe','.zip') or p.name.endswith('validation.json')))
(root/'SHA256SUMS-Windows.txt').write_text(''.join(hashlib.file_digest(p.open('rb'),'sha256').hexdigest()+'  '+p.name+'\n' for p in items),encoding='utf-8')
