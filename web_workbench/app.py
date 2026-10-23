"""Same-origin local API, immutable published reports and Vue application."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import hashlib
import importlib.util
import json
import logging
import os
from pathlib import Path
import shutil
import sys
import uuid
from typing import Literal
from urllib.parse import quote, urlparse
import zipfile

import httpx
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from . import materials, runner
from .deployment import Deployment, auth_client, authority, origin_tuple
from .store import ACTIVE, TERMINAL, Conflict, NotFound, ROOT, Store, atomic_json


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BoardInput(Payload):
    name: str = Field(min_length=1, max_length=100)


class RunInput(Payload):
    mode: Literal["materials", "program", "review"] = "program"
    rule_ids: list[str] = Field(default_factory=list, max_length=100)
    objects: list[str] | None = Field(default=None, max_length=500)
    concurrency: int = Field(default=2, ge=1, le=8)
    profile_id: str | None = None
    resume: bool = False
    parent_id: str | None = None


class ProfileInput(Payload):
    id: str | None = None
    name: str = Field(default="默认模型", min_length=1, max_length=100)
    provider: str = "openai-compatible"
    wire_api: Literal["chat", "responses"] = "chat"
    base_url: str = Field(min_length=1, max_length=2000)
    model: str = Field(min_length=1, max_length=200)
    api_key_env: str | None = None
    api_key: str | None = None
    timeout: float = Field(default=120, ge=1, le=1200)
    max_tokens: int = Field(default=8192, ge=128, le=131072)
    max_context_tokens: StrictInt | None = Field(default=None, ge=1)
    max_turns: int = Field(default=16, ge=1, le=100)
    max_requests: int = Field(default=40, ge=1, le=500)
    max_retries: int = Field(default=2, ge=0, le=5)
    stream: bool = False
    structured_output: Literal["json_schema", "json_object", "none"] = "json_schema"
    strict_schema: bool = False
    supports_tools: bool = True
    supports_images: bool = True
    token_parameter: Literal["max_tokens", "max_completion_tokens"] = "max_tokens"
    default: bool = False


class FeedbackInput(Payload):
    mark: Literal["", "unreviewed", "confirmed", "disputed"] = ""
    note: str = Field(default="", max_length=10000)


class QuestionInput(Payload):
    question: str = Field(min_length=1, max_length=10000)
    profile_id: str | None = None


def public_run(run, root_path=""):
    value = {k: v for k, v in run.items() if k not in {"profile_path", "workspace", "process_started", "board_path", "parent_workspace"}}
    report_url = value.get("report_url")
    if root_path and isinstance(report_url, str) and report_url.startswith("/reports/"):
        value["report_url"] = root_path + report_url
    return value


def public_profile(profile):
    return {k: v for k, v in profile.items() if k not in {"profile_path", "api_key"}}


def rules_list():
    from agent_review.enumerate import load_program_rules, rule_meta_map
    from agent_review.rule_modes import load_agent_rule_ids
    from kernel.checkers.all15 import TITLES
    meta, agent = rule_meta_map(), set(load_agent_rule_ids())
    return [{"id": rid, "title": meta.get(rid, {}).get("name") or TITLES.get(rid, rid), "mode": "review" if rid in agent else "program"} for rid in load_program_rules()["rule_ids"]]


def safe_asset(base, asset):
    root = Path(base).resolve()
    relative = Path(asset)
    path = (root / relative).resolve()
    if relative.is_absolute() or not path.is_relative_to(root) or path == root:
        raise HTTPException(404, "资源不存在")
    if not path.is_file():
        raise HTTPException(404, "资源不存在")
    return path


def report_path(store, run_id):
    run = store.run(run_id)
    if run["status"] != "completed" or not run.get("report_dir"):
        raise NotFound("该任务尚无完整发布报告")
    path = Path(run["report_dir"]).resolve()
    if not path.is_relative_to(store.home / "reports") or not path.is_dir():
        raise NotFound("报告路径无效")
    return path


def report_data(store, run_id, root_path=""):
    root = report_path(store, run_id)
    verdicts = json.loads((root / "verdicts.json").read_text(encoding="utf-8-sig"))
    data = json.loads((root / "report_data.json").read_text(encoding="utf-8-sig"))
    evidence = {r["rule_id"]: json.loads(safe_asset(root, f"evidence/{r['rule_id']}.json").read_text(encoding="utf-8-sig")) for r in verdicts.get("results", [])}
    manifest_path = root / "snapshot.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig")) if manifest_path.is_file() else {}
    base = root_path + f"/reports/{run_id}/"
    source_assets = {original: {"path": relative, "sha256": manifest["files"][relative], "url": base + quote(relative, safe="/")}
                     for original, relative in manifest.get("sources", {}).items() if relative in manifest.get("files", {})}
    return {"verdicts": verdicts, "evidence": evidence, "report_data": data, "asset_base": base, "source_assets": source_assets, "run": public_run(store.run(run_id), root_path)}


def require_object(store, run_id, rule_id, object_id):
    pack = report_data(store, run_id)["evidence"].get(rule_id, {})
    if not any(str(c.get("object")) == object_id for c in pack.get("object_cards", [])):
        raise NotFound("此评审批次中没有该对象")


def create_app(home=None, *, repository=ROOT, launch_workers=True, discover=True, root_path=None,
               allowed_hosts=None, auth_check_url=None, login_url=None, ui_dir=None, auth_transport=None):
    deployment = Deployment.configure(repository, root_path=root_path, allowed_hosts=allowed_hosts,
        auth_check_url=auth_check_url, login_url=login_url, ui_dir=ui_dir)
    store = Store(home, repository=repository)
    if discover:
        store.discover_boards()

    @asynccontextmanager
    async def lifespan(app):
        async def monitor():
            while True:
                await asyncio.to_thread(runner.reconcile, store)
                await asyncio.sleep(3)
        app.state.auth_client = auth_client(auth_transport) if deployment.auth_check_url else None
        task = asyncio.create_task(monitor()) if launch_workers else None
        try:
            yield
        finally:
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            if app.state.auth_client:
                await app.state.auth_client.aclose()
                app.state.auth_client = None

    app = FastAPI(title="原理图评审工作台", version="0.1.0", lifespan=lifespan)
    app.state.store = store
    app.state.deployment = deployment

    def expose_run(run):
        return public_run(run, deployment.root_path)

    @app.middleware("http")
    async def same_origin(request, call_next):
        if deployment.root_path:
            # ASGI root_path lets the router accept either a stripped proxy path
            # or the public path, without double-prepending it in the server.
            request.scope["root_path"] = deployment.root_path
        cookies = []

        def finish(response):
            response.raw_headers.extend((b"set-cookie", value) for value in cookies)
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
            return response

        origin = request.headers.get("origin")
        fetch_site = request.headers.get("sec-fetch-site")
        host = request.headers.get("host", "")
        try:
            host_name, _ = authority(host)
            valid_host = host_name in deployment.allowed_hosts
        except ValueError:
            valid_host = False
        if not valid_host:
            return finish(JSONResponse({"detail": "服务不接受此 Host"}, status_code=403))
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            try:
                valid_origin = not origin or origin_tuple(origin) == origin_tuple(f"{request.url.scheme}://{host}")
            except ValueError:
                valid_origin = False
            if fetch_site == "cross-site" or not valid_origin:
                return finish(JSONResponse({"detail": "不允许跨站操作工作台"}, status_code=403))

        if deployment.auth_check_url:
            headers = {"cookie": request.headers.get("cookie", ""), "x-forwarded-proto": request.url.scheme}
            if "authorization" in request.headers:
                headers["authorization"] = request.headers["authorization"]
            try:
                client = getattr(app.state, "auth_client", None)
                if client:
                    checked = await client.get(deployment.auth_check_url, headers=headers)
                else:
                    # ASGI clients that omit lifespan still get the same checks.
                    async with auth_client(auth_transport) as client:
                        checked = await client.get(deployment.auth_check_url, headers=headers)
                cookies = [value for name, value in checked.headers.raw if name.lower() == b"set-cookie"]
            except httpx.HTTPError:
                return finish(JSONResponse({"detail": "登录认证服务暂不可用，请稍后重试"}, status_code=503))
            if checked.status_code in {401, 403}:
                local_path = deployment.local_path(request)
                if local_path == "/api" or local_path.startswith("/api/"):
                    login = deployment.login_redirect(deployment.public_path("/"))
                    return finish(JSONResponse({"detail": "请先登录主站后使用评审工作台", "login_url": login}, status_code=401))
                login = deployment.login_redirect(deployment.request_path(request))
                return finish(RedirectResponse(login, status_code=302 if request.method in {"GET", "HEAD"} else 303))
            if not 200 <= checked.status_code < 300:
                return finish(JSONResponse({"detail": "登录认证服务返回异常，请稍后重试"}, status_code=503))
        try:
            response = await call_next(request)
        except Exception:
            if not deployment.auth_check_url:
                raise
            # A downstream error must not discard a successful session rotation.
            logging.getLogger(__name__).exception("Authenticated workbench request failed")
            response = JSONResponse({"detail": "工作台处理请求失败，请稍后重试"}, status_code=500)
        return finish(response)

    @app.exception_handler(NotFound)
    async def not_found(_, exc):
        return JSONResponse({"detail": str(exc)}, status_code=404)

    @app.exception_handler(Conflict)
    async def conflict(_, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(ValueError)
    async def invalid(_, exc):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.get("/api/runtime")
    def runtime():
        checks = [{"name": "python", "ok": sys.version_info >= (3, 12), "detail": sys.version.split()[0]}]
        for module in ("pymupdf", "jsonschema", "openpyxl", "httpx"):
            checks.append({"name": module, "ok": bool(importlib.util.find_spec(module)), "detail": "本地依赖"})
        try:
            from kernel.runtime import node_executable
            node = node_executable()
            checks.append({"name": "project_parser", "ok": True, "detail": node})
        except Exception as exc:
            checks.append({"name": "project_parser", "ok": False, "detail": str(exc)})
        profiles = store.profiles()
        return {"ready": all(x["ok"] for x in checks), "checks": checks, "model_configured": bool(profiles), "active_runs": [expose_run(r) for r in store.runs(active=True)]}

    def enrich_board(board):
        runs = store.runs(board["id"])
        reports = [r for r in runs if r["status"] == "completed" and r.get("report_dir")]
        active = next((r for r in runs if r["status"] in ACTIVE), None)
        return {**board, "latest_report": expose_run(reports[0]) if reports else None, "active_run": expose_run(active) if active else None, "material_version": materials.inventory(board)["version"]}

    @app.get("/api/boards")
    def boards():
        return {"boards": [enrich_board(b) for b in store.boards()]}

    @app.post("/api/boards", status_code=201)
    def create_board(payload: BoardInput):
        return enrich_board(store.create_board(payload.name))

    @app.get("/api/boards/{board_id}/materials")
    def get_materials(board_id: str):
        return materials.inventory(store.board(board_id))

    @app.post("/api/boards/{board_id}/materials")
    async def upload_material(board_id: str, role: str = Form(...), file: UploadFile = File(...)):
        chunks, size = [], 0
        while data := await file.read(1024 * 1024):
            size += len(data)
            if size > 50 * 1024 * 1024:
                raise HTTPException(413, "单个材料文件不能超过 50 MB")
            chunks.append(data)
        return await asyncio.to_thread(materials.import_file, store, board_id, role, file.filename, b"".join(chunks))

    @app.post("/api/boards/{board_id}/preflight")
    def check_materials(board_id: str):
        return materials.preflight(store.board(board_id), rules_list())

    @app.get("/api/rules")
    def rules():
        return {"rules": rules_list()}

    def start_run(board_id, payload, request_key=None):
        board = store.board(board_id)
        inv = materials.inventory(board)
        if inv["missing"]:
            raise ValueError("材料未齐：" + ", ".join(inv["missing"]))
        available = {r["id"]: r for r in rules_list()}
        ids = list(dict.fromkeys(payload.rule_ids))
        if any(rid not in available for rid in ids):
            raise ValueError("检查范围含未知规则")
        if payload.mode == "program":
            if not ids:
                ids = [rid for rid, r in available.items() if r["mode"] == "program"]
            if any(available[rid]["mode"] != "program" for rid in ids):
                raise ValueError("程序检查模式只能选择程序规则；AI 规则请使用深审模式")
        elif payload.mode == "review" and not ids:
            ids = list(available)
        if payload.objects is not None and (not payload.objects or any(not x.strip() or len(x) > 200 for x in payload.objects)):
            raise ValueError("对象范围不能为空或含无效名称")
        profile = None
        if payload.profile_id or payload.mode == "review":
            profile = store.profile(payload.profile_id)
        if payload.mode == "review" and any(available[rid]["mode"] == "review" for rid in ids) and profile.get("supports_tools", True) is False:
            raise ValueError("所选模型关闭了工具调用，不能执行需要读取材料的深审规则；请启用并测试工具能力")
        if payload.mode == "review" and profile.get("max_context_tokens") is not None and profile["max_context_tokens"] <= profile.get("max_tokens", 8192):
            raise ValueError("上下文窗口预算必须大于输出预留，才能容纳输入材料；请调整模型配置")
        parent = None
        if payload.resume:
            if not payload.parent_id:
                raise ValueError("续跑需指定原任务")
            parent = store.run(payload.parent_id)
            if parent["board_id"] != board_id or parent["status"] not in {"stopped", "interrupted", "failed"}:
                raise ValueError("只能续跑同一项目已停止、中断或失败的任务")
            if parent.get("material_version") != inv["version"]:
                raise ValueError("材料已变化，请创建新任务；不能沿用旧任务断点")
            if parent.get("mode") != payload.mode or set(parent.get("rule_ids") or []) != set(ids) or (
                None if parent.get("objects") is None else set(parent["objects"])
            ) != (None if payload.objects is None else set(payload.objects)):
                raise ValueError("续跑必须保持原任务的模式、检查项和对象范围")
            if parent.get("profile_id") != (profile["id"] if profile else None):
                raise ValueError("续跑必须使用原任务的模型配置")
            if profile:
                from .execution import profile_signature
                if profile_signature(parent.get("profile_path")) != profile_signature(profile["profile_path"]):
                    raise ValueError("模型或执行策略已变化，请创建新任务")
        elif payload.parent_id:
            raise ValueError("原任务标识仅用于续跑")
        values = payload.model_dump()
        values.update(rule_ids=ids, material_version=inv["version"], board_path=board["path"], profile_id=profile["id"] if profile else None)
        if parent:
            values["parent_workspace"] = parent["workspace"]
        run = store.create_run(board_id, values, request_key=request_key)
        if run["status"] != "queued" or run.get("pid"):
            return expose_run(run)
        try:
            workspace = Path(run["workspace"])
            workspace.mkdir(parents=True, exist_ok=True)
            if profile and not run.get("profile_path"):
                frozen = workspace / "model-profile.json"
                atomic_json(frozen, json.loads(Path(profile["profile_path"]).read_text()), private=True)
                run = store.update_run(run["id"], profile_path=str(frozen))
            if launch_workers:
                run = runner.launch(store, run["id"])
        except Exception as exc:
            store.finish(run["id"], "failed", errors=["无法启动执行进程：" + str(exc)])
            raise
        return expose_run(run)

    @app.post("/api/boards/{board_id}/runs", status_code=202)
    def create_run(board_id: str, payload: RunInput, request: Request):
        key = request.headers.get("idempotency-key")
        if key and len(key) > 200:
            raise ValueError("请求标识过长")
        return start_run(board_id, payload, key)

    @app.get("/api/runs")
    def get_runs(board_id: str | None = None):
        if board_id:
            store.board(board_id)
        return {"runs": [expose_run(r) for r in store.runs(board_id)]}

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str):
        return expose_run(store.run(run_id, events=True))

    @app.get("/api/runs/{run_id}/events")
    async def run_events(run_id: str, request: Request, after: int = 0):
        store.run(run_id)
        try:
            cursor = max(after, int(request.headers.get("last-event-id", "0")))
        except ValueError:
            raise HTTPException(422, "事件序号无效")

        async def stream():
            nonlocal cursor
            while not await request.is_disconnected():
                events = await asyncio.to_thread(store.events, run_id, cursor)
                for event in events:
                    cursor = event["seq"]
                    yield f"id: {cursor}\nevent: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                current = await asyncio.to_thread(store.run, run_id)
                if current["status"] in TERMINAL and not events:
                    return
                if not events:
                    yield ": heartbeat\n\n"
                await asyncio.sleep(.5)
        return StreamingResponse(stream(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})

    @app.post("/api/runs/{run_id}/stop")
    def stop_run(run_id: str):
        return expose_run(runner.stop(store, run_id) if launch_workers else store.request_stop(run_id))

    @app.post("/api/runs/{run_id}/resume", status_code=202)
    def resume_run(run_id: str):
        run = store.run(run_id)
        keys = ("mode", "rule_ids", "objects", "concurrency", "profile_id")
        payload = RunInput(**{k: run.get(k) for k in keys}, resume=True, parent_id=run_id)
        return start_run(run["board_id"], payload)

    @app.get("/api/boards/{board_id}/reports")
    def reports(board_id: str):
        store.board(board_id)
        return {"reports": [expose_run(r) for r in store.runs(board_id) if r["status"] == "completed" and r.get("report_dir")]}

    @app.get("/api/reports/{run_id}/data")
    def get_report_data(run_id: str):
        return report_data(store, run_id, deployment.root_path)

    @app.get("/api/reports/{run_id}/download")
    def download_report(run_id: str):
        path = report_path(store, run_id)
        target = store.home / "downloads" / (run_id + ".zip")
        target.parent.mkdir(exist_ok=True)
        if not target.is_file():
            temp = target.with_name("." + target.name + "." + uuid.uuid4().hex + ".tmp")
            with zipfile.ZipFile(temp, "w", zipfile.ZIP_DEFLATED) as bundle:
                for p in sorted(path.rglob("*")):
                    if p.is_file():
                        bundle.write(p, p.relative_to(path))
            os.replace(temp, target)
        return FileResponse(target, filename=f"schematic-review-{run_id}.zip", media_type="application/zip")

    @app.get("/api/reviews/{run_id}/feedback")
    def feedback(run_id: str):
        return {"feedback": store.feedback(run_id)}

    @app.put("/api/reviews/{run_id}/{rule_id}/{object_id}/feedback")
    def save_feedback(run_id: str, rule_id: str, object_id: str, payload: FeedbackInput):
        require_object(store, run_id, rule_id, object_id)
        return store.save_feedback(run_id, rule_id, object_id, payload.model_dump())

    @app.get("/api/reviews/{run_id}/{rule_id}/{object_id}/questions")
    def questions(run_id: str, rule_id: str, object_id: str):
        require_object(store, run_id, rule_id, object_id)
        return {"questions": store.questions(run_id, rule_id, object_id)}

    @app.post("/api/reviews/{run_id}/{rule_id}/{object_id}/questions")
    def ask_question(run_id: str, rule_id: str, object_id: str, payload: QuestionInput):
        require_object(store, run_id, rule_id, object_id)
        profile = store.profile(payload.profile_id)
        from .questions import answer_question
        from agent_review.llm import LLMError
        try:
            result = answer_question(report_path(store, run_id), rule_id, object_id, payload.question, Path(profile["profile_path"]))
        except LLMError as exc:
            raise HTTPException(422, str(exc)) from exc
        return store.save_question(run_id, rule_id, object_id, {"question": payload.question, **result})

    @app.get("/api/model-profiles")
    def model_profiles():
        profiles = store.profiles()
        return {"profiles": [public_profile(p) for p in profiles], "default_profile_id": next((p["id"] for p in profiles if p["default"]), None)}

    @app.put("/api/model-profiles")
    def save_profile(payload: ProfileInput):
        parsed = urlparse(payload.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("模型接口需为不含凭据或查询参数的 HTTP(S) 地址")
        from dataclasses import fields
        from agent_review.llm import Profile, LLMError
        supplied = payload.model_dump()
        if payload.id and "wire_api" not in payload.model_fields_set:
            # Older clients have no protocol selector. Updating a name or other
            # setting must not silently change an existing Responses endpoint.
            try:
                supplied["wire_api"] = store.profile(payload.id).get("wire_api", "chat")
            except NotFound:
                pass
        known = {field.name for field in fields(Profile)}
        values = {key: value for key, value in supplied.items() if key in known and value is not None}
        try:
            Profile(**values)
        except LLMError as exc:
            raise ValueError(str(exc)) from exc
        return public_profile(store.save_profile(supplied))

    @app.post("/api/model-profiles/{profile_id}/test")
    def test_profile(profile_id: str):
        profile = store.profile(profile_id)
        from agent_review.llm import load_profile, probe_profile
        identity = store.probe_identity(profile["profile_path"])
        configured = None
        try:
            configured = load_profile(Path(profile["profile_path"]))
            identity = configured.signature(), hashlib.sha256(configured.api_key.encode()).hexdigest()
            result = probe_profile(configured)
            result["wire_api"] = configured.wire_api
            result["capabilities"] = result.get("tested", {})
            return store.save_probe(profile_id, result, identity)
        except Exception as exc:
            private = store.profile(profile_id, private=True)
            detail = str(exc)
            secrets = (configured.api_key if configured else None, private.get("api_key"), os.environ.get(private.get("api_key_env") or ""), os.environ.get("SCHEMATIC_LLM_API_KEY"))
            for secret in secrets:
                if secret:
                    detail = detail.replace(secret, "[redacted]")
            result = {"ok": False, "wire_api": profile.get("wire_api", "chat"), "error": detail[:2000], "tested": {}, "capabilities": {}, "vision": "not_tested",
                      "context": {"max_context_tokens": profile.get("max_context_tokens"), "status": "not_tested", "provider_window_verified": False}}
            return JSONResponse(store.save_probe(profile_id, result, identity), status_code=422)

    @app.get("/reports/{run_id}/{asset:path}")
    def report_asset(run_id: str, asset: str):
        if not asset:
            asset = "expert_review.html"
        return FileResponse(safe_asset(report_path(store, run_id), asset))

    @app.get("/{path:path}")
    def frontend(path: str):
        if path.startswith(("api/", "reports/")):
            raise HTTPException(404, "接口不存在")
        root = deployment.ui_dir
        if path and "." in Path(path).name:
            return FileResponse(safe_asset(root, path))
        index = root / "index.html"
        if not index.is_file():
            raise HTTPException(503, "前端尚未构建，请执行 npm --prefix report_ui run build")
        return FileResponse(index)

    return app
