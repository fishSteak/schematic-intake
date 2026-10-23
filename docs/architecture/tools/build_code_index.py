"""Build a read-only production-code inventory; never inspect board results."""
import ast
import collections
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[3]
DEST = Path(__file__).resolve().parents[1] / 'code-index.json'
SCOPES = ['agent_review', 'kernel', 'solve_prog', 'rules', 'scripts',
          'report_ui/src', 'sdk', 'vendor/expert_html']
SKIP = {'node_modules', '__pycache__', 'dist', '_archive', 'pdfjs', 'regression'}
SUFFIXES = {'.py', '.mjs', '.js', '.ts', '.vue', '.json', '.md'}


def describe(path):
    data = path.read_bytes()
    text = data.decode('utf-8-sig')
    result = {'path': path.relative_to(ROOT).as_posix(), 'bytes': len(data),
              'sha256': hashlib.sha256(data).hexdigest(),
              'imports': [], 'symbols': []}
    if path.suffix == '.py':
        try:
            tree = ast.parse(text)
        except SyntaxError as error:
            result['parse_error'] = {'line': error.lineno, 'message': error.msg}
            return result
        result['summary'] = (ast.get_docstring(tree) or '').split('\n')[0]
        for node in tree.body:
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                result['symbols'].append({'name': node.name, 'line': node.lineno,
                                          'kind': type(node).__name__})
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                result['imports'].extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                result['imports'].append('.' * node.level + (node.module or ''))
    elif path.suffix in {'.mjs', '.js', '.ts', '.vue'}:
        result['imports'] = re.findall(
            r'''(?:\bfrom\s*|\bimport\s*\(\s*|\bimport\s*)["']([^"']+)["']''', text)
    result['imports'] = sorted(set(result['imports']))
    return result


def main():
    records = []
    for scope in SCOPES:
        start = ROOT / scope
        for path in sorted(start.rglob('*')):
            relative = path.relative_to(start)
            if any(part in SKIP for part in relative.parts):
                continue
            if not path.is_file() or path.suffix not in SUFFIXES or path.is_symlink():
                continue
            if not path.resolve().is_relative_to(ROOT.resolve()):
                raise RuntimeError('Source escaped repository: ' + str(path))
            records.append(describe(path))
    records.sort(key=lambda row: row['path'])
    counts = collections.Counter(Path(row['path']).suffix for row in records)
    payload = {'version': 1, 'scope': SCOPES,
               'limitations': 'Static symbols and import declarations only; not a complete call graph. Excludes board data, dependencies, generated reports and archives.',
               'file_count': len(records), 'extension_counts': dict(sorted(counts.items())),
               'files': records}
    DEST.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'index': str(DEST), 'files': len(records),
                      'python_parse_errors': sum('parse_error' in row for row in records)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
