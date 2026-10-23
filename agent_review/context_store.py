# -*- coding: utf-8 -*-
"""Sealed context store: init / build / fetch / patch / invalidate / delete / seal."""

from __future__ import annotations

import hashlib
import json
import shutil
import time
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_review.context_schema import (
    content_hash,
    safe_key_filename,
    validate_candidate,
)
from kernel.util import read_json, write_json


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def store_root(out: Path) -> Path:
    d = out / "context_store"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _ledger_path(root: Path) -> Path:
    return root / "ledger.jsonl"


def append_ledger(root: Path, op: str, **extra: Any) -> None:
    row = {"op": op, "at": _now(), **extra}
    with _ledger_path(root).open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def load_manifest(root: Path) -> dict[str, Any]:
    p = root / "manifest.json"
    if not p.is_file():
        return {}
    return read_json(p)


def save_manifest(root: Path, man: dict[str, Any]) -> None:
    write_json(root / "manifest.json", man)


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def board_kg_hash(board_kg_path: Path) -> str:
    if not board_kg_path.is_file():
        return ""
    return file_sha256(board_kg_path)[:32]


def live_path(root: Path, kind: str, key: str) -> Path:
    folder = {"object": "objects", "mpn": "mpn", "net": "nets"}[kind]
    return root / folder / f"{safe_key_filename(key)}.json"


def planned_key_id(kind: str, key: str) -> str:
    return f"{kind}:{key}"


def parse_planned_key(pk: str) -> tuple[str, str]:
    kind, _, key = pk.partition(":")
    return kind, key


