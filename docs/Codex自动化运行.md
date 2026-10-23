# Codex 自动化运行（2026-10-22 更新）

本仓是原理图自动化评审的唯一开发入口。Agent 后端已切换为本机 **Codex CLI**，不再导入或调用 Cursor SDK，也不需要 Cursor API 密钥。

## 环境与鉴权

- Python：先安装 `requirements.txt` 中的依赖，推荐使用下方的项目虚拟环境。
- Node.js：要求 >=24.0.0（见根目录 `package.json`）。运行时先读 `SCHEMATIC_NODE`，其次查 PATH，再尝试 Codex 提供的 `CODEX_MCP_NODE_PATH`；前端构建还需要可用的 npm。
- Codex：默认调用 PATH 中的 `codex`，可用 `SCHEMATIC_CODEX` 指定可执行文件。
- 鉴权：新环境执行 `codex login status`，未登录时按 CLI 提示完成登录。不复制登录凭据、不读取凭据内容、不依赖 Cursor 登录态。
- 模型默认继承用户的 Codex 配置；可用 `AGENT_JUDGE_MODEL` 显式指定。未指定时不再发送 Cursor 模型名。
- `report_ui/dist` 是本地构建产物，不进入 Git；首次 clone 后必须构建前端。根目录与 `sdk/` 的 `dependencies` 均为空，真实 Agent 通过本机 Codex CLI 执行，不需要安装 Cursor/OpenAI npm SDK。

## 首次 clone 后初始化

