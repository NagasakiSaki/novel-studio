"""Development-only documentation validator; no product runtime entry. Offline."""
from pathlib import Path
import hashlib
import json
import re
import urllib.parse

ROOT = Path(__file__).resolve().parents[1]
LINK = re.compile(r'!?\[[^\]\n]*\]\((<[^>]+>|[^)\s]+)(?:\s+[^)]*)?\)')


def check():
    findings = []
    count = 0
    for file in [*ROOT.glob('*.md'), *(ROOT/'docs').rglob('*.md')]:
        content = file.read_text(encoding='utf-8')
        for match in LINK.finditer(content):
            value = match.group(1).strip('<>')
            url = urllib.parse.urlsplit(value)
            if url.scheme or not url.path or value.startswith(('/', '\\')):
                continue
            count += 1
            if not (file.parent/urllib.parse.unquote(url.path)).exists():
                findings.append(f'{file.relative_to(ROOT)}:{content[:match.start()].count(chr(10))+1}: {value}')
    manifest = ROOT/'docs/versions/1.2.4/testing/archive-manifest.json'
    if manifest.exists():
        for item in json.loads(manifest.read_bytes())['files']:
            target = ROOT/item['new']
            if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != item['after_sha256']:
                findings.append('Archived evidence changed: '+item['new'])
    return count, findings


if __name__ == '__main__':
    count, findings = check()
    print(json.dumps({'status': 'FAIL' if findings else 'PASS', 'relative_links': count,
                      'findings': findings, 'scope': 'inline local Markdown destinations and archived file hashes; not remote URLs or heading fragments'}, ensure_ascii=False, indent=2))
    raise SystemExit(bool(findings))
