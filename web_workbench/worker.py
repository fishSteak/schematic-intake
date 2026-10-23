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

from .store import Store, TERMINAL, atomic_json, now


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
    for result in verdicts.get("results", []):
        evidence = source / "evidence" / (result["rule_id"] + ".json")
        if not evidence.is_file():
            raise ValueError("报告缺少检查项详情")
        pack = json.loads(evidence.read_text(encoding="utf-8-sig"))
        documents.extend(card.get("review_detail", {}) for card in pack.get("object_cards", []))
    parent = store.home / "reports"
    parent.mkdir(exist_ok=True)
    staging = parent / (".pending-" + run["id"])
    destination = parent / run["id"]
    if destination.exists():
        raise ValueError("本次报告已经发布，拒绝覆盖")
    try:
        # Model profiles and transient prompts never enter a report bundle.
        allow = {"verdicts.json", "report_data.json", "agent_review_gate.json", "delivery_coverage.json", "expert_review.html", "engineer_review.html", "evidence", "pdfjs", "sch", "stress_paths", "datasheets", "assets", "report_assets", "page_evidence", "research_assets", "evidence_store", "figures", "program_candidate.json", "figure_delivery.json"}
        staging.mkdir()
        for item in source.iterdir():
            if item.name not in allow:
                continue
            if item.is_symlink():
                raise ValueError("报告资源包含符号链接")
            if item.is_dir():
                if any(p.is_symlink() for p in item.rglob("*")):
                    raise ValueError("报告资源包含符号链接")
                shutil.copytree(item, staging / item.name)
            else:
                shutil.copy2(item, staging / item.name)
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
        atomic_json(staging / "snapshot.json", {"run_id": run["id"], "board_id": run["board_id"], "material_version": run.get("material_version"), "created_at": now(), "files": manifest})
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
            secret = profile.get("api_key") or os.environ.get(profile.get("api_key_env", ""))
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
