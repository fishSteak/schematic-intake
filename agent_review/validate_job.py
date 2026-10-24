# -*- coding: utf-8 -*-
"""Validate Judge output cards — 过程契约优先，不强制对齐程序 case_id。"""

from __future__ import annotations

import re
import math
import ast
from pathlib import Path
from typing import Any
from agent_review.decision_contract import validate_decision_audit
from agent_review.analysis_contract import VERSION as ANALYSIS_VERSION, validate_links, step_schema

VERDICTS = frozenset({"是", "否", "待确认", "不适用"})

_POWER_LABEL = r"(?:\bP(?:_(?!limit\b|rated\b)[A-Za-z0-9]+|\s*[（(][^）)\n]{1,60}[）)])?|功耗|耗散(?:功率)?)\s*(?:上界|下界)?\s*"
_POWER_BOUND = re.compile(_POWER_LABEL + r"(≤|<=|≦|≥|>=|≧|<|>)")
_POWER_FORMULA = re.compile(_POWER_LABEL + r"(<=|>=|≤|≥|≦|≧|<|>|=|≈)")
_BOUND_RELATION = re.compile(r"(<=|>=|≤|≥|≦|≧|<|>|=|≈)")
_POWER_RESULT = re.compile(r"\s*(\d+(?:\.\d*)?|\.\d+)([eE][+-]?\d+)?\s*(mW|uW|µW|μW|W)\s*$")
_BOUND_DIRECTIONS = {"upper_bound": {"≤", "<=", "≦", "<"}, "lower_bound": {"≥", ">=", "≧", ">"}}
_NAMED_POWER_BOUND = re.compile(r"\bP_(upper|lower)\s*[=≈]\s*")
_AUXILIARY_POWER = re.compile(r"(\bP_(?:limit|rated)\b|功率限额|功耗限额|额定功率|限额|余量)\s*(<=|>=|≤|≥|≦|≧|<|>|=|≈)")
_POWER_SCALE = {"W": 1, "mW": 1e-3, "uW": 1e-6, "µW": 1e-6, "μW": 1e-6}
# Dimensions use (voltage, current): resistance=V/A, power=V*A.
_CALC_UNITS = {"V": (1, (1, 0)), "mV": (1e-3, (1, 0)),
               "A": (1, (0, 1)), "mA": (1e-3, (0, 1)),
               "Ω": (1, (1, -1)), "kΩ": (1e3, (1, -1)), "MΩ": (1e6, (1, -1)),
               **{unit: (scale, (1, 1)) for unit, scale in _POWER_SCALE.items()}}
_NUMBER = r"(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
_CALC_QUANTITY = re.compile(r"(?<![\w.])(" + _NUMBER + r")\s*(" + "|".join(sorted(_CALC_UNITS, key=len, reverse=True)) + r")(?!\w)")


def _normalize_power_formula_text(text: str) -> str:
    """Normalize explicit punctuation only; preserve exponents and inner prose."""
    return text.translate(str.maketrans({"＝": "=", "＋": "+", "－": "-", "＊": "*",
                                        "／": "/", "＜": "<", "＞": ">", "＾": "^"})).rstrip().removesuffix("。").rstrip()


def _independent_power_derivation(tree, quantities, output_scale):
    """Distinguish deriving power from merely rescaling an existing power value.

    Unitful arithmetic must construct W from electrical quantities, not a W
    operand decorated with cancelling units. For historical unit-free formulae,
    retain the recognizable squared-voltage/resistance or squared-current times
    resistance forms, including scalar output conversions. Bare products have
    no physical identity; callers must supply V/A units instead of guessing.
    """
    if quantities:
        return all(dimension != (1, 1) for _, dimension in quantities.values())

    from agent_review.llm_tools import MaterialTools

    def value(node):
        return MaterialTools.calculate(ast.unparse(node))["value"]

    def squared(node):
        return (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow)
                and isinstance(node.right, ast.Constant) and type(node.right.value) in (int, float)
                and node.right.value == 2)

    def derived(node):
        if isinstance(node, ast.UnaryOp):
            return derived(node.operand)
        if not isinstance(node, ast.BinOp):
            return False
        if isinstance(node.op, ast.Div):
            if squared(node.left):
                return True  # Preserve the direct historical 1**2 / R form.
            return (derived(node.left)
                    and (value(node.left) != 1 or value(node.right) == 1 / output_scale))
        if isinstance(node.op, ast.Mult):
            for factor, other in ((node.left, node.right), (node.right, node.left)):
                if value(factor) != 1:
                    if squared(factor) or derived(factor):
                        return True
                elif derived(factor) and value(other) == output_scale:
                    # A genuine 1 W resistor calculation may be converted to
                    # mW. An unrelated adopted value times (1**2 / 1) cannot
                    # borrow that neutral factor's electrical identity.
                    return True
            return False
        if isinstance(node.op, (ast.Add, ast.Sub)):
            # An unrelated constant cannot borrow the identity of an appended
            # electrical expression, e.g. adopted_power + 0 * V**2 / R.
            return derived(node.left) and derived(node.right)
        return False

    return derived(tree.body)


