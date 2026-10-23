"""Package development sources and existing evidence without local credentials."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {'.git', 'node_modules', '__pycache__', '.venv', 'venv', '.codex', '.pytest_cache', '_tmp_ppt'}
SECRET_NAMES = {'auth.json', 'credentials.json', 'credentials', '.npmrc', '.pypirc', 'id_rsa', 'id_ed25519', 'id_ecdsa'}
CORE_DIRS = {'agent_review', 'kernel', 'solve_prog', 'rules', 'scripts', 'tests', 'report_ui', 'sdk', 'vendor', 'packages', 'skills', 'knowledge', 'materials', 'migration_baseline', 'docs', 'boards'}
CORE_ROOT_FILES = {'AGENTS.md', 'README.md', '.gitignore', 'package.json', 'package-lock.json', 'requirements.txt'}
REPORT_DIRS = {'stress_paths', 'datasheets', 'sch', 'pdfjs', 'page_evidence', 'research_assets', 'agent_jobs', 'evidence', 'evidence_store', 'independent_reviews', 'detail_prose', 'remediation', 'board_paths', 'facts', 'operating_cases'}


def report_exclusion(path: Path, base: Path) -> str | None:
    parts = path.relative_to(base).parts
    if base.name == 'schematic-intake':
        if len(parts) >= 2 and parts[:2] == ('report_ui', 'dist'):
            return None
        if len(parts) >= 3 and parts[0] == 'boards' and parts[2] == 'review_out':
            if len(parts) == 3:
                return None
            if parts[3] in REPORT_DIRS:
                return None
            if len(parts) == 4 and path.is_file() and path.suffix.lower() in {'.json', '.html', '.csv', '.png', '.jpg', '.pdf'}:
                return None
            return 'runtime cache, unselected generated output or archived bundle'
    return core_exclusion(path, base)


def profile_exclusion(path: Path, base: Path, profile: str) -> str | None:
    if profile == 'core':
        return core_exclusion(path, base)
    if profile == 'core-report':
        return report_exclusion(path, base)
    return None


def core_exclusion(path: Path, base: Path) -> str | None:
    if base.name != 'schematic-intake':
        return 'historical material backup' if '.bak' in path.name or '_bak-' in path.name else None
    parts = path.relative_to(base).parts
    if not parts:
        return None
    top = parts[0]
    if len(parts) == 1 and top not in CORE_DIRS and top not in CORE_ROOT_FILES:
        return 'outside core development scope'
    if top == 'boards' and len(parts) >= 3:
        if len(parts) > 3 or path.is_dir() or path.suffix != '.json':
            return 'board runtime outputs, duplicated inputs, logs or history'
    if top == 'report_ui' and len(parts) >= 2 and parts[1] == 'dist':
        return 'rebuildable frontend output'
    if top == 'docs' and path.is_file() and path.suffix.lower() not in {'.md', '.txt', '.json', '.py', '.yaml', '.yml'}:
        return 'non-core document attachment'
    if '_smoke_out' in parts or (top == 'solve_prog' and 'out' in parts) or (top == 'packages' and path.suffix == '.zip'):
        return 'test output or duplicate package archive'
    return None


def long_path(path: Path) -> Path:
    text = str(path.resolve())
    if os.name == 'nt' and not text.startswith('\\\\?\\'):
        text = '\\\\?\\UNC\\' + text[2:] if text.startswith('\\\\') else '\\\\?\\' + text
    return Path(text)


def exclusion(path: Path, base: Path) -> str | None:
    rel = path.relative_to(base)
    if any(x in SKIP_DIRS for x in rel.parts):
        return 'dependency, local environment, credentials directory or temporary directory'
    name = path.name.lower()
    if name in SECRET_NAMES or (name.startswith('.env') and name not in {'.env.example', '.env.sample', '.env.template'}) or name.endswith(('.key', '.p12', '.pfx')):
        return 'potential credential file'
    if name.endswith(('.pyc', '.pyo')) or name.startswith('_tmp_'):
        return 'temporary or bytecode file'
    if path.is_symlink():
        return 'symbolic link, not followed'
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--profile', choices=('full', 'core', 'core-report'), default='full')
    args = parser.parse_args()
    out = args.output.resolve()
    if out.is_relative_to(ROOT) or out.exists():
        raise SystemExit('Output must be a new file outside the source repository')
    # 原始材料在仓库内 materials/；旧布局的同级 0918全量材料/ 存在时一并打包。
    material = ROOT.parent / '0918全量材料'
    bases = (ROOT, material) if material.is_dir() else (ROOT,)
    out.parent.mkdir(parents=True, exist_ok=True)
    manifest = {'format': 'handoff.v1', 'profile': args.profile, 'started_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'snapshot': 'per-file, non-transactional', 'files': [], 'excluded': [], 'changed_during_read': [], 'material_references': []}
    for config in ROOT.glob('boards/*/materials.json'):
        data = json.loads(config.read_text(encoding='utf-8-sig'))
        for key, value in data.items():
            if not isinstance(value, str):
                continue
            p = (config.parent / value).resolve()
            if key in {'netlist', 'bom', 'schematic_pdf', 'eprj2', 'operating_conditions', 'design_task_book', 'design_task_book_docx', 'drc', 'rules'}:
                valid = p.is_file() and (p.is_relative_to(ROOT) or p.is_relative_to(material))
                manifest['material_references'].append({'config': config.relative_to(ROOT).as_posix(), 'field': key, 'value': value, 'included_tree_exists': valid})
                if not valid:
                    raise SystemExit(f'Material reference missing or outside package roots: {key}')
    count = total = 0
    with zipfile.ZipFile(out, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=1, allowZip64=True) as z:
        for base in bases:
            base = long_path(base)
            print(f'Scanning {base.name}', flush=True)
            for current, dirs, files in os.walk(base, followlinks=False):
                current = Path(current)
                for name in list(dirs):
                    p = current / name
                    reason = exclusion(p, base) or profile_exclusion(p, base, args.profile)
                    if reason:
                        dirs.remove(name)
                        manifest['excluded'].append({'path': f'{base.name}/{p.relative_to(base).as_posix()}/', 'reason': reason})
                dirs.sort()
                for name in sorted(files):
                    p = current / name
                    arc = f'{base.name}/{p.relative_to(base).as_posix()}'
                    reason = exclusion(p, base) or profile_exclusion(p, base, args.profile)
                    if reason:
                        manifest['excluded'].append({'path': arc, 'reason': reason})
                        continue
                    before = p.stat()
                    h = hashlib.sha256()
                    size = 0
                    info = zipfile.ZipInfo.from_file(p, arc)
                    info.compress_type = zipfile.ZIP_DEFLATED
                    with p.open('rb') as src, z.open(info, 'w', force_zip64=True) as dest:
                        while chunk := src.read(1024 * 1024):
                            h.update(chunk)
                            size += len(chunk)
                            dest.write(chunk)
                    after = p.stat()
                    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                        manifest['changed_during_read'].append(arc)
                    manifest['files'].append({'path': arc, 'bytes': size, 'sha256': h.hexdigest(), 'source_mtime_ns': before.st_mtime_ns})
                    count += 1
                    total += size
                    if count % 1000 == 0:
                        print(f'Packed {count} files, {total / 1048576:.1f} MiB', flush=True)
        manifest['finished_at'] = time.strftime('%Y-%m-%dT%H:%M:%S%z')
        manifest['file_count'] = count
        manifest['source_bytes'] = total
        z.writestr('PACKAGE_MANIFEST.json', json.dumps(manifest, ensure_ascii=False, indent=2))
        scope = ('核心开发包：保留源码、配置、提示词、测试、文本资料、架构文档和当前板卡原始材料；不含已有报告、运行缓存、历史执行记录、生成图片或前端dist。\n安装依赖并构建前端后，按运行文档从prep开始生成所需板级产物，再启动报告服务。首次评审前需各自登录Codex；不要将打包的选择范围配置视为已完成评审。\n完整历史证据另见此前完整交接包。\n' if args.profile == 'core' else '完整交接包：源码、配置、提示词、材料、已有报告与历史记录均保留。\n已有评审结论及未完成事项按原样保留，不代表本次重新评审。\n')
        if args.profile == 'core-report':
            scope = '核心开发与当前报告包：在核心源码和原始材料之外，保留当前HTML报告、Vue构建、报告JSON、工况图、规格书、PDF.js、原理图及当前评审证据。\n不含历史执行快照、context_store、本机依赖和运行缓存，不保证旧任务可直接续跑。已有未完成与待确认结论按原样保留，未重新评审。\n进入schematic-intake目录后执行：\npy -m agent_review.serve --board boards/0918全量 --host 127.0.0.1 --port 8765 --no-browser\n浏览器打开 http://127.0.0.1:8765/expert_review.html\n当前展示无需重新评审或重新构建前端；修改源码后仍按开发文档安装依赖、构建与验证。\n'
        z.writestr('START_HERE.txt', '原理图评审开发交接包\n\n' + ('请同时解压 schematic-intake 与 0918全量材料 两个目录，保持同级。\n' if len(bases) > 1 else '只有 schematic-intake 一个目录，原始材料在 schematic-intake/materials/。\n') + '先阅读 schematic-intake/docs/architecture/README.md 和 09-team-handoff.md。\n' + scope + '不含依赖安装目录和个人鉴权。\nPACKAGE_MANIFEST.json 含逐文件SHA-256、排除清单及快照变化记录。\n')
    print('Checking ZIP CRC and entry names', flush=True)
    with zipfile.ZipFile(out) as z:
        if z.testzip() is not None:
            raise RuntimeError('Archive CRC verification failed')
        if len(z.namelist()) != len(set(z.namelist())):
            raise RuntimeError('Duplicate ZIP entries')
    h = hashlib.sha256()
    with out.open('rb') as stream:
        while chunk := stream.read(4 * 1024 * 1024):
            h.update(chunk)
    digest = h.hexdigest()
    out.with_suffix(out.suffix + '.sha256').write_text(f'{digest}  {out.name}\n', encoding='utf-8')
    summary = {'archive': str(out), 'bytes': out.stat().st_size, 'files': count, 'source_bytes': total, 'excluded_entries': len(manifest['excluded']), 'changed_during_read': manifest['changed_during_read'], 'crc_verified': True, 'sha256': digest}
    out.with_suffix(out.suffix + '.manifest.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
