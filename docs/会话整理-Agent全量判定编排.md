# 会话整理：Agent 全量判定编排

供其他 AI / 协作者快速对齐上下文。权威代码在 `K:\project\schematic-intake`（已从 `schematic-review` 迁入）。

---

## 1. 项目是干什么的

**schematic-intake** = 原理图评审的「材料 prep + Agent 全量判定」同仓。

| 阶段 | 谁做 | 产物 |
|---|---|---|
| s0–s5 | 程序 `kernel/` | `board_kg.json`、规格书缓存等 |
| 判定 | Cursor Agent（经 SDK） | 对象级是/否/待确认 |
| 交付 | 程序聚合 | **`engineer_review.html`**（唯一对人主交付） |

不做：旧「程序 L3 落词 + 配方闸升绿」路径（已废弃）。

检查项：产品中心子集 **19 项**（模板名仍叫 product_center_15），见 `rules/product_center_15.json`。

---

## 2. 本会话拍板的架构定调

### 2.1 判定权

- **全部交给 Agent**，程序不写「是/否/待确认」。
- 程序只做：收料、枚举对象、上下文仓、队列、schema 校验、项级聚合、门禁、报告。

### 2.2 为何要「主调度 + 子 Agent」

有价值，但 **主调度不能是自由发挥的大模型**（会漂移）。

| 角色 | 谁 | 干什么 | 禁止 |
|---|---|---|---|
| Stage Machine | Python/Node | 推进阶段、发队列、校验、聚合、门禁 | 写判定词 |
| Dialog Supervisor | 对话框主 Agent | 调 CLI、读 gate、resume | 代写对象卡、改仓 |
| Context Builder | SDK 子 Agent | 只写 quarantine 事实候选 | 写 live 仓、写是/否 |
| Context Committer | 程序 | 校验后 commit + seal | 信任未校验候选 |
| Judge | SDK 子 Agent | 读密封上下文 + 单项规则，写对象判定 | 写仓、猜数升绿 |

### 2.3 共享上下文仓（核心）

同一器件会出现在多个检查项 → **事实只建一次，判定按项多次读**。

路径：`review_out/context_store/`

- 三层：`objects/<ref>.json`、`mpn/<mpn>.json`、`nets/<net>.json`
- 生命周期：`init → build(quarantine) → commit → seal → fetch`
- 补充：`patch`（只加空字段）；纠正：`invalidate → rebuild`；删除：tombstone
- **防污染**：Builder 只写 quarantine；Judge/对话框零写仓；数值要 provenance；seal 后只读；hash 钉死；污染停机

实现：`agent_review/context_store.py`、`context_schema.py`  
当前默认：BoardKG **程序种子**写入 quarantine 再 commit（`--use-sdk-context` 才让 SDK 补全）。

### 2.4 细分组、小批次

- 一项一组（`agent_groups_one_per_item`）
- 单次 Judge：**≤3 对象且同一 rule_id**
- 默认并发 **16**，模型 **`auto`**
- 编排器：`agent_review/orchestrator.mjs`（`@cursor/sdk`）

### 2.5 Prompt

- Judge：**一检查项一份** `agent_review/prompts/judge/<rule_id>.md`（共 19）
- 公共约束：`prompts/_common_judge.md`
- Context：按 kind 三份 `object.md` / `mpn.md` / `net.md`（不按检查项拆）
- 缺 judge prompt → 拒派发该 rule

### 2.6 最终交付

**唯一对人主交付**：`boards/<板>/review_out/engineer_review.html`

七章（全在一份 HTML 里）：

1. 封面与总评  
2. 优先处理清单（位置 / 问题一句 / 推理摘要 / 建议动作）  
3. 检查项全册（项级推理总览 + 对象表）  
4. 对象详情（哪里 → 逐步推理 → 结论与改法）  
5. 待确认与缺资料  
6. 材料与规格书快照  
7. 附录（可折叠）

「一眼读懂」硬合同：哪里 / 怎么推（`reasoning_steps[]`）/ 结论与下一步（`summary`+`action`）。

机读附件：`verdicts.json`、`findings.json`、`report_data.json`、`context_store/`、`agent_review_gate.json`。

---

## 3. 阶段机

```
prep → enumerate → context_build → judge → aggregate → gate
```

入口：

```bat
cd K:\project\schematic-intake
set PYTHONPATH=%CD%
python -m agent_review.run --board boards\<板名>
```

常用开关：