def _calculated_power(expression: str, *, require_operation=True, allow_negative=False, output_scale=1) -> tuple[float, bool, bool]:
    """Return value, unit presence and derivation identity after safe arithmetic."""
    from agent_review.llm_tools import MaterialTools
    if len(expression) > 300 or "_" in expression:
        raise ValueError("Unsupported power expression")
    superscripts = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺", "0123456789-+")
    expression = re.sub(r"[⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺]+", lambda m: "**(" + m[0].translate(superscripts) + ")", expression)
    expression = expression.translate(str.maketrans({"×": "*", "÷": "/", "−": "-", "^": "**", "（": "(", "）": ")"})).strip()
    # Only a numeric literal percentage is arithmetic, never a variable,
    # suffix to discard, or permission to admit Python's modulo operator.
    expression = re.sub(r"(?<![\w.])(" + _NUMBER + r")\s*%(?![\w%])", r"(\1/100)", expression)
    quantities = {}
    def quantity(match):
        name = f"q{len(quantities)}"
        scale, dimension = _CALC_UNITS[match[2]]
        quantities[name] = (f"({match[1]}*{scale!r})", dimension)
        return name
    # User names are never admitted; only these generated quantity placeholders.
    if re.search(r"\bq\d+\b", expression):
        raise ValueError("Unsupported variable")
    normalized = _CALC_QUANTITY.sub(quantity, expression)
    tree = ast.parse(normalized, mode="eval")
    nodes = list(ast.walk(tree))
    if len(nodes) > 100 or (require_operation and not any(isinstance(n, ast.BinOp) for n in nodes)):
        raise ValueError("Numeric substitution required")
    def dimension(node, depth=0):
        if depth > 30:
            raise ValueError("Expression too deep")
        if isinstance(node, ast.Constant) and type(node.value) in (int, float) and math.isfinite(node.value):
            return (0, 0)
        if isinstance(node, ast.Name) and node.id in quantities:
            return quantities[node.id][1]
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            return dimension(node.operand, depth + 1)
        if isinstance(node, ast.BinOp):
            left, right = dimension(node.left, depth + 1), dimension(node.right, depth + 1)
            if isinstance(node.op, (ast.Add, ast.Sub)) and left == right:
                return left
            if isinstance(node.op, ast.Mult):
                return tuple(a + b for a, b in zip(left, right))
            if isinstance(node.op, ast.Div):
                return tuple(a - b for a, b in zip(left, right))
            if isinstance(node.op, ast.Pow) and right == (0, 0):
                exponent = MaterialTools.calculate(ast.unparse(node.right))["value"]
                if math.isfinite(exponent) and exponent.is_integer() and abs(exponent) <= 100:
                    return tuple(a * int(exponent) for a in left)
        raise ValueError("Unsupported arithmetic or inconsistent dimensions")
    if dimension(tree.body) != ((1, 1) if quantities else (0, 0)):
        raise ValueError("Power expression must have W dimensions")
    numeric = re.sub(r"\bq\d+\b", lambda m: quantities[m[0]][0], normalized)
    value = MaterialTools.calculate(numeric)["value"]
    if not math.isfinite(value) or (value < 0 and not allow_negative):
        raise ValueError("Power must be finite and nonnegative")
    return value, bool(quantities), _independent_power_derivation(tree, quantities, output_scale)


def _numeric_power_prefix(expression, *, output_scale=1):
    """Return None only for a genuinely symbolic prefix, never broken arithmetic."""
    stripped = _CALC_QUANTITY.sub("0", expression)
    for number in re.finditer(r"(?<![\w.])" + _NUMBER, stripped):
        rest = stripped[number.end():].lstrip()
        if rest and rest[0].isalpha():
            raise ValueError("Unsupported numeric unit")
    stripped = re.sub(_NUMBER, "0", stripped)
    if any(char.isalpha() or char == "_" for char in stripped):
        return None
    return _calculated_power(expression, require_operation=False, output_scale=output_scale)


def _power_quantity(text: str, *, allow_negative=False):
    sign = 1
    if allow_negative and text.lstrip().startswith(("-", "+")):
        text = text.lstrip()
        sign = -1 if text[0] == "-" else 1
        text = text[1:]
    match = _POWER_RESULT.fullmatch(text)
    if match is None:
        return None
    value = sign * float(match[1] + (match[2] or "")) * _POWER_SCALE[match[3]]
    if not math.isfinite(value) or (value < 0 and not allow_negative):
        raise ValueError("Power quantity must be finite and nonnegative")
    return value, _POWER_SCALE[match[3]]


def _power_comparison(left, relation, right, *, arithmetic_result=False):
    if relation in {"=", "≈"}:
        return math.isclose(left, right, rel_tol=1e-5 if arithmetic_result else 1e-12, abs_tol=1e-12 if arithmetic_result else 0)
    if relation in {"≤", "<=", "≦"}:
        return left <= right or math.isclose(left, right, rel_tol=1e-12, abs_tol=0)
    if relation in {"≥", ">=", "≧"}:
        return _power_comparison(right, "≤", left)
    if math.isclose(left, right, rel_tol=1e-12, abs_tol=0):
        return False  # Unit scaling must not turn equality into strict order.
    return left < right if relation == "<" else left > right if relation == ">" else False


