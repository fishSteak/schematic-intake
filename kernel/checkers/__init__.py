"""CheckResult + process steps for program candidates / Agent evidence."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class CheckResult:
    rule_id: str
    verdict_zh: str
    title: str = ""
    violations: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    coverage_n: int | None = None
    sub_questions: list[dict[str, Any]] = field(default_factory=list)
    objects: list[str] = field(default_factory=list)
    measured: list[str] = field(default_factory=list)
    target: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    fix: list[str] = field(default_factory=list)
    steps: list[dict[str, Any]] = field(default_factory=list)
    object_cards: list[dict[str, Any]] = field(default_factory=list)
    card_class: str = ""
    method: str = "程序检查（BoardKG 纯函数，零模型改词）"
    materials_needed: str = "网表 + BOM + 原理图PDF（+规格书/工况视项而定）"

    def add_step(
        self,
        action: str,
        observation: str,
        decision: str = "continue",
        *,
        n: int | None = None,
    ) -> None:
        self.steps.append(
            {
                "step": n if n is not None else len(self.steps) + 1,
                "action": action,
                "observation": observation,
                "decision": decision,
            }
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
