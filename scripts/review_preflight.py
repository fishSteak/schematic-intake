"""Check preparation and all Agent context dependencies without running judges."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_review.context_store import ContextStore
from agent_review.enumerate import enumerate_board
from agent_review.rule_modes import filter_enumeration_for_agent
from agent_review.stages.judge import _deps_for_object, reusable_card
from kernel.util import ROOT, out_dir, read_json, resolve_board_path, write_json
from kernel.runtime import node_executable


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--board", required=True)
    args = parser.parse_args()
    board = resolve_board_path(args.board)
    output = out_dir(board)
    kg = read_json(output / "board_kg.json")
    enumeration = enumerate_board(kg)
    agent_enum = filter_enumeration_for_agent(enumeration, kg)
    store = ContextStore(output)
    from agent_review.execution_policy import material_hash
    store.execution_material_hash = material_hash(board)
    # Verify the full seal at both ends; per-object reads check readiness without
    # rehashing hundreds of files for each object on network-mounted workspaces.
    store.fetch([])
    ready = reusable = 0
    failures = []
    for rule_id, objects in agent_enum["by_rule"].items():
        for obj in objects:
            try:
                store.fetch(_deps_for_object(kg, obj, rule_id), require_sealed=False)
                ready += 1
            except Exception as error:
                failures.append({"rule_id": rule_id, "object": obj, "error": str(error)})
            reusable += reusable_card(board, kg, store, rule_id, obj)
    store.fetch([])
    result = {
        "rules": len(enumeration["rule_ids"]), "agent_rules": len(agent_enum["rule_ids"]),
        "node": node_executable(), "context": store.ready_coverage(),
        "judge_context_ready": ready, "judge_context_failures": failures,
        "reusable_cards": reusable, "pending_cards": ready - reusable,
        "sources_exist": {key: Path(value).is_file() for key, value in kg["sources"].items() if value},
        "note": "Preparation status only; this does not certify a completed review.",
    }
    smoke = ROOT / "migration_baseline/codex_smoke.json"
    if smoke.is_file():
        result["codex_smoke"] = read_json(smoke)
    write_json(output / "automation_preflight.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if failures or not all(result["sources_exist"].values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