| 开关 | 含义 |
|---|---|
| `--dry-run` | 不调 SDK；上下文种子 + 对象卡填「待确认」；仍出完整报告 |
| `--resume` | 只补未完成 Judge |
| `--rebuild-context` | 升 epoch 重建上下文仓（不强制重跑 s0–s5） |
| `--rule-ids P05-A01` | 只跑指定检查项 |
| `--concurrency 16` | 板内并发 |

门禁通过：`review_out/agent_review_gate.json` 中 `passed=true`。

对话 skill：`.cursor/skills/agent-review/SKILL.md`  
规则：`.cursor/rules/agent-review-default.mdc`

---

## 4. 目录要点（intake）

```
schematic-intake/
  kernel/                 # s0–s5 材料（不落词）
  agent_review/           # 全量判定阶段机
    run.py
    context_store.py
    enumerate.py
    batching.py
    orchestrator.mjs
    prompts/
    stages/
    report.py
  rules/                  # product_center_15.json 等
  sdk/                    # @cursor/sdk（npm）
  boards/<板>/review_out/
  docs/会话整理-Agent全量判定编排.md   # 本文
```

材料-only：`python kernel\run.py --board ...`  
判定：`python -m agent_review.run --board ...`

---

## 5. 鉴权（Cursor）

- 环境变量 `CURSOR_API_KEY`：**可选**
- 本机实测：`Cursor.auth.status()` 可为 `logged-in`（桌面登录态即可）
- 未登录时：设 `CURSOR_API_KEY` 或 `Cursor.auth.login`
- `Agent.prompt` 冒烟在终端里可能较慢/卡住；编排走 `agent_review/orchestrator.mjs`，需 `NODE_PATH=sdk/node_modules`（阶段机已设置）

---

## 6. 已验证结果

在 **schematic-intake** 上：

```text
python -m agent_review.run --board boards\0918全量 --dry-run --rule-ids P05-A01 --rebuild-context
→ context 135/135 sealed
→ judge done=51 failed=0
→ gate passed=True
→ engineer_review.html
```

正式全量：去掉 `--dry-run`（依赖 Cursor 登录/API）。

---

## 7. 明确不要做的事

1. 不要恢复 `all15` 程序落词当权威、不要配方闸升绿合同。  
2. 不要手改 `context_store/**`、`verdicts.json`、`agent_jobs/**`。  
3. 不要一次 prompt 塞多个检查项或扫全板。  
4. 不要用 Meta-LLM 自由决定下一阶段（阶段机说了算）。  
5. 不要把 SDK 日志当给人交付；交付只认 `engineer_review.html`。  
6. 工作区以 **schematic-intake** 为准；schematic-review 上同名实现视为迁出源，不再作为主仓。

---

## 8. 检查项清单（19）

| 编号 | 名称（摘要） | 严重度 |
|---|---|---|
| P04-A01 | 网络命名合法 | 中 |
| P04-A03 | 网名冲突/跨页一致 | 中 |
| P05-A01 | 电阻标注可解析 | 中 |
| P05-A06 | 电容标注可解析 | 中 |
| P07-A05 | 电源/信号域隔离 | 高 |
| P11-A01 | 电阻功耗降额 | 严重 |
| P11-A03 | 电容耐压降额 | 严重 |
| P11-A07 | 电感 Isat/温升 | 严重 |
| P12-A01 | IC 供电范围 | 严重 |
| P14-A02 | 入口防反 | 严重 |
| P14-A05 | 浪涌防护匹配 | 高 |
| P14-A06 | 供电口限流/负载开关 | 严重 |
| P16-A01 | IC 电源脚去耦 | 高 |
| P16-A05 | 模拟电源隔离 | 高 |
| P18-A02 | 晶振负载电容 | 高 |
| P19-A03 | 复位上拉/下拉 | 高 |
| P21-A01 | 发送/接收电平兼容 | 高 |
| P42-A01 | 位号唯一 | 严重 |
| P42-A04 | BOM↔原理图 MPN/Value 一致 | 严重 |

细则：`rules/intent_steps_15.json`、`rules/xlsx_detail_15.json`；Judge 专属文案：`agent_review/prompts/judge/<rule_id>.md`。

---

## 9. 给后续 AI 的最短行动清单

1. 打开工作区 `K:\project\schematic-intake`。  
2. 读 `.cursor/skills/agent-review/SKILL.md` 与本文。  
3. 改判定逻辑 → 动 `agent_review/` + 对应 `prompts/judge/*.md`，不要回潮 all15 落词。  
4. 改材料解析 → 动 `kernel/` s0–s5。  
5. 验证：先 `--dry-run --rule-ids P05-A01`，再去掉 dry-run 跑真 Agent。  
6. 验收：打开 `engineer_review.html` + `agent_review_gate.json.passed==true`。
