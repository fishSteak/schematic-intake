"""Check this documentation pack and index without executing review code."""
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import unquote

BASE = Path(__file__).resolve().parents[1]
ROOT = BASE.parents[1]


def main():
    failures = []
    docs = sorted(BASE.glob('*.md'))
    links = 0
    for path in docs:
        text = path.read_text(encoding='utf-8')
        if text.count('```') % 2:
            failures.append(path.name + ': unbalanced code fence')
        for target in re.findall(r'\]\(([^\n)]+)\)', text):
            if '://' in target or target.startswith('#'):
                continue
            destination = (path.parent / unquote(target.split('#', 1)[0])).resolve()
            links += 1
            if not destination.is_relative_to(ROOT.resolve()) or not destination.exists():
                failures.append(path.name + ': invalid link ' + target)
    index = json.loads((BASE / 'code-index.json').read_text(encoding='utf-8'))
    paths = [row['path'] for row in index['files']]
    if len(paths) != len(set(paths)) or len(paths) != index['file_count']:
        failures.append('Index count or duplicate paths')
    for row in index['files']:
        source = (ROOT / row['path']).resolve()
        if not source.is_relative_to(ROOT.resolve()) or not source.is_file():
            failures.append('Invalid indexed source: ' + row['path'])
            continue
        if hashlib.sha256(source.read_bytes()).hexdigest() != row['sha256']:
            failures.append('Stale index: ' + row['path'])
    print(json.dumps({'documents': len(docs), 'checked_links': links,
                      'indexed_files': len(paths), 'errors': failures}, ensure_ascii=False))
    if failures:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
