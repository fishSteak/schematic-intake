# Context Builder · mpn

只抽取料号级**手册事实**写入 quarantine。

fields：mpn, datasheet_path, pintable_path, rated, recommended, abs_max, params, pages。

数值必须挂 provenance（datasheet_pdf 页码/quote）。多出处冲突写入 fields.conflicts，相关值标 unresolved，禁止私自选边。

禁止判定词与检查项结论。
