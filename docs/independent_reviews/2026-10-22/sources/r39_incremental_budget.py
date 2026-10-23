"""Recompute conditional normal-periodic engineering bounds; not a circuit simulator."""
import json
import math
from pathlib import Path

rlo = 3900 * .99 * .987
rhi = 3900 * 1.01 * 1.013
r21lo = 10000 * .99 * .987
budget = 72 / r21lo
vlimit = math.sqrt(.05 * rlo)
rows = []
for ron in (500, 2000):
    conductance = 4 / (rhi + ron)
    coefficient = conductance * (1 + r21lo * conductance)
    delta = math.sqrt(budget / coefficient)
    rows.append(dict(ron_differential_ohm=ron, conductance_s=conductance,
                     coefficient_s=coefficient, delta_v_rms=delta,
                     r39_power_bound_w=(11.5 + delta)**2 / rlo,
                     r39_power_bound_at_vf2_5_w=(12.5 + delta)**2 / rlo,
                     additional_active_power_to_fail_bound_w=
                     coefficient*(vlimit-11.5)**2-budget,
                     reference_voltage_to_fail_bound_v=vlimit-delta))
gcritical = (-1 + math.sqrt(1+4*r21lo*budget/(vlimit-11.5)**2))/(2*r21lo)
result = dict(r39_min_ohm=rlo, r39_max_ohm=rhi, r21_min_ohm=r21lo,
              coupling_budget_w=budget, r39_voltage_limit_v=vlimit, rows=rows,
              differential_ron_to_fail_bound_ohm=4/gcritical-rhi,
              scope='Conditional incremental-passive normal periodic model; not manufacturer full-state certification.')
out = Path(__file__).with_name('R39-incremental-budget.json')
out.write_text(json.dumps(result, indent=2), encoding='utf-8')
print(json.dumps(result, indent=2))