class ContextStore:
    def __init__(self, out_dir: Path):
        self.out = out_dir
        self.root = store_root(out_dir)
        (self.root / "quarantine").mkdir(exist_ok=True)
        (self.root / "objects").mkdir(exist_ok=True)
        (self.root / "mpn").mkdir(exist_ok=True)
        (self.root / "nets").mkdir(exist_ok=True)
        (self.root / "tombstones").mkdir(exist_ok=True)

    def init(
        self,
        *,
        board_kg_path: Path,
        planned: list[dict[str, str]],
        rebuild: bool = False,
        materials_hash: str = "",
        library_hash: str = "",
    ) -> dict[str, Any]:
        """Register planned keys. Empty store better than dirty."""
        kg_hash = board_kg_hash(board_kg_path)
        man = load_manifest(self.root)
        sealed = bool((man.get("seal") or {}).get("sealed"))
        same = (
            man.get("board_kg_hash") == kg_hash
            and man.get("materials_hash") == materials_hash
            and man.get("library_hash") == library_hash
            and sealed
            and not rebuild
        )
        if same and man.get("planned_keys"):
            append_ledger(self.root, "init_reuse", epoch=man.get("epoch"), board_kg_hash=kg_hash)
            man["reused"] = True
            save_manifest(self.root, man)
            return man

        epoch = int(man.get("epoch") or 0) + (1 if man else 1)
        if man and (rebuild or man.get("board_kg_hash") != kg_hash):
            self._archive_live(epoch - 1 if epoch > 1 else 0)
            for sub in ("objects", "mpn", "nets"):
                d = self.root / sub
                if d.is_dir():
                    for f in d.glob("*.json"):
                        f.unlink()

        planned_keys = [planned_key_id(p["kind"], p["key"]) for p in planned]
        index: dict[str, Any] = {}
        for pk in planned_keys:
            kind, key = parse_planned_key(pk)
            index[pk] = {
                "kind": kind,
                "key": key,
                "status": "planned",
                "content_hash": None,
                "revision": 0,
            }

        man = {
            "epoch": epoch,
            "board_kg_hash": kg_hash,
            "materials_hash": materials_hash,
            "library_hash": library_hash,
            "planned_keys": planned_keys,
            "index": index,
            "seal": {"sealed": False},
            "pollution": [],
            "reused": False,
        }
        save_manifest(self.root, man)
        append_ledger(
            self.root,
            "init",
            epoch=epoch,
            board_kg_hash=kg_hash,
            planned_n=len(planned_keys),
            rebuild=rebuild,
        )
        return man

    def _archive_live(self, old_epoch: int) -> None:
        ts = self.root / "tombstones" / f"epoch_{old_epoch}_{int(time.time())}"
        ts.mkdir(parents=True, exist_ok=True)
        for sub in ("objects", "mpn", "nets"):
            src = self.root / sub
            if not src.is_dir():
                continue
            dest = ts / sub
            dest.mkdir(exist_ok=True)
            for f in src.glob("*.json"):
                shutil.move(str(f), str(dest / f.name))

    def write_quarantine(self, job_id: str, candidate: dict[str, Any]) -> Path:
        q = self.root / "quarantine" / f"{safe_key_filename(job_id)}.json"
        payload = dict(candidate)
        payload["job_id"] = job_id
        payload["quarantine_at"] = _now()
        write_json(q, payload)
        man = load_manifest(self.root)
        pk = planned_key_id(str(candidate.get("kind")), str(candidate.get("key")))
        if pk in (man.get("index") or {}):
            man["index"][pk]["status"] = "quarantine"
            save_manifest(self.root, man)
        append_ledger(self.root, "quarantine_write", job_id=job_id, key=pk)
        return q

    def commit_quarantine(self, job_id: str, *, patch_mode: bool = False) -> dict[str, Any]:
        q = self.root / "quarantine" / f"{safe_key_filename(job_id)}.json"
        if not q.is_file():
            return {"ok": False, "errors": [f"quarantine missing: {job_id}"]}
        cand = read_json(q)
        errs = validate_candidate(cand)
        if errs:
            man = load_manifest(self.root)
            pk = planned_key_id(str(cand.get("kind")), str(cand.get("key")))
            if pk in (man.get("index") or {}):
                man["index"][pk]["status"] = "rejected"
                save_manifest(self.root, man)
            append_ledger(self.root, "build_reject", job_id=job_id, errors=errs)
            return {"ok": False, "errors": errs}

        man = load_manifest(self.root)
        if (man.get("seal") or {}).get("sealed"):
            return {"ok": False, "errors": ["store sealed; unseal before commit"]}

        kind = str(cand["kind"])
        key = str(cand["key"])
        pk = planned_key_id(kind, key)
        fields = dict(cand.get("fields") or {})
        provenance = list(cand.get("provenance") or [])
        live = live_path(self.root, kind, key)

        if live.is_file() and patch_mode:
            old = read_json(live)
            old_fields = dict(old.get("fields") or {})
            for k, v in fields.items():
                if k in old_fields and old_fields[k] not in (None, "", {}, []) and old_fields[k] != v:
                    return {
                        "ok": False,
                        "errors": [f"patch cannot rewrite existing field {k}; use invalidate"],
                    }
            merged = dict(old_fields)
            for k, v in fields.items():
                if k not in merged or merged[k] in (None, "", {}, []):
                    merged[k] = v
            provenance = list(old.get("provenance") or []) + provenance
            fields = merged
            revision = int(old.get("revision") or 0) + 1
        elif live.is_file() and not patch_mode:
            return {
                "ok": False,
                "errors": ["live ready exists; use patch_mode or invalidate"],
            }
        else:
            revision = 1

        ch = content_hash(fields, provenance)
        record = {
            "key": key,
            "kind": kind,
            "epoch": man.get("epoch"),
            "status": "ready",
            "revision": revision,
            "provenance": provenance,
            "fields": fields,
            "content_hash": ch,
            "built_by": {"job_id": job_id, "agent_run_id": cand.get("agent_run_id")},
            "created_at": cand.get("created_at") or _now(),
            "updated_at": _now(),
        }
        write_json(live, record)
        idx = man.setdefault("index", {})
        idx[pk] = {
            "kind": kind,
            "key": key,
            "status": "ready",
            "content_hash": ch,
            "revision": revision,
            "path": str(live.relative_to(self.root)).replace("\\", "/"),
        }
        save_manifest(self.root, man)
        append_ledger(self.root, "build_commit", job_id=job_id, key=pk, content_hash=ch)
        return {"ok": True, "key": pk, "content_hash": ch, "path": str(live)}

    def fetch(
        self,
        keys: list[str],
        *,
        require_sealed: bool = True,
        require_ready: bool = True,
    ) -> dict[str, Any]:
        man = load_manifest(self.root)
        if require_sealed and not (man.get("seal") or {}).get("sealed"):
            raise RuntimeError("context_store not sealed")
        if require_sealed:
            store_hash = self.compute_store_hash()
            if (man.get("seal") or {}).get("store_hash") != store_hash:
                raise RuntimeError("context_store seal hash mismatch; possible pollution")

        bundle: dict[str, Any] = {"epoch": man.get("epoch"), "entries": {}}
        for pk in keys:
            kind, key = parse_planned_key(pk) if ":" in pk else ("object", pk)
            if ":" not in pk:
                pk = planned_key_id(kind, key)
            meta = (man.get("index") or {}).get(pk) or {}
            if require_ready and meta.get("status") != "ready":
                raise RuntimeError(f"context key not ready: {pk} status={meta.get('status')}")
            path = live_path(self.root, kind, key)
            if not path.is_file():
                raise RuntimeError(f"context live missing: {pk}")
            rec = deepcopy(read_json(path))
            if rec.get("status") == "tombstoned":
                raise RuntimeError(f"refusing tombstone: {pk}")
            bundle["entries"][pk] = rec
        return bundle

    def invalidate(self, kind: str, key: str) -> None:
        man = load_manifest(self.root)
        if (man.get("seal") or {}).get("sealed"):
            self.unseal(reason="invalidate")
            man = load_manifest(self.root)
        pk = planned_key_id(kind, key)
        path = live_path(self.root, kind, key)
        if path.is_file():
            rec = read_json(path)
            rec["status"] = "stale"
            tomb = self.root / "tombstones" / f"{safe_key_filename(pk)}_{int(time.time())}.json"
            write_json(tomb, rec)
            path.unlink()
        if pk in (man.get("index") or {}):
            man["index"][pk]["status"] = "stale"
            man["index"][pk]["content_hash"] = None
        save_manifest(self.root, man)
        append_ledger(self.root, "invalidate", key=pk)

    def delete(self, kind: str, key: str) -> None:
        self.invalidate(kind, key)
        man = load_manifest(self.root)
        pk = planned_key_id(kind, key)
        if pk in (man.get("index") or {}):
            man["index"][pk]["status"] = "tombstoned"
        if pk in (man.get("planned_keys") or []):
            man["planned_keys"] = [x for x in man["planned_keys"] if x != pk]
        save_manifest(self.root, man)
        append_ledger(self.root, "delete", key=pk)

    def compute_store_hash(self) -> str:
        parts: list[str] = []
        for sub in ("objects", "mpn", "nets"):
            d = self.root / sub
            if not d.is_dir():
                continue
            for f in sorted(d.glob("*.json")):
                parts.append(f"{f.name}:{file_sha256(f)[:16]}")
        return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:32]

    def detect_pollution(self) -> list[str]:
        """Live files must be indexed; ledger must exist after init."""
        alarms: list[str] = []
        man = load_manifest(self.root)
        if not man:
            return alarms
        index = man.get("index") or {}
        for sub, kind in (("objects", "object"), ("mpn", "mpn"), ("nets", "net")):
            d = self.root / sub
            if not d.is_dir():
                continue
            for f in d.glob("*.json"):
                rec = read_json(f)
                pk = planned_key_id(kind, str(rec.get("key") or f.stem))
                if pk not in index:
                    alarms.append(f"orphan live file: {f.name}")
                else:
                    if index[pk].get("content_hash") and index[pk]["content_hash"] != rec.get(
                        "content_hash"
                    ):
                        alarms.append(f"hash drift: {pk}")
        man["pollution"] = alarms
        save_manifest(self.root, man)
        if alarms:
            append_ledger(self.root, "pollution_alarm", alarms=alarms)
        return alarms

    def seal(self, *, allow_waive: bool = False) -> dict[str, Any]:
        alarms = self.detect_pollution()
        if alarms:
            raise RuntimeError(f"refuse seal: pollution {alarms}")
        man = load_manifest(self.root)
        not_ready = []
        for pk, meta in (man.get("index") or {}).items():
            st = meta.get("status")
            if st != "ready":
                if allow_waive and st == "waived":
                    continue
                not_ready.append(pk)
        if not_ready:
            raise RuntimeError(f"refuse seal: not ready {not_ready[:20]} (n={len(not_ready)})")
        store_hash = self.compute_store_hash()
        man["seal"] = {
            "sealed": True,
            "at": _now(),
            "key_count": len(man.get("planned_keys") or []),
            "store_hash": store_hash,
        }
        save_manifest(self.root, man)
        append_ledger(self.root, "seal", store_hash=store_hash)
        return man["seal"]

    def unseal(self, reason: str = "") -> None:
        man = load_manifest(self.root)
        man["seal"] = {"sealed": False, "unsealed_at": _now(), "reason": reason}
        save_manifest(self.root, man)
        append_ledger(self.root, "unseal", reason=reason)

    def ready_coverage(self) -> dict[str, Any]:
        man = load_manifest(self.root)
        idx = man.get("index") or {}
        planned_keys = list(man.get("planned_keys") or [])
        planned_ready = sum(
            1 for pk in planned_keys if (idx.get(pk) or {}).get("status") == "ready"
        )
        ready_all = sum(1 for m in idx.values() if m.get("status") == "ready")
        return {
            "planned": len(planned_keys),
            "ready": planned_ready,
            "ready_index_all": ready_all,
            "sealed": bool((man.get("seal") or {}).get("sealed")),
            "pollution": list(man.get("pollution") or []),
        }
