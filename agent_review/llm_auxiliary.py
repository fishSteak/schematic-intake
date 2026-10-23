"""Direct LLM context, parameter and path jobs with application-owned writes."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
from pathlib import Path

from jsonschema import Draft202012Validator

from agent_review.context_schema import KIND_FIELDS, SOURCE_KINDS, safe_key_filename, validate_candidate
from agent_review.context_store import live_path
from agent_review.llm import LLMCancelled, load_profile
from agent_review.llm_tasks import atomic_json, check_cancelled, task
from agent_review.llm_tools import MaterialTools
from kernel.util import ROOT, out_dir


def _object(properties, required=None):
    return {"type": "object", "properties": properties,
            "required": list(properties) if required is None else required,
            "additionalProperties": False}


STRING = {"type": "string"}
STRINGS = {"type": "array", "items": STRING}


def output_schema(mode, job):
    if mode == "context":
        return _object({
            "kind": {"const": job["context_kind"]}, "key": {"const": job["key"]},
            "fields": _object({key: {} for key in sorted(KIND_FIELDS[job["context_kind"]])}, []),
            "provenance": {"type": "array", "items": _object({
                "source_kind": {"enum": sorted(SOURCE_KINDS)}, "path": {"type": "string", "minLength": 1},
                "page": {"type": ["integer", "string", "null"]}, "quote": STRING,
            }, ["source_kind", "path"])},
        })
    if mode in {"mpn_param", "mpn_vr"}:
        properties = {"mpn": {"const": job["mpn"]}, "cite": STRING,
                      "confidence": {"enum": ["high", "low"]}}
        if mode == "mpn_vr":
            properties["value_v"] = {"type": ["number", "null"], "minimum": 0.5, "maximum": 2000}
        else:
            properties.update(field={"const": job["field"]}, value={"type": ["number", "null"]},
                              value_text=STRING, unit=STRING)
        required = [key for key in properties if key != "value_text"]
        return _object(properties, required)
    if mode == "path_draft":
        path = _object({
            "path_id": {"type": "string", "minLength": 1}, "text": STRING,
            "refs": {**STRINGS, "minItems": 1}, "supply_net": STRING, "exit_net": STRING,
            "nodes": STRINGS,
            "actors": {"type": "array", "items": _object({"ref": STRING, "role": STRING})},
            "events": {"type": "array", "items": _object({"event_id": STRING, "name_zh": STRING, "what_changes": STRING})},
            "evidence": {"type": "array", "items": _object({"page": {"type": ["integer", "string"]}, "note": STRING})},
        })
        return _object({"domain_id": {"const": job["domain_id"]}, "paths": {"type": "array", "items": path}})
    raise ValueError("Unknown auxiliary mode: " + mode)


def _scoped_path(board, relative):
    path = (board / relative).resolve()
    if not path.is_relative_to(out_dir(board).resolve()):
        raise ValueError("Auxiliary output must stay under this board review_out")
    return path


def _run_one(board, mode, job, profile, store):
    check_cancelled()
    prompt_path = ROOT / "agent_review" / "prompts" / (
        "context/" + job["context_kind"] + ".md" if mode == "context" else mode + ".md")
    context, inputs = {"job": job}, []
    if mode == "context":
        existing = live_path(store.root, job["context_kind"], job["key"])
        if existing.is_file():
            context["existing"] = json.loads(existing.read_text(encoding="utf-8-sig"))
        context["policy"] = "Return only missing facts as a patch. Never change a nonempty existing field. Provenance paths must identify actual inspected material."
    elif mode == "path_draft":
        source = _scoped_path(board, job["slice_path"])
        inputs.append(source)
        context["domain"] = json.loads(source.read_text(encoding="utf-8-sig"))
    schema = output_schema(mode, job)
    audit = out_dir(board) / "llm_runs" / mode / safe_key_filename(job["job_id"])
    value, metadata = task(board, prompt_path.read_text(encoding="utf-8") +
        "\nReturn the final JSON in your response. The application validates and writes it; file-write instructions in legacy text are obsolete.",
        context, schema, inputs=inputs, profile=profile, log_path=Path(str(audit) + ".events.jsonl"))
    Draft202012Validator(schema).validate(value)
    if mode == "context":
        errors = validate_candidate(value)
        if errors:
            raise ValueError("Invalid context candidate: " + "; ".join(errors))
        material = MaterialTools(board, inputs=inputs)
        for source in value["provenance"]:
            material.resolve(source["path"])
    elif mode == "path_draft":
        from agent_review.board_paths.schema import validate_path
        refs = set(context["domain"].get("refs") or [])
        seen = set()
        for path in value["paths"]:
            errors = validate_path(path)
            if path["path_id"] in seen:
                errors.append("duplicate path_id")
            seen.add(path["path_id"])
            if not set(path["refs"]).issubset(refs) or any(actor["ref"] not in refs for actor in path["actors"]):
                errors.append("path contains refs outside the domain")
            if errors:
                raise ValueError("Invalid domain path: " + "; ".join(errors))
    elif value.get("value_v", value.get("value")) is not None and not value["cite"].strip():
        raise ValueError("A numeric parameter requires a source citation")
    check_cancelled()
    atomic_json(Path(str(audit) + ".run.json"), metadata)
    return value, {"job_id": job["job_id"], "ok": True, "run": metadata["id"], "runtime": "llm",
                   "metadata_path": str(Path(str(audit) + ".run.json").relative_to(board))}


def run_jobs(board, mode, jobs, *, concurrency=4, model=None, store=None):
    """Keep legacy job sidecars while using only the configured HTTP model runtime."""
    board = Path(board).resolve()
    jobs = list(jobs)
    profile = load_profile(task=mode) if jobs else None
    if profile and model and model != "auto":
        profile = replace(profile, model=model)

    def work(job):
        try:
            return _run_one(board, mode, job, profile, store)
        except LLMCancelled:
            raise
        except Exception as exc:
            error = str(exc)
            if profile and profile.api_key:
                error = error.replace(profile.api_key, "[redacted]")
            return None, {"job_id": job["job_id"], "ok": False, "error": error[:2000], "runtime": "llm"}

    results = []
    # Context store manifests are committed serially; only independent LLM calls overlap.
    with ThreadPoolExecutor(max_workers=max(1, min(int(concurrency), 8 if mode == "context" else 4))) as pool:
        for job, (value, result) in zip(jobs, pool.map(work, jobs)):
            check_cancelled()
            if value is not None:
                if mode == "context":
                    value["agent_run_id"] = result["run"]
                    store.write_quarantine(job["job_id"], value)
                    commit = store.commit_quarantine(job["job_id"], patch_mode=True)
                    result.update(ok=bool(commit["ok"]), commit=commit)
                else:
                    atomic_json(_scoped_path(board, job["out_path"]), value)
                    result["wrote"] = True
            results.append(result)
    sidecar = out_dir(board) / ("board_paths" if mode == "path_draft" else "") / ("sdk_" + mode + "_result.json")
    atomic_json(sidecar, {"runtime": "llm", "results": results})
    bad = sum(not result["ok"] for result in results)
    return {"ok": not bad, "returncode": 2 if bad else 0, "jobs": len(jobs), "results": results,
            "stdout": "", "stderr": "\n".join(result.get("error", "") for result in results if not result["ok"])[-2000:]}
