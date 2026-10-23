"""One durable run in a dedicated process; late outputs cannot be published."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import sys
import threading
import time
from urllib.parse import unquote, urlsplit

import psutil

from .store import ROOT, Store, TERMINAL, atomic_json, now


def referenced_assets(value):
    """Only UI resource fields are links; evidence quotations can contain raw paths."""
    result = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"figure", "img", "pdf", "datasheet_pdf"} and isinstance(item, str) and item:
                result.add(item)
                if key == "pdf":
                    result.add("pdfjs/web/viewer.html")
            elif isinstance(item, (dict, list)):
                result.update(referenced_assets(item))
    elif isinstance(value, list):
        for item in value:
            result.update(referenced_assets(item))
    return result


def copy_snapshot_asset(source, target):
    """Only actual contained files enter a snapshot, never symlink targets."""
    if source.is_symlink() or (source.is_dir() and any(p.is_symlink() for p in source.rglob("*"))):
        raise ValueError("报告资源包含符号链接")
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        shutil.copytree(source, target)
    else:
        shutil.copy2(source, target)


def frozen_board(store, run, source):
    workspace = Path(run["workspace"]).resolve()
    marker = workspace / "execution.json"
    if marker.is_file():
        data = json.loads(marker.read_text(encoding="utf-8-sig"))
        board = Path(data["board"]).resolve()
        if data.get("material_version") != run.get("material_version") or not board.is_relative_to(store.home / "runs"):
            raise ValueError("原始材料执行快照无效")
        return board
    # Unit fixtures and earlier runs can use the ordinary workspace/board layout.
    return source.parent if source.name == "review_out" and source.parent.is_relative_to(workspace) else None


def evidence_paths(value):
    paths = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"evidence", "evidence_refs"} and isinstance(item, list):
                paths.update(row["path"] for row in item if isinstance(row, dict) and isinstance(row.get("path"), str))
            if isinstance(item, (dict, list)):
                paths.update(evidence_paths(item))
    elif isinstance(value, list):
        for item in value:
            paths.update(evidence_paths(item))
    return paths


def include_source_materials(store, run, source, staging, documents):
    """Publish exact research sources without proposal prompts or model transcripts."""
    board = frozen_board(store, run, source)
    aliases = {}

    def register(original, published):
        aliases[original] = published
        if board is not None:
            aliases[str(board / original)] = published

    def copy_reference(original):
        if original in aliases:
            return aliases[original]
        canonical = original
        path = Path(original)
        if path.is_absolute():
            if board is not None and path.is_relative_to(board):
                canonical = path.relative_to(board).as_posix()
            elif path.is_relative_to(ROOT / "rules"):
                canonical = path.relative_to(ROOT).as_posix()
            else:
                return None
        if ".." in Path(canonical).parts:
            return None
        rule_source = canonical.startswith("rules/")
        context_source = (canonical.startswith("review_out/agent_jobs/") and canonical.endswith(".context.json")) or any(
            canonical.startswith("review_out/context_store/" + folder + "/") for folder in ("objects", "mpn", "nets"))
        allowed = rule_source or context_source or canonical.startswith("cache/")
        if not allowed or (board is None and not rule_source):
            return None
        base = ROOT if rule_source else board
        raw = base / canonical
        if raw.is_symlink() or not raw.resolve().is_relative_to(base) or not raw.is_file():
            raise ValueError("被引用事实来源不存在或路径无效：" + original[:200])
        if raw.suffix.lower() not in {".pdf", ".json", ".txt", ".yaml", ".yml", ".md"}:
            raise ValueError("被引用事实来源格式无法发布：" + original[:200])
        relative = canonical.removeprefix("review_out/")
        copy_snapshot_asset(raw, staging / relative)
        register(canonical, relative)
        aliases[original] = relative
        aliases[str(raw)] = relative
        return relative

    # Research downloads live beside untrusted proposals, so copy only assets.
    proposals = source / "evidence_proposals"
    if proposals.is_symlink():
        raise ValueError("报告资源包含符号链接")
    for proposal in sorted(proposals.iterdir()) if proposals.is_dir() else []:
        if proposal.is_symlink():
            raise ValueError("报告资源包含符号链接")
        assets = proposal / "assets"
        if assets.exists():
            copy_snapshot_asset(assets, staging / "evidence_proposals" / proposal.name / "assets")

    if board is not None:
        inputs = board / "material_inputs"
        if inputs.exists():
            copy_snapshot_asset(inputs, staging / "material_inputs")
            for path in (staging / "material_inputs").rglob("*"):
                if path.is_file():
                    relative = path.relative_to(staging).as_posix()
                    register(relative, relative)
        config = board / "materials.json"
        if config.is_file():
            relative = "source_inputs/materials.json"
            copy_snapshot_asset(config, staging / relative)
            register("materials.json", relative)

    # Output-relative and board-relative spellings identify the same byte copy.
    for path in staging.rglob("*"):
        if path.is_file() and not path.is_relative_to(staging / "material_inputs") and not path.is_relative_to(staging / "source_inputs"):
            relative = path.relative_to(staging).as_posix()
            register("review_out/" + relative, relative)
            aliases.setdefault(relative, relative)

    for original in evidence_paths(documents):
        copy_reference(original)

    sealed = staging / "evidence_store"
    for record in sealed.rglob("*.json") if sealed.is_dir() else []:
        data = json.loads(record.read_text(encoding="utf-8-sig"))
        hashes = (data.get("_provenance") or {}).get("evidence_hashes") or {}
        for original, expected in hashes.items():
            relative = copy_reference(original)
            if relative is None or not (staging / relative).is_file():
                raise ValueError("报告缺少已封存研究来源：" + original[:200])
            if hashlib.sha256((staging / relative).read_bytes()).hexdigest() != expected:
                raise ValueError("已封存研究来源字节已变化：" + original[:200])
    return aliases


def publish_report(store, run, report_dir):
    source = Path(report_dir).resolve()
    workspace = Path(run["workspace"]).resolve()
    if not source.is_relative_to(workspace) or not source.is_dir():
        raise ValueError("报告必须来自当前运行工作目录")
    required = ("verdicts.json", "report_data.json")
    for name in required:
        data = json.loads((source / name).read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            raise ValueError(f"报告格式无效：{name}")
    verdicts = json.loads((source / "verdicts.json").read_text(encoding="utf-8-sig"))
    documents = [json.loads((source / "report_data.json").read_text(encoding="utf-8-sig")).get("review_details", {})]
    source_documents = list(documents)
    for result in verdicts.get("results", []):
        evidence = source / "evidence" / (result["rule_id"] + ".json")
        if not evidence.is_file():
            raise ValueError("报告缺少检查项详情")
        pack = json.loads(evidence.read_text(encoding="utf-8-sig"))
        source_documents.append(pack)
        documents.extend(card.get("review_detail", {}) for card in pack.get("object_cards", []))
    parent = store.home / "reports"
    parent.mkdir(exist_ok=True)
    staging = parent / (".pending-" + run["id"])
    destination = parent / run["id"]
    if destination.exists():
        raise ValueError("本次报告已经发布，拒绝覆盖")
    try:
        # Model profiles and transient prompts never enter a report bundle.
        allow = {"verdicts.json", "report_data.json", "agent_review_gate.json", "delivery_coverage.json", "expert_review.html", "engineer_review.html", "evidence", "pdfjs", "sch", "stress_paths", "datasheets", "assets", "report_assets", "page_evidence", "research_assets", "evidence_store", "figures", "program_candidate.json", "figure_delivery.json", "board_kg.json", "design_graph.json", "jlc_project.json", "materials.json", "sch_index.json", "operating_conditions.json", "design_task.json"}
        staging.mkdir()
        for item in source.iterdir():
            if item.name not in allow:
                continue
            copy_snapshot_asset(item, staging / item.name)
        sources = include_source_materials(store, run, source, staging, source_documents)
        for asset in referenced_assets(documents):
            parsed = urlsplit(asset)
            if parsed.scheme == "data" and asset.startswith("data:image/"):
                continue
            if parsed.scheme or parsed.netloc:
                raise ValueError("报告图文资源必须包含在快照中：" + asset[:200])
            path = (staging / unquote(parsed.path)).resolve()
            if not path.is_relative_to(staging.resolve()) or not path.is_file():
                raise ValueError("报告缺少引用资源：" + asset[:200])
        manifest = {str(p.relative_to(staging)): hashlib.sha256(p.read_bytes()).hexdigest() for p in staging.rglob("*") if p.is_file()}
        atomic_json(staging / "snapshot.json", {"run_id": run["id"], "board_id": run["board_id"], "material_version": run.get("material_version"), "created_at": now(), "files": manifest, "sources": sources})
        if store.run(run["id"]).get("stop_requested"):
            raise InterruptedError("任务已停止，报告未发布")
        os.replace(staging, destination)
        return destination
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def main(ident=None):
    ident = ident or sys.argv[1]
    store = Store()
    run = store.run(ident)
    if run["status"] in TERMINAL:
        return 0
    store.update_run(ident, pid=os.getpid(), process_started=psutil.Process().create_time())
    local_stop = threading.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: local_stop.set())

    def cancelled():
        current = store.run(ident)
        return local_stop.is_set() or current.get("stop_requested", False) or current["status"] in TERMINAL

    while not store.claim_run(ident):
        if cancelled():
            store.finish(ident, "stopped", errors=[])
            return 0
        store.update_run(ident, heartbeat=now())
        time.sleep(.5)
    if cancelled():
        store.finish(ident, "stopped", errors=[])
        return 0
    store.update_run(ident, started_at=now(), stage="starting")
    ended = threading.Event()

    def heartbeat():
        while not ended.wait(2):
            store.update_run(ident, heartbeat=now())

    threading.Thread(target=heartbeat, daemon=True).start()
    try:
        from .execution import execute_run
        run = store.run(ident)
        profile_path = run.get("profile_path")
        if profile_path:
            os.environ["SCHEMATIC_LLM_PROFILE"] = profile_path
        workspace = Path(run["workspace"])
        workspace.mkdir(parents=True, exist_ok=True)
        result = execute_run(run, Path(store.board(run["board_id"])["path"]), workspace,
                             lambda kind, data: store.event(ident, kind, data), cancelled)
        if cancelled():
            raise InterruptedError("任务已停止")
        values = {"summary": result.get("summary", {}), "errors": []}
        if result.get("report_dir"):
            report = publish_report(store, run, result["report_dir"])
            values.update(report_dir=str(report), report_url=f"/reports/{ident}/", published_at=now())
        store.finish(ident, "completed", **values)
        return 0
    except BaseException as exc:
        if cancelled() or isinstance(exc, (InterruptedError, KeyboardInterrupt)):
            store.finish(ident, "stopped", errors=[])
            return 0
        # SDK errors may contain upstream request details. Transport supplies safe
        # errors; additionally redact the configured secret before persistence.
        message = str(exc)
        try:
            profile = json.loads(Path(run.get("profile_path", "")).read_text())
            secrets = (profile.get("api_key"), os.environ.get(profile.get("api_key_env") or ""), os.environ.get("SCHEMATIC_LLM_API_KEY"))
            for secret in secrets:
                if secret:
                    message = message.replace(secret, "[redacted]")
        except (OSError, ValueError):
            pass
        store.finish(ident, "failed", errors=[message[:3000]])
        print(type(exc).__name__, message[:3000], flush=True)
        return 1
    finally:
        ended.set()


if __name__ == "__main__":
    raise SystemExit(main())
