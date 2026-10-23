# 料号额定参数查参（材料瀑布末位）

按任务给出的 **MPN + 字段**，查该额定值并写入指定 JSON。可查本地手册 PDF、立创参数、厂商资料页；**能查到就写，附出处**。

## 只做这件事

写出一个 JSON 到 `out_path`（禁止改其它文件、禁止写评审卡）。

| 字段 | 要求 |
|------|------|
| `mpn` / `field` | 与输入一致 |
| `value` | 数字；查不到则 `null`（寿命/IP 等字符串可放 `value_text`，`value` 用 null） |
| `unit` | V / A / W / ℃ / 次 等 |
| `cite` | `value` 非 null 时必填：手册页/立创参数名/可核验 URL + 原文片段 |
| `confidence` | `high`（出处可核验）或 `low`（仅模糊记忆） |

## 温度类字段（`t_op_*` / `t_max_c`）

- 优先：**工作环境 / TOPR / Operating ambient**。
- 若资料只有 **结温 TJ** 或 **存储 Tstg**：仍把数字写入 `value`，并在 `cite` 写明「结温 TJ」或「存储 Tstg」；`confidence` 用 `high`（有出处）即可，由后续审图决定是否当环境窗。
- 不要因为「不是环境窗」就交空——**先把数抽进材料**。

## 示例

```json
{
  "mpn": "0603WAF4702T5E",
  "field": "p_rated_w",
  "value": 0.1,
  "unit": "W",
  "cite": "立创参数 额定功率=1/10W",
  "confidence": "high"
}
```

禁止编造 URL；不要输出是/否/待确认。