def _checked_auxiliary_power(line):
    """Check rated/limit/margin calculations without making a bound proof."""
    markers = list(_AUXILIARY_POWER.finditer(line))
    if not markers:
        return None
    marker = markers[0]
    tail = line[marker.end():]
    # A plain voltage/current margin remains outside this power-only checker.
    if marker[1] in {"限额", "余量"} and not re.search(_NUMBER + r"\s*(?:mW|uW|µW|μW|W)(?!\w)", tail):
        return None
    if len(markers) != 1:
        raise ValueError("Use one auxiliary power calculation per clause")
    if _POWER_FORMULA.search(line) or _BOUND_RELATION.search(line[:marker.start()]):
        raise ValueError("Separate independent power calculations with semicolons")
    parts = _BOUND_RELATION.split(tail)
    margin = marker[1] == "余量"
    quantities = [_power_quantity(part, allow_negative=margin) for part in parts[::2]]
    reference = next((q for q in quantities if q is not None), None)
    if reference is None:
        raise ValueError("Auxiliary power needs an explicit W result")
    values = []
    for part, quantity in zip(parts[::2], quantities):
        if quantity is not None:
            values.append((quantity[0], False))
        else:
            value, unitful, _ = _calculated_power(part, require_operation=False, allow_negative=margin)
            values.append((value if unitful else value * reference[1], True))
    for index, relation in enumerate(parts[1::2]):
        left, left_calculated = values[index]
        right, right_calculated = values[index + 1]
        if not _power_comparison(left, relation, right, arithmetic_result=left_calculated or right_calculated):
            raise ValueError("Incorrect rated/limit/margin arithmetic or conversion")
    return 0


def _checked_power_formula(line, power, stress_kind):
    """Return 1 for a numeric proof, 0 for a checked annotation, None otherwise."""
    line = _normalize_power_formula_text(line)
    if len(line) > 2000:
        raise ValueError("Power formula too long")
    if re.search(r"(?<!\w)(?:nan|inf|infinity)(?!\w)|∞", line, re.IGNORECASE):
        raise ValueError("Power formula contains a non-finite quantity")
    auxiliary = _checked_auxiliary_power(line)
    if auxiliary is not None:
        return auxiliary
    markers = list(_POWER_FORMULA.finditer(line))
    if not markers:
        parts = _BOUND_RELATION.split(line)
        signed = _power_quantity(parts[0], allow_negative=True)
        if signed is not None and signed[0] < 0:
            raise ValueError("Negative power is only valid in an explicit margin")
        if len(parts) > 1 and _power_quantity(parts[0]) is not None:
            values = [_power_quantity(part) for part in parts[::2]]
            if any(value is None for value in values) or any(not _power_comparison(values[i][0], relation, values[i + 1][0]) for i, relation in enumerate(parts[1::2])):
                raise ValueError("Incorrect auxiliary power comparison or conversion")
            return 0  # A conversion without a P label is still not a derivation.
        return None
    if len(markers) != 1:
        raise ValueError("Use one power proof per formula row")
    marker = markers[0]
    named = _NAMED_POWER_BOUND.search(line)
    if named and named[1] + "_bound" != stress_kind:
        raise ValueError("Wrong bound kind")
    direction = _BOUND_DIRECTIONS[stress_kind]
    parts = _BOUND_RELATION.split(line[marker.end():])
    if marker[1] not in direction | {"=", "≈"} or any(op not in direction | {"=", "≈"} for op in parts[1::2]):
        raise ValueError("Wrong bound direction")
    quantities = [_power_quantity(part) for part in parts[::2]]
    operators = parts[1::2]
    substituted, independent, calculated, first = False, False, None, 0
    for index, operator in enumerate(operators):
        if operator in {"=", "≈"} and quantities[index + 1] is not None and quantities[index] is None:
            output_scale = 1 / quantities[index + 1][1]
            value, has_units, independent = _calculated_power(parts[index * 2], output_scale=output_scale)
            calculated = value if has_units else value * quantities[index + 1][1]
            if not _power_comparison(calculated, operator, quantities[index + 1][0], arithmetic_result=True) or any(q is not None for q in quantities[:index + 1]):
                raise ValueError("Incorrect numeric substitution")
            # Symbolic prefixes are not proved here, but every decidable numeric
            # prefix must be valid and consistent with the later substitution.
            known = []
            for prefix_index in range(index):
                prefix = _numeric_power_prefix(parts[prefix_index * 2], output_scale=output_scale)
                if prefix is not None:
                    number, unitful, prefix_derivation = prefix
                    independent = independent or prefix_derivation
                    known.append((prefix_index, number if unitful else number * quantities[index + 1][1]))
            known.append((index, calculated))
            for (left_index, left), (right_index, right) in zip(known, known[1:]):
                relations = operators[left_index:right_index]
                relation = next((op for op in relations if op in {"<", ">"}), None)
                relation = relation or next((op for op in relations if op not in {"=", "≈"}), "=")
                if not _power_comparison(left, relation, right):
                    raise ValueError("Incorrect numeric prefix relation")
            substituted, first = True, index + 1
            break
    if not substituted and not any(q is not None for q in quantities):
        if re.search(r"(?<![\w.])" + _NUMBER, line[marker.end():]):
            raise ValueError("Numeric power needs an explicit W result")
        return 0  # Symbolic relations alone never establish the numeric bound.
    if any(q is None for q in quantities[first:]):
        raise ValueError("Unsupported power quantity chain")
    bound = quantities[first][0]
    relaxed = False
    for index in range(first, len(operators)):
        left, right = quantities[index][0], quantities[index + 1][0]
        relation = operators[index]
        if not _power_comparison(left, relation, right):
            raise ValueError("Incorrect power comparison or unit conversion")
        # Select the first explicit conservative relaxation. Later limits and
        # unit conversions are comparisons, not the formal state.P bound.
        if substituted and not relaxed and relation in {"≤", "<=", "≦", "≥", ">=", "≧"}:
            bound, relaxed = right, True
    if not substituted and not named and marker[1] == "=" and all(op == "=" for op in operators):
        # An actual constant operating point is not the envelope value. It may
        # annotate an independently proved bound only when it lies inside it.
        if not _power_comparison(bound, "≤" if stress_kind == "upper_bound" else "≥", power):
            raise ValueError("Constant operating point lies outside the proved bound")
        return 0
    if not math.isclose(bound, power, rel_tol=1e-5, abs_tol=1e-12):
        raise ValueError("Power bound differs from state.P")
    if substituted and not _power_comparison(calculated, "≤" if stress_kind == "upper_bound" else "≥", power):
        raise ValueError("Power rounding must be conservative")
    return int(independent)


