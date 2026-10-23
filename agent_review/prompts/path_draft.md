# path_draft · 分域应力通路探路

## 任务

只为本域列出**会改变电阻直流稳态功耗**的应力通路与出口事件。
禁止计算电压/电流/功率；禁止填写瓦数；禁止一次覆盖全板其它页。

## 输入（只读）

1. `review_out/board_paths/draft/<domain_id>.slice.json` — 域内位号、网、页码切片
2. 原理图 PDF 中本域页（页名见 slice.`page_names`）

## 输出（必须）

只写一个 JSON 文件到：

`review_out/board_paths/draft/<domain_id>.json`

结构：

```json
{
  "domain_id": "<slice.domain_id>",
  "paths": [
    {
      "path_id": "<unique_path_id>",
      "text": "<从材料核实的有序通路>",
      "refs": [],
      "supply_net": "<材料中的供电网名>",
      "exit_net": "<材料中的出口网名>",
      "nodes": [],
      "actors": [],
      "events": [],
      "evidence": []
    }
  ]
}
```

## 规则

- 上述仅为结构，不是电路范例。`refs/nodes` 填真实顺序；`actors` 为 `{ref, role}`；`events` 为 `{event_id, name_zh, what_changes}`；`evidence` 为 `{page, note}`。所有条目从当前域材料推导，不套用特定位号或固定拓扑。

- 每条 path 的 `refs` 必须是本域切片里真实存在的位号，且顺序沿供电→出口。
- `events` 只列会改本串直流功耗的出口事件；不要把每条信号线都当成通路。
- 工作工况优先：`clamp_work` / `switch_pull` / `port_gnd`（**不要列未上电 / supply_off**）。
- 若存在本域内可核对但非常规的事件，仍可列出（程序将标 unmapped，不得删）。
- 禁止输出任何电压、电流、功率数字字段。
- 禁止改写网表、禁止写其它目录。
