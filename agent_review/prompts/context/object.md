# Context Builder · object

只抽取**事实**写入 quarantine JSON，不写任何检查项结论。

输出字段（fields）：ref, type, value, mpn, footprint, manufacturer, pins, nets, location, neighbors, in_bom, in_tel, in_jlc。

provenance 必填，source_kind 用 board_kg / bom / netlist / jlc_project。

禁止字段：verdict_zh、是、否、待确认、建议通过。
