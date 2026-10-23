# 原理图自动化评审

本仓是原理图自动化评审的唯一开发入口。`feat/web-review-workbench` 提供 **本机网页 + Python 评审编排 + 直接 LLM API**。应用负责材料、上下文、工具调用、结构化校验和报告发布，不依赖 Codex CLI、登录态或配置文件。

## 初始化与启动

需要 Python 3.12、Node.js >=24 和 npm。Node 用于 EPRJ2 解析与前端构建；可用 `SCHEMATIC_NODE` 指定 Node 可执行文件。

Linux / macOS，在仓库根目录执行：

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
npm --prefix report_ui ci --ignore-scripts
npm --prefix report_ui run build
.venv/bin/python -m web_workbench --port 8765
```

Windows PowerShell：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
npm --prefix report_ui ci --ignore-scripts
npm --prefix report_ui run build
.\.venv\Scripts\python.exe -m web_workbench --port 8765
```

打开 `http://127.0.0.1:8765`。服务仅监听本机；前后端同源。工作台数据默认保存在 `.workbench/`，可通过 `--data-dir` 或 `SCHEMATIC_WORKBENCH_HOME` 设置。运行进程独立于浏览器，刷新或关闭页面不会停止评审。停止任务使用页面按钮。

`report_ui/dist` 不进入 Git，新 clone 后需要构建。开发前端可运行 `npm --prefix report_ui run dev`，Vite 的 `/api` 和 `/reports` 代理指向本机 8765 端口。

## 使用工作台

1. 选择仓库已有板卡，或新建项目并导入原理图 PDF、BOM、TEL 网表、EPRJ2、工况与设计任务书。替换原项目的材料会创建工作台副本。
2. 执行材料预检，在“运行评审”选择规则、对象和执行模式。
3. 查看任务事件和结果；报告通过校验后发布为独立快照。失败或停止不会覆盖上一版报告。
4. 在三栏报告中筛选问题、定位原理图、填写复核意见、比较历史版本或下载报告。复核意见与机器结论分别保存。
5. 配置模型后，可围绕当前对象追问，并设置单对象复核。问答引用会核对报告原文，不修改正式结论。

| 模式 | 行为 |
|---|---|
| 材料检查 | 解析本地材料、准备事实与原理图索引，不调用 LLM |
| 程序检查 | 执行选定程序规则，发布程序结果；模型分析明确标为未生成 |
| AI 深审 | 按选定规则直接调用配置的 LLM，完成查证、分析、自检、结构化交付和校验 |

默认规则范围来自 `rules/product_center_15.json`：当前 30 项，其中 16 项需要模型评审、14 项为程序检查。工程结论“不通过”与任务执行“失败”是不同状态。

## 模型设置

在网页“模型设置”中填写 API 地址、模型名和凭据，保存后执行连接测试。首版使用 OpenAI-compatible Chat Completions 协议，支持对应兼容服务和本机模型 API。不同服务的工具、图片、JSON Schema 与流式能力需要按实际情况配置；连接测试只报告已实测项目，图片能力不会冒充已验证。

也可为 Python 命令行入口配置：

```bash
export SCHEMATIC_LLM_BASE_URL='http://127.0.0.1:11434/v1'
export SCHEMATIC_LLM_MODEL='填写实际已部署模型名'
# 服务需要密钥时，在本机设置 SCHEMATIC_LLM_API_KEY。
```

或将 `SCHEMATIC_LLM_PROFILE` 指向私有 JSON 配置。字段包括 `provider`、`base_url`、`model`、`api_key_env`、`timeout`、`max_tokens`、`max_context_tokens`、`max_turns`、`max_requests`、`supports_tools`、`supports_images`、`structured_output`、`stream`。可以用 `profiles` 和 `tasks` 分别指定 `review`、`evidence`、`prose`、`remediation` 等任务。`max_context_tokens` 可留空；配置后应用按保守文本/图片估算检查每个请求预算，不代表探测了服务商的最大窗口。`max_requests` 限制单个模型子任务（如一个对象的多阶段评审），整次板级运行可能包含多个子任务。不要将密钥提交到仓库；网页配置保存于本机私有文件，API 返回脱敏信息，报告不包含配置文件。

模型通过应用提供的材料读取、PDF 页面、网表查询、算术、直流求解和公开来源查找工具取得证据。模型不执行 shell 或直接写正式产物。引用、结构、工程契约和报告门禁失败时保留错误记录，不发布未经校验的结果。未配置服务时 AI 功能明确报错，没有 CLI 回退。

## 命令行与续跑

将下面的 `python` 替换为项目虚拟环境中的 Python：

```bash
python -m agent_review.run --board boards/0918全量 --resume
python -m agent_review.run --board boards/0918全量 --rule-ids P05-A01 --dry-run
python -m agent_review.serve --board boards/0918全量
```

`--enrich-context`（旧别名 `--use-sdk-context`）使用 LLM 补充上下文；通常的确定性事实重建不需要它。`--dry-run` 不生成模型分析文案，但通用准备阶段仍可能联网查参；需要无模型的工作流时使用网页“材料检查 / 程序检查”。

网页续跑仅用于失败、停止或中断的任务；材料、规则、对象范围和模型策略须一致。应用按源文件哈希、提示词、工具实现和模型配置校验检查点，拒绝复用无效或过期结果。程序项重算；已完成报告不可变。停止会阻止后续派发和迟到发布，远端已经发送的请求是否继续计费取决于供应商。

## 验证

```bash
.venv/bin/python -m unittest discover -s tests -v
node --test tests/*.test.mjs
npm --prefix report_ui run build
.venv/bin/python scripts/verify_standalone.py
.venv/bin/python scripts/verify_workbench_lifecycle.py
```

`verify_standalone.py` 使用隔离目录与固定 P05-A01 程序规则，核对求解器和程序检查迁移基线，并验证报告 HTTP 读取。`verify_workbench_lifecycle.py` 用独立临时数据目录验证真实服务重启、worker 中断与续跑，保留正式工作台数据。LLM 协议测试使用模拟 HTTP，不能替代真实模型深审验收。

本分支已验证本机程序检查链路：0918 板 P05-A01 的 51 个对象、报告门禁、网页 PDF 联动、复核保存、历史比较和导出。真实服务的对象深审与问答验收仍需要配置可用模型，不能用协议测试或程序结果替代。

## 仓库内容

- `kernel/`：材料解析与程序检查；`solve_prog/`：求解模块；`agent_review/llm/`：直接模型运行时。
- `web_workbench/`：FastAPI、SQLite、后台任务和报告快照；`report_ui/`：Vue 工作台。
- `.workbench/`、`.venv/`、`node_modules/`、前端构建、生成报告与大部分运行缓存均已忽略。
- `materials/`、板卡配置、固定知识图与 `migration_baseline/` 保留，用于可复现验证。禁止手改正式判定卡、上下文或 verdicts 来通过门禁。
- `sdk_*` 产物字段与部分旧命名保留读取兼容；Python 活跃入口已使用 LLM API。历史 `.mjs` Codex worker 和 `scripts/codex_smoke.mjs` 仅留作迁移记录，不属于工作台执行链路。

竞品、方案与完整验收范围见[网页交互竞品调研与方案](docs/网页交互竞品调研与方案.md)。历史迁移见 `docs/迁移到单仓.md` 与 `archive/`。