def _power_bound_formulas(lines: list[str], power: float | None, stress_kind: Any, *, reasons=None) -> bool:
    """Verify all numeric claims and require an independent arithmetic proof."""
    direction = _BOUND_DIRECTIONS.get(stress_kind) if isinstance(stress_kind, str) else None
    if not direction or power is None or not math.isfinite(power) or power < 0 or any(len(line) > 2000 for line in lines):
        if reasons is not None:
            reasons.append("功耗值、边界类型或公式长度无效")
        return False
    messages = {
        "Incorrect rated/limit/margin arithmetic or conversion": "额定、限额或余量的算术或单位换算不一致",
        "Incorrect numeric substitution": "数字代入与标注结果不一致",
        "Incorrect power comparison or unit conversion": "功率比较或单位换算不成立",
        "Incorrect auxiliary power comparison or conversion": "辅助功率比较或单位换算不成立",
        "Power expression must have W dimensions": "功率算式的量纲不是W",
        "Unsupported arithmetic or inconsistent dimensions": "算术不受支持或量纲不一致",
        "Power bound differs from state.P": "功耗上、下界与state.P不一致",
        "Constant operating point lies outside the proved bound": "实际功耗点超出所列界限",
        "Power rounding must be conservative": "功耗舍入方向不保守",
        "Separate independent power calculations with semicolons": "独立功率声明须用分号分开",
        "Incorrect numeric prefix relation": "前部数字算式的比较不成立",
    }
    checked = []
    # Check every clause; expose only static diagnostics, never source text.
    for line_index, line in enumerate(lines, 1):
        for clause_index, clause in enumerate(re.split(r"[；;]", line), 1):
            if not clause.strip():
                continue
            try:
                checked.append(_checked_power_formula(clause, power, stress_kind))
            except (ArithmeticError, ValueError, TypeError, SyntaxError, RecursionError) as error:
                if reasons is not None:
                    reason = messages.get(str(error), "公式语法或数值不受支持")
                    reasons.append(f"第{line_index}行第{clause_index}段：{reason}")
                return False
    passed = any(value == 1 for value in checked)
    if not passed and reasons is not None:
        reasons.append("缺少独立、完整的功耗数值推导")
    return passed


def _numeric_power_bound(expr: str, power: float | None, stress_kind: Any) -> bool:
    return _power_bound_formulas([expr], power, stress_kind)


def validate_power_decision(card: dict[str, Any]) -> list[str]:
    """Check a decision witness without requiring a fabricated global maximum."""
    import math
    basis = card.get("decision_basis")
    if not isinstance(basis, dict):
        return ["decision_basis must be object"]
    kind = basis.get("kind")
    if kind not in {"reachable_state", "proven_lower_bound", "normal_design_model", "envelope_upper_bound", "insufficient"}:
        return ["decision_basis.kind invalid"]
    if kind == "insufficient":
        return validate_decision_audit(card) + ([] if card.get("verdict_zh") == "待确认" else ["insufficient evidence requires 待确认"])
    errs = validate_decision_audit(card, required=kind == "normal_design_model")
    try:
        value, limit = float(basis["value_w"]), float(basis["limit_w"])
        if not all(math.isfinite(x) and x >= 0 for x in (value, limit)):
            raise ValueError()
        actual_limit = float((card.get("electrical") or {})["P_limit"])
        if not math.isclose(limit, actual_limit, rel_tol=1e-6, abs_tol=1e-12):
            errs.append("decision_basis limit differs from electrical.P_limit")
        if card.get("verdict_zh") == "否" and (kind not in {"reachable_state", "proven_lower_bound", "normal_design_model"} or value <= limit):
            errs.append("否 requires reachable stress or proven lower bound above limit")
        if card.get("verdict_zh") == "是" and (kind != "envelope_upper_bound" or value > limit):
            errs.append("是 requires envelope upper bound within limit")
    except (KeyError, TypeError, ValueError):
        errs.append("decision_basis requires finite nonnegative value_w/limit_w and P_limit")
    if not basis.get("evidence") or not basis.get("conditions"):
        errs.append("decision_basis requires evidence and conditions")
    if kind in {"reachable_state", "proven_lower_bound", "normal_design_model", "envelope_upper_bound"}:
        idx = basis.get("state_index")
        states = card.get("states") or []
        if type(idx) is not int or not 0 <= idx < len(states):
            errs.append("decision_basis.state_index invalid")
        elif states[idx].get("status") in {"unmapped", "conditional"} or states[idx].get("role") in {"unmapped", "worst_candidate"}:
            errs.append("decision witness cannot be unmapped or conditional")
        elif kind == "proven_lower_bound" and states[idx].get("stress_kind") != "lower_bound":
            errs.append("lower-bound witness requires stress_kind=lower_bound")
        elif kind == "envelope_upper_bound" and states[idx].get("stress_kind") != "upper_bound":
            errs.append("envelope decision requires stress_kind=upper_bound")
        elif kind != "envelope_upper_bound" and states[idx].get("stress_kind") == "upper_bound":
            errs.append("an upper bound cannot establish a reachable failure witness")
        else:
            try:
                if not math.isclose(float(states[idx]["P"]), float(basis["value_w"]), rel_tol=1e-5, abs_tol=1e-9):
                    errs.append("decision witness value differs from state.P")
            except (KeyError, TypeError, ValueError):
                errs.append("decision witness requires numeric state.P")
    return errs


