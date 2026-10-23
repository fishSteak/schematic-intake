"""P11 power decisions: design failure, worst-value uncertainty, and blocking gaps."""
from __future__ import annotations

import math
import hashlib
from pathlib import Path
from typing import Any

POWER_DECISION_VERSION = 2


def power_contract_fingerprint() -> str:
    root = Path(__file__).resolve().parent
    files = ["decision_contract.py", "validate_job.py", "prompts/_common_judge.md",
             "prompts/judge/_power_decision.md", "prompts/judge/P11-A01.analysis.md",
             "prompts/judge/P11-A01.md", "prompts/judge/_p11_self_check.md", "prompts/judge/_p11_critique.md"]
    digest = hashlib.sha256()
    for name in files:
        digest.update(name.encode())
        digest.update((root/name).read_bytes())
    return digest.hexdigest()


def finite_power(value: Any) -> bool:
    return type(value) in (float, int) and math.isfinite(value) and value >= 0


def validate_decision_audit(card: dict[str, Any], *, required: bool = False) -> list[str]:
    audit = card.get("decision_audit")
    if audit is None and not required:
        return []
    if not isinstance(audit, dict) or audit.get("version") != POWER_DECISION_VERSION:
        return [f"decision_audit.version={POWER_DECISION_VERSION} required for current power review"]
    errors = []
    states = card.get("states") or []
    reviews = audit.get("state_reviews") or []
    if not isinstance(states, list) or any(not isinstance(s, dict) for s in states):
        return ["decision_audit requires state objects"]
    if not isinstance(reviews, list):
        return ["decision_audit.state_reviews must be list"]
    indices = [r.get("state_index") for r in reviews if isinstance(r, dict) and type(r.get("state_index")) is int]
    if sorted(indices) != list(range(len(states))):
        errors.append("decision_audit.state_reviews must cover every state exactly once")
    if not audit.get("normal_design_search"):
        errors.append("decision_audit requires normal_design_search independent of external worst candidates")
    limit = (card.get("electrical") or {}).get("P_limit")
    failures = []
    for review in reviews:
        if not isinstance(review, dict) or review.get("admissibility") not in {"normal_design", "conditional", "unmapped", "out_of_scope"} or not review.get("reason"):
            errors.append("state review requires admissibility and supporting reason")
            continue
        index = review.get("state_index")
        if type(index) is not int or not 0 <= index < len(states):
            continue
        state = states[index]
        if review["admissibility"] == "normal_design":
            if state.get("status") in {"conditional", "unmapped"}:
                errors.append(f"normal-design states[{index}] cannot be conditional/unmapped")
            if state.get("stress_kind") != "upper_bound" and finite_power(state.get("P")) and finite_power(limit) and state["P"] > limit:
                failures.append(index)
    gaps = audit.get("gaps")
    blocking = []
    if not isinstance(gaps, list):
        errors.append("decision_audit.gaps must be list")
        gaps = []
    for gap in gaps:
        if not isinstance(gap, dict) or gap.get("effect") not in {"maximum_only", "precision_only", "can_change_verdict"} or not gap.get("issue") or not gap.get("reason"):
            errors.append("each decision gap requires issue/effect/reason")
            continue
        if gap["effect"] == "can_change_verdict":
            countercase = gap.get("countercase")
            proof_gap=gap.get('proof_gap')
            main=card.get('main_analysis') or {}
            if isinstance(countercase,dict) and all(countercase.get(key) for key in ('description','evidence','decision_effect')):
                blocking.append(gap)
            elif main.get('kind')=='incomplete_proof' and main.get('proof_status')=='incomplete' and isinstance(proof_gap,dict) and all(proof_gap.get(key) for key in ('failed_claim','reason','evidence','decision_effect')):
                # Failure to prove the required envelope is not evidence of a
                # physical exceedance; retain an explicit proof obligation.
                blocking.append(gap)
            else:
                errors.append('a verdict-changing gap needs a supported countercase or an explicit incomplete-proof obligation')
    if card.get("verdict_zh") == "待确认":
        if failures:
            errors.append("pending contradicts a normal-design exceedance; uncertain maximum/precision cannot erase failure")
        if not blocking:
            errors.append("pending requires a supported gap that can change the decision")
    basis = card.get("decision_basis") or {}
    if basis.get("kind") == "normal_design_model":
        index = basis.get("state_index")
        if index not in failures:
            errors.append("normal_design_model requires a supported normal-design exceedance")
        if basis.get("model_scope") != "nominal_design" or not basis.get("applicability") or not basis.get("coverage_argument"):
            errors.append("normal_design_model requires nominal_design scope, applicability and coverage_argument")
    return errors