在仓库根目录执行以下 Windows PowerShell 命令。直接使用虚拟环境中的 Python，无须运行 `Activate.ps1`：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
npm --prefix report_ui ci --ignore-scripts
npm --prefix report_ui run build
codex login status
```

依赖版本按 Python 3.12 / Windows x64 验证（见 `requirements.txt`）；其他 Python 版本需另行验证。

下文运行和验证命令中的 `py` 表示已安装项目依赖的 Python。若按上面的 `.venv` 初始化，请将命令开头的 `py` 替换为 `.\.venv\Scripts\python.exe`；不需要激活虚拟环境。

## Git 内容范围

- 保留业务源码、规则、测试、依赖清单与锁文件，以及开发和运行所需文档。
- `.venv/`、`node_modules/`、`report_ui/dist/`、Python 缓存和大部分 `boards/*/review_out/` 已忽略；不需为 push 删除这些本地文件。
- 保留 `boards/<板>/*.json` 配置及 `boards/<板>/review_out/board_kg.json`：前者描述材料和评审配置，后者目前仍用于离线验证与既有入口，不能将整个 `review_out` 的例外直接去掉。
- 保留 `materials/` 原始输入及 `migration_baseline/` 迁移/回归基准，使新 clone 能准备材料并执行基准验证。生成报告、资料查询缓存和板级历史不等同于这些受控输入。

## 运行

在仓库根目录执行：

```powershell
# 材料准备，包含查参；不运行对象判定
py -m kernel.run --board boards/0918全量

# 用当前材料重建密封事实，不运行对象判定
py -m agent_review.run --board boards/0918全量 --only-stage context_build --rebuild-context

# 全量评审；续跑仅复用契约有效且上下文仍一致的对象卡
py -m agent_review.run --board boards/0918全量 --resume

# 报告
py -m agent_review.serve --board boards/0918全量
```

默认范围来自 `rules/product_center_15.json`（当前 30 项），不受上次缓存的局部运行范围影响。只有显式传 `--rule-ids` 才执行子集。当前 16 项走 Agent，其他项走程序。

深审保留同一会话三轮：分析 → 自检 → 落卡。传输使用 `codex exec --json`，后续轮次按明确 session ID 调用 `codex exec resume`；每个对象拥有独立会话。CLI 使用 workspace-write 沙箱和非交互执行。

NAS 映射目录中的任务先复制事实材料到本地临时工作区运行，再由程序只收回任务声明的输出文件；密封事实的临时副本改动不会回写。`SCHEMATIC_CODEX_REASONING_EFFORT` 可显式设置推理强度。已通过本地输出白名单与事实隔离测试。

`--enrich-context` 可选启用 Codex 补充上下文；旧名 `--use-sdk-context` 仅保留为兼容别名。正常上下文重建使用确定性事实种子，不需要模型补全。

断点续跑拒绝复用：缺卡、坏 JSON、契约无效、失败标记、离线演练卡、中断占位卡、缺哈希或哈希不匹配。程序项照常重算。正式卡不得手改来通过门禁。

## 验证

先完成上述依赖安装和前端构建，再执行；`verify_standalone.py` 需要本地 `report_ui/dist`，不会替你构建前端。

注意：当前 `--dry-run` 不是全链路离线保证。`verify_standalone.py` 的报告汇总阶段在缺少分析文案缓存时仍可能调用真实 Codex，使用网络及模型额度；仅需本地代码检查时，运行单元测试和前端构建即可。

```powershell
py -m unittest discover -s tests -v
# 完整流程验证：可能调用真实 Codex，先确认鉴权和额度
py scripts/verify_standalone.py
# 真实 Codex 三轮文件交付测试（临时目录，不触及正式板卡）
# 使用已配置的 Node 运行 scripts/codex_smoke.mjs
```

### 2026-10-20 迁移验证记录（历史）

以下是迁移时本机的验证记录，不代表新 clone 已安装依赖、已登录或已完成评审；本地结果与历史目录不会随代码完整推送，需在目标环境重新验证。

当时已验证 Codex 登录、真实响应、同会话三轮和 JSON 文件交付，并通过新执行逻辑测试及独立运行验证。`migration_baseline/codex_smoke.json` 保存当时的真实三轮测试结果。

当时 0918 板材料重建记录：94 个器件、70 个网络、44 份规格书命中、原理图位号覆盖 94/94；来源路径可访问，密封上下文 184/184。旧知识图、索引、上下文和门禁快照保存在当时的本地 `boards/0918全量/history/`。

该次验证没有启动全量对象深审。`automation_preflight.json` 仅表示准备状态，不代表整板评审完成；最终仍以目标环境中正常全量流程生成的 `agent_review_gate.json` 为准。部分料号参数可能仍需补证；查不到时保持缺口，不以猜值通过。

`sdk_*.json`、`use_sdk` 等少量内部字段名为了兼容旧报告与脚本暂时保留，实际执行后端均为 Codex CLI。历史迁移资料见 `docs/迁移到单仓.md`，归档旧文档见 `archive/`。

官方接口说明：https://developers.openai.com/codex/noninteractive

## 现行判定与展示约定

材料参数由 `agent_review/material_params.py` 统一读取：精确匹配料号，合并板级缓存、兼容缓存和 BoardKG 补充参数；冲突显式保留，不再按料号前缀猜配。工作台同时保留额定字段与建模参数、测试条件和来源，制造商适用性仍需核对。参数表或种子版本变化会使密封缓存失效。

功耗卡以 `decision_basis` 区分可达反例、保证下界、包络上界和证据不足。全局最大值未知时保持 `P_max=null`，不妨碍独立充分超限证据判否；通过需要完整包络。分析及自检须将关键节点上的全部引脚与平衡方程对账，检查受控通道的负载，模型结果不能冒充保证上下界。

通路图使用 CAD 脚位与 PDF 铜线配准，多分支分别绘制，器件内部用虚线，跨页同名网逻辑续接。无法配准或不连通的部分明确圈注，不用直线补接；候选工况不会显示成 PASS。出图索引的渲染成功与 `routing_complete` 分开记录，不能将成功生成 PNG 当成电路闭合证据。重评后仍经正常聚合更新原来的 `review_out/expert_review.html`。

P11-A01 的主交付必须包括最坏通路搜索及比较，不能因找到超限反例就结束。`worst_case` 分别索引已确认最坏和条件最坏；每个状态有比较理由，尚可能改变排名的边界单列。已能计算但驱动/负载/持续条件待确认的状态保留 `status=conditional`、模型功耗及成立条件，不能吞入 `unmapped` 丢失数值，也不能作为已确认反例或填入全局 P_max。报告优先展示两类最坏，条件最坏明确标识。每张图只高亮该状态导通的电流路径，电压标注及计算框由该状态的数据生成；截止器件仅圈注，互斥状态分别绘制。

执行中断时，仅将已经写出的分析及其工作台保存到板内 `history/review-checkpoints/`，不会发布中断的判定卡。可用 `run_p11a01_objects --critique --analysis-checkpoint <检查点根目录>` 继续自检和落卡；恢复前核对当前工作台，变化则拒绝复用。`AGENT_P11_TURN_TIMEOUT_MS` 可延长复杂分析的单轮时限。

若自检修订已写盘而执行未正常结束，可加 `--resume-card` 只续接汇总卡生成；仍执行卡契约校验和正常聚合。失败的 scoped Judge 不覆盖已发布页面。页面默认展开最坏候选，其余对照保持可展开，便于复核通路比较。

业务展示约定更新：主区域只显示一个决定结论的极值工况（按规则选最坏或最好）及选择依据；其他工况仅作文字比较，不生成或展示其图。端压使用“V(对象) 两端电压差”，脚号留在内部路径和证据中；计算前提摘要仅保留关键输入，完整材料默认折叠。条件工况用“模型超限/模型内满足限额”，与对象正式判定分开。箭头只使用声明路径支持的铜线方向，中间同名网按引脚衔接，方向冲突的线只高亮不画箭头，几何补线不自动推断电流方向。

功耗判定契约 v2：`agent_review/decision_contract.py` 与共用 `_power_decision.md` 区分正常设计工程模型、未证实的条件候选和最大值/精度缺口。材料支持的正常设计核算超限可用 `normal_design_model` 判否，须记录适用性与覆盖论证，不称作全温实物保证。所有新 P11-A01 卡必须有 `decision_audit.version=2`、逐状态准入审查、正常设计证据搜索及缺口影响分类。待确认必须有材料支持且可能改变结论的反向情形；正常设计证据已超限而仍待确认会被校验拒绝。

分析、自检和落卡三轮均由编排器注入同一通用判定契约；规则不含特定位号、板名、阈值答案。控制器将判定代码及相关提示词的指纹附到成功卡；断点复用须指纹与材料哈希同时一致。契约失败或中断未产卡属于执行失败，不编造待确认卡；无效/失败侧标记的卡不会进入聚合。修复业务反馈须同时核对流水线、回归验证和正常报告再生，禁止只改生成物。


## review.v1 单对象试点

使用 `run_p11a01_objects --objects R4 --contract-v1` 显式启用 R4，范围由板内 review_contracts.json 保存，禁止将该配置自动扩展给其他对象。

新卡保存原生 schema_version=review.v1。不可变 target、criterion、材料额定和阻值由工作台注入，模型只写契约核心字段；控制器附 `_meta`，记录密封材料、引用证据、分析与契约指纹。读写新卡不经过旧四格修补。

三轮提示词为 review_v1.common.md + P11-A01.analysis.md / review_v1.self_check.md + review_v1.power.md，最终按工作台旁的 schema.json 落卡。隔离目录仅复制材料、上下文、工作台、Schema 和当前运行分析；不复制 docs 样例、历史评审卡或报告。

统一转换输出 review_detail 给 HTML/Vue，display 显式选择核心参数/前提，extreme 显式选择唯一主图。原图几何未通过或图片源哈希不匹配则拒绝报告发布，不用圈注 fallback 掩盖连线失败。结构/一致性错误仍是执行失败，不能改成业务待确认。
