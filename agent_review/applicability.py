"""Conservative exclusions from explicit material facts; unknowns stay in deep review."""
import re

def exclusions(kg,rule_id,objects):
    result={}
    for obj in objects:
        c=(kg.get('components') or {}).get(obj,{})
        # Do not infer material from footprint size, voltage or incomplete MPNs.
        fields={k:c.get(k) for k in ('dielectric','material','description','value','type')}
        text=' '.join(str(v or '') for v in fields.values())
        ceramic=bool(re.search(r'\b(?:X7R|X5R|C0G|NP0|Y5V|MLCC|ceramic)\b|陶瓷',text,re.I))
        tantalum=bool(re.search(r'tantalum|钽',text,re.I))
        nonceramic=bool(re.search(r'aluminum|aluminium|铝电解|薄膜|\bfilm\b',text,re.I))
        if rule_id=='P11-A04' and (ceramic or nonceramic) and not tantalum:
            result[obj]={'reason':'材料明确为非钽电容，本项不适用。','facts':fields}
        if rule_id=='P11-A05' and (tantalum or nonceramic) and not ceramic:
            result[obj]={'reason':'材料明确为非陶瓷电容，本项不适用。','facts':fields}
        if rule_id=='P12-A08' and c.get('external_interface') is False and c.get('external_interface_source'):
            result[obj]={'reason':'受控材料明确为板内接口，本项不适用。','facts':{k:c[k] for k in ('external_interface','external_interface_source')}}
        if rule_id=='P11-A02' and c.get('high_voltage_circuit') is False and c.get('high_voltage_source'):
            result[obj]={'reason':'受控材料明确为非高压回路，本项不适用。','facts':{k:c[k] for k in ('high_voltage_circuit','high_voltage_source')}}
    return result