def validate_worst_case(card: dict[str, Any]) -> list[str]:
    """Validate ranking separately from sufficient evidence for a verdict."""
    import math
    worst = card.get("worst_case")
    if worst is None:  # Existing cards remain readable; new prompt requires this field.
        return []
    if not isinstance(worst, dict):
        return ["worst_case must be object"]
    states = card.get("states") or []
    if not isinstance(states, list) or any(not isinstance(state, dict) for state in states):
        return ["worst_case requires a list of state objects"]
    if any(state.get("stress_kind") is not None and not isinstance(state["stress_kind"], str) for state in states):
        return ["worst_case state stress_kind must be string"]
    errors = []
    def power(state):
        value = state.get("P")
        return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None
    for key, conditional in (("confirmed_state_index", False), ("conditional_state_index", True)):
        candidates = [i for i, state in enumerate(states)
                      if power(state) is not None and state.get("stress_kind") not in {"upper_bound", "lower_bound"}
                        and state.get("role") not in {"idle", "witness"}
                      and state.get("status") != "unmapped"
                      and (state.get("status") == "conditional") == conditional]
        index = worst.get(key)
        if not candidates:
            if index is not None:
                errors.append(f"worst_case.{key} has no eligible state")
            continue
        if type(index) is not int or index not in candidates:
            errors.append(f"worst_case.{key} must select an eligible state")
        elif power(states[index]) + 1e-10 < max(power(states[i]) for i in candidates):
            errors.append(f"worst_case.{key} omitted a larger comparable power")
        elif states[index].get("role") != ("worst_candidate" if conditional else "worst"):
            errors.append(f"worst_case.{key} role does not match ranking")
    comparison = worst.get("comparison")
    rows = comparison if isinstance(comparison, list) else []
    if sorted(row.get("state_index") for row in rows if isinstance(row, dict) and type(row.get("state_index")) is int) != list(range(len(states))):
        errors.append("worst_case.comparison must cover each state once")
    if any(not isinstance(row, dict) or not row.get("reason") for row in rows):
        errors.append("worst_case.comparison requires reasons")
    if not isinstance(worst.get("unresolved"), list):
        errors.append("worst_case.unresolved must be list")
    for index, state in enumerate(states):
        if state.get("status") == "conditional":
            if power(state) is None or not isinstance(state.get("conditions"), str) or not state["conditions"].strip() or state.get("stress_kind") not in {"modeled", "upper_bound", "lower_bound"}:
                errors.append(f"conditional states[{index}] requires numeric P, conditions and modeled/upper_bound/lower_bound stress_kind")
            if state.get("stress_kind") in {"upper_bound", "lower_bound"} and state.get("role") in {"worst", "worst_candidate"}:
                errors.append(f"conditional bound states[{index}] cannot claim a ranked working-point role")
    return errors
_P11 = frozenset({"P11-A01", "P11-A03"})
_DEEP = frozenset(
    {
        "P11-A01",
        "P11-A02",
        "P11-A03",
        "P11-A04",
        "P11-A05",
        "P11-A06",
        "P11-A07",
        "P12-A01",
        "P12-A02",
        "P12-A03",
        "P12-A04",
        "P12-A05",
        "P12-A06",
        "P12-A07",
        "P12-A08",
        "P21-A01",
    }
)
_FORBIDDEN_COPY = (
    "本态",
    "· P=",
    "case_id=",
    "dry-run",
    "dry_run",
)
# 仅 headline/summary（对象摘要）禁内部词；stuck 可留工程备忘，展示层会改写/降级
_FORBIDDEN_BLURB = (
    "程序应力包",
    "interface_specs",
    "rail=unknown",
    "rail unknown",
)
_FORBIDDEN_CASE_IDS = (
    "supply_off",
    "clamp_work",
    "switch_pull",
    "port_gnd",
    "dc_work",
)


def validate_judge_card(
    card: dict[str, Any],
    *,
    rule_id: str,
    obj: str,
    board: Path | None = None,
) -> list[str]:
    if card.get("schema_version") == "review.v1":
        from agent_review.review_contract import validate_review
        return validate_review(card, board=board, rule_id=rule_id, obj=obj)
    errs: list[str] = []
    from agent_review.main_analysis import VERSION as MAIN_VERSION, validate as validate_main, pilot_enabled
    workbench_context = {}
    if board:
        from kernel.util import read_json
        from agent_review.batching import safe_object_name
        workbench = Path(board)/'review_out/agent_jobs'/rule_id/(safe_object_name(obj)+'.context.json')
        if workbench.is_file():
            workbench_context = read_json(workbench)
    main_required = workbench_context.get('main_analysis_contract') == MAIN_VERSION
    errs.extend(validate_main(card, required=main_required or bool(board and pilot_enabled(board,rule_id,obj))))
    if card.get("rule_id") != rule_id:
        errs.append(f"rule_id mismatch: {card.get('rule_id')!r} != {rule_id!r}")
    if str(card.get("object") or "") != str(obj):
        errs.append(f"object mismatch: {card.get('object')!r} != {obj!r}")
    vz = card.get("verdict_zh")
    if vz not in VERDICTS:
        errs.append(f"verdict_zh invalid: {vz!r}")
    for k in ("phenomenon", "calc", "stuck", "conclusion"):
        if not str(card.get(k) or "").strip():
            errs.append(f"empty four-grid field: {k}")
    current_analysis = card.get('analysis_contract') == ANALYSIS_VERSION
    if not (card.get('meta') or {}).get('dry_run') and workbench_context.get('analysis_contract') == ANALYSIS_VERSION and not current_analysis:
        errs.append('analysis_contract required by current workbench')
    if current_analysis:
        from jsonschema import Draft202012Validator
        analysis = card.get('analysis_steps')
        errs.extend('analysis_steps: '+e.message for e in Draft202012Validator(step_schema()).iter_errors(analysis))
        assumptions = card.get('key_assumptions')
        assumption_schema = {'type':'array','items':{'type':'object','required':['id','text','status','evidence_refs'],
            'properties':{'id':{'type':'string','minLength':1},'text':{'type':'string','minLength':1},
            'status':{'enum':['supported','model_assumption','unresolved']},'evidence_refs':{'type':'array','items':{'type':'string'}}}}}
        assumption_errors = list(Draft202012Validator(assumption_schema).iter_errors(assumptions))
        errs.extend('key_assumptions: '+e.message for e in assumption_errors)
        steps=[x['text'] for x in (analysis if isinstance(analysis,list) else []) if isinstance(x,dict) and isinstance(x.get('text'),str)]
        if not assumption_errors and isinstance(analysis,list):
            errs.extend(validate_links(analysis,assumptions,[a['id'] for a in assumptions]))
            known={e.get('id') or e.get('path') for e in card.get('evidence_refs',[]) if isinstance(e,dict)}
            for assumption in assumptions:
                if any(ref not in known for ref in assumption['evidence_refs']):errs.append('key assumption evidence reference not in evidence_refs')
                if assumption['status']=='supported' and not assumption['evidence_refs']:errs.append('supported key assumption needs evidence')
        if 'reasoning_steps' in card or 'assumptions' in card:errs.append('new analysis contract must not duplicate legacy prose fields')
        from agent_review.detail_prose import mask_source_identifiers
        from agent_review.detail_view import QUANTITY
        if any(re.search(QUANTITY,mask_source_identifiers(step,{'card':card})) for step in steps):errs.append('analysis_steps must describe process without numeric quantities')
        if steps and obj not in ' '.join(steps):errs.append('analysis_steps must mention current object')
        if not steps:errs.append('analysis_steps required non-empty list')
    else:
        steps = card.get("reasoning_steps")
        if not isinstance(steps, list) or not steps:
            errs.append("reasoning_steps required non-empty list")
        elif rule_id in _DEEP and len(steps) < 3:
            errs.append("深审 reasoning_steps 至少 3 步（通路/工况/对照）")
    if not str(card.get("location_one_liner") or "").strip():
        errs.append("location_one_liner required")
    if vz in {"否", "待确认"}:
        if not str(card.get("summary") or "").strip():
            errs.append("summary required for 否/待确认")
        if not str(card.get("action") or "").strip():
            errs.append("action required for 否/待确认")
    if vz == "是":
        refs = card.get("evidence_refs") or []
        if not isinstance(refs, list) or not refs:
            errs.append("是 requires evidence_refs")
    if rule_id in _DEEP and vz in {"是", "否", "待确认"}:
        hl = str(card.get("headline_zh") or "").strip()
        if not hl:
            errs.append(
                "headline_zh required for 深审 是/否/待确认（场景→数字来源→数字→限额/缺口）"
            )
        elif "：" not in hl and ":" not in hl:
            errs.append("headline_zh 须为「位号：…」一句")
        elif len(hl) > 90:
            errs.append(f"headline_zh 过长（{len(hl)}字，建议≤65）")

    if rule_id in _P11:
        states = card.get("states")
        if not isinstance(states, list) or not states:
            errs.append("P11 requires non-empty states[]（Agent 自列工况）")
        else:
            for i, s in enumerate(states):
                if not isinstance(s, dict):
                    errs.append(f"P11 states[{i}] must be object")
                    continue
                name = str(s.get("name_zh") or s.get("name") or "").strip()
                cid = str(s.get("case_id") or "").strip()
                if not name and not cid:
                    errs.append(f"P11 states[{i}] 需要 name_zh 或 case_id")
                # Agent 审图卡：工况名须是通路描述，禁止纯分类词
                meta = card.get("meta") if isinstance(card.get("meta"), dict) else {}
                agentish = bool(card.get("key_assumptions") or card.get("assumptions")) and not meta.get("program_reference_only")
                thin = name in {
                    "有源拉低",
                    "直流工作",
                    "正常钳位",
                    "外接接地",
                    "其它工况",
                    "未评",
                }
                if agentish and thin:
                    errs.append(
                        f"P11 states[{i}] name_zh 须写通路/端态连通，禁止仅用分类词: {name!r}"
                    )
                # Agent 卡：每态须有完整 formula_lines 推理链（对人展示主合同）
                if (
                    agentish
                    and rule_id == "P11-A01"
                    and str(s.get("status") or "") != "unmapped"
                    and "未建模" not in name
                ):
                    fl = [
                        x
                        for x in (s.get("formula_lines") or s.get("lines") or [])
                        if isinstance(x, dict) and str(x.get("expr") or "").strip()
                    ]
                    # 降压：有可核对公式即可；单行完整 P= 也接受（避免凑行）
                    if len(fl) < 1:
                        errs.append(f"P11 states[{i}] 须含 formula_lines，当前 0")
                    else:
                        try:
                            p_val = float(
                                s["P"] if s.get("P") is not None else s.get("P_w")
                            )
                        except (TypeError, ValueError, KeyError):
                            p_val = None
                        # P≈0 态允许「可忽略/≈0」，不强制再写 P= 行
                        near_zero = p_val is not None and abs(p_val) < 1e-9
                        expressions = [str(x.get("expr") or "") for x in fl]
                        has_bound = s.get("stress_kind") in ("upper_bound", "lower_bound") or any(_POWER_BOUND.search(_normalize_power_formula_text(line)) or _NAMED_POWER_BOUND.search(_normalize_power_formula_text(line)) for line in expressions)
                        formula_reasons = []
                        has_p = _power_bound_formulas(expressions, p_val, s.get("stress_kind"), reasons=formula_reasons) if has_bound else re.search(
                            r"(P\s*[=≈]|P_[A-Za-z0-9]+\s*[=≈]|P\s*[\(（]|(?:功耗|耗散功率|耗散)\s*[=≈]"
                            r"|耗散\s*可忽略|功耗\s*可忽略|≈\s*0\s*W"
                            r"|\d+(?:\.\d+)?\s*mW\b)",
                            "\n".join(_normalize_power_formula_text(line) for line in expressions),
                        )
                        if not has_p and (not near_zero or has_bound):
                            detail = "；".join(formula_reasons)
                            errs.append(f"P11 states[{i}] formula_lines 须含功耗 P= 代入或与 P 及单位一致的上下界数值推导" + ("：" + detail if detail else ""))
                        missing_src = sum(1 for x in fl if not str(x.get("src") or "").strip())
                        if missing_src:
                            errs.append(f"P11 states[{i}] formula_lines 有 {missing_src} 行缺 src")
                    if s.get("P") is None and s.get("P_w") is None:
                        errs.append(f"P11 states[{i}] 须填写 P（W）")
                fig = s.get("figure")
                if isinstance(fig, dict):
                    pages = fig.get("pages") if isinstance(fig.get("pages"), list) else [fig]
                    if not pages:
                        errs.append(f"P11 states[{i}] figure.pages 为空")
                    for pi, page in enumerate(pages):
                        if not isinstance(page, dict):
                            errs.append(f"P11 states[{i}] figure.pages[{pi}] 须为对象")
                            continue
                        paths = ([page["path"]] if page.get("path") else []) + list(page.get("paths") or [])
                        if not paths or any(not isinstance(p, list) or len(p) < 2 for p in paths):
                            errs.append(f"P11 states[{i}] figure.pages[{pi}] 每条 path/paths 至少两个名字")
                        path = [str(x).strip() for p in paths if isinstance(p, list) for x in p if str(x).strip()]
                        for mi, marker in enumerate(page.get("markers") or []):
                            if not isinstance(marker, dict):
                                errs.append(f"P11 states[{i}] figure 圆点[{mi}] 须为对象")
                                continue
                            on = str(marker.get("on") or "").strip()
                            if not on:
                                errs.append(f"P11 states[{i}] figure 圆点[{mi}] 缺 on")
                            # Off-state devices may be annotated without pretending
                            # that current flows through them. Geometry checks location.
                            if not str(marker.get("title") or "").strip():
                                errs.append(f"P11 states[{i}] figure 圆点[{mi}] 缺 title")
                conclusion = str(s.get("结论") or s.get("conclusion") or "").strip()
                if conclusion and ("未建模" not in conclusion):
                    has_arrow = "→" in conclusion or "->" in conclusion
                    has_cmp = any(x in conclusion for x in ("≤", ">=", "≤", "＞", ">", "<", "≦"))
                    has_vz = any(
                        x in conclusion
                        for x in (
                            "是",
                            "否",
                            "待确认",
                            "超标",
                            "达标",
                            "满足",
                            "不满足",
                            "超",
                            "低于",
                            "高于",
                            "超过",
                            "上界",
                        )
                    )
                    has_limit = any(
                        x in conclusion
                        for x in (
                            "P_limit",
                            "V_limit",
                            "限额",
                            "P_max",
                            "V_max",
                            "Pmax",
                            "Vmax",
                        )
                    )
                    # 过程优先：有限额对照或结论词即可；不逼长对照句
                    if not (
                        has_arrow
                        or (has_cmp and has_vz)
                        or (has_limit and has_vz)
                        or (has_cmp and has_limit)
                        or has_limit
                        or has_vz
                    ):
                        errs.append(
                            f"P11 states[{i}] 结论须能对照限额或给出是|否|超|低于等: {conclusion!r}"
                        )
                # Agent 自己标 unmapped 则禁止升绿
                if vz == "是" and (
                    s.get("status") == "unmapped" or str(s.get("role") or "") == "unmapped"
                ):
                    errs.append("P11 states 含 unmapped 时禁止 verdict_zh=是")

        elec = card.get("electrical") or {}
        if not isinstance(elec, dict):
            errs.append("P11 electrical must be object")
            elec = {}
        if rule_id == "P11-A01":
            errs.extend(validate_worst_case(card))
            basis = card.get("decision_basis")
            if basis is not None:
                errs.extend(validate_power_decision(card))
            if elec.get("P_rated") is None and "缺额定" not in str(card.get("stuck") or ""):
                if vz == "是":
                    errs.append("P11-A01 是 requires electrical.P_rated")
            if vz == "是" and not str(elec.get("cite") or "").strip():
                errs.append("P11-A01 是 requires electrical.cite")
            calc = str(card.get("calc") or "")
            if vz != "不适用" and not re.search(
                r"(P\s*=|P_[A-Za-z0-9]+\s*=|P≈|P_max|mW|功耗|\d+(?:\.\d+)?\s*W\b)", calc
            ):
                errs.append("P11-A01 calc 须含功耗/P/mW 代入痕迹")
        if rule_id == "P11-A03":
            if elec.get("V_rated") is None and "缺额定" not in str(card.get("stuck") or ""):
                if vz == "是":
                    errs.append("P11-A03 是 requires electrical.V_rated")
            if vz == "是" and not str(elec.get("cite") or "").strip():
                errs.append("P11-A03 是 requires electrical.cite")
            calc = str(card.get("calc") or "")
            if vz != "不适用" and not re.search(r"(V\s*=|V_max|耐压|电压)", calc):
                errs.append("P11-A03 calc 须含电压代入痕迹")

        if len(str(card.get("summary") or "")) > 600:
            errs.append("summary too long (keep <=600 chars)")

        # 过程痕迹：phenomenon 或 reasoning 应触及通路/工况
        process_blob = "\n".join(
            [
                str(card.get("phenomenon") or ""),
                "\n".join(str(x) for x in (steps or [])),
                str(card.get("assumptions") or ""),
            ]
        )
        if vz != "不适用" and len(process_blob.strip()) < 20:
            errs.append("P11 推理过短：请写通路与工况假设")

        visible = [
            str(card.get(k) or "")
            for k in ("phenomenon", "calc", "stuck", "conclusion", "summary", "action")
        ]
        sol = card.get("solution") if isinstance(card.get("solution"), dict) else {}
        for st in sol.get("states") or []:
            if not isinstance(st, dict):
                continue
            for step in st.get("steps") or []:
                if isinstance(step, dict):
                    visible.append(str(step.get("eq") or ""))
            for row in st.get("lines") or []:
                if isinstance(row, dict):
                    visible.append(str(row.get("src") or ""))
                    visible.append(str(row.get("expr") or ""))
        for st in states if isinstance(states, list) else []:
            if isinstance(st, dict):
                visible.append(str(st.get("结论") or st.get("conclusion") or ""))
                visible.append(str(st.get("name_zh") or ""))
        blob = "\n".join(visible)
        for bad in _FORBIDDEN_COPY:
            if bad in blob:
                errs.append(f"P11 对人可见文案禁词: {bad!r}")
        for en in _FORBIDDEN_CASE_IDS:
            if re.search(rf"\b{en}\b", blob):
                errs.append(f"P11 对人可见文案禁止裸英文工况 id: {en}")
        blurb_blob = "\n".join(
            str(card.get(k) or "") for k in ("headline_zh", "summary")
        )
        for bad in _FORBIDDEN_BLURB:
            if bad in blurb_blob:
                errs.append(f"headline_zh/summary 禁内部词: {bad!r}（请改成任务书/手册白话）")

        for st in sol.get("states") or []:
            if not isinstance(st, dict):
                continue
            sol_steps = st.get("steps") or []
            if not sol_steps:
                continue
            eq = str((sol_steps[-1] or {}).get("eq") or "") if isinstance(sol_steps[-1], dict) else ""
            if eq and ("→" not in eq) and ("未建模" not in eq):
                errs.append(f"P11 solution.states 末行须为对照句(含→): {eq!r}")
    return errs
