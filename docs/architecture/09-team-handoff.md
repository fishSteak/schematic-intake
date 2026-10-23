# 多人开发与交接

## 解压与首次启动

工作目录只需要 `schematic-intake/`：boards/0918全量/materials.json 的原始材料路径指向仓库内的 `materials/0918全量/`。较早的交接包把同一批材料放在同级的 `0918全量材料/`，内容一致，不再需要。旧产物中的绝对路径和旧会话ID是历史追溯信息，不代表可在新机器直接续跑。

进入 schematic-intake 后先阅读 AGENTS.md、本目录 README.md 与全局架构。Python依赖在 requirements.txt，Node最低版本在 package.json。每位开发者独立安装和登录 Codex CLI；交接包不携带个人鉴权或本机依赖。

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
# Node安装完成后，在仓库根安装根依赖
npm ci
cd report_ui
npm ci
npm run build
cd ..
codex login status
.\.venv\Scripts\python.exe -m agent_review.serve --board boards/0918全量 --host 127.0.0.1 --port 8765 --no-browser
```

浏览器访问 http://127.0.0.1:8765/expert_review.html 。这是查看已有报告，不会重新评审。新机器执行评审前按运行文档做预检。requirements.txt 已锁定全部直接与间接依赖的精确版本（Python 3.12.4、Windows x64 上验证）；venv 宜放在较短路径下，避免 Windows 路径长度限制导致安装失败。交接包仍不是完全可复现的运行环境镜像：Node、Codex CLI 版本和模型配置需各自记录。

## 分工边界

| 负责范围 | 目录 | 协作要求 |
|---|---|---|
| 材料、事实和程序检查 | kernel、solve_prog、rules | 区分材料事实与推导，变更输入需检查下游失效 |
| Agent与判定契约 | agent_review、相关JSON Schema和提示词 | 不写样例答案；契约、校验与执行签名同步调整 |
| 图与证据 | agent_review中的figure、route、render模块 | 主分析、计算与图引用一致，保留定位和方向告警 |
| 报告 | report_ui、vendor/expert_html、detail_view | HTML与Vue共用展示内容，不能只修发布HTML |
| 交付和验证 | scripts、tests、docs/architecture | 通过正常流水线发布，维护证据与运行记录 |

## 开发、合并与发布

1. 使用团队Git远端，每个变更使用独立分支和独立本地工作副本；本包不含Git历史，若尚无远端，由负责人建立基线后统一克隆。
2. 不多人同时写共享板目录。开发者使用自己的板副本和输出目录，避免覆盖上下文、断点、图片和报告。共享正式板由指定发布人写入。
3. 提交中说明变更层级、输入输出、兼容与断点失效策略。跨模块字段先确认契约再合并，不能靠前端猜测补齐语义。
4. 源码、规则、契约、提示词和测试纳入版本管理；大体积原始材料、规格书和运行快照使用团队约定的受控存储，保留版本与哈希关联。不要直接将本包全部二进制文件提交Git。
5. 按改动范围验证。迁移模块运行 verify_standalone；运行逻辑按 AGENTS.md 执行相关测试；正式发布检查汇总、详情、图及PDF入口。
6. 报告发布前保留上一份可用产物，由单一发布入口执行。执行成功、契约合规和电子证明充分是不同状态，不能互相替代。

## 包内容与边界

`core-report`为核心开发与当前报告包：在core基础上加回当前HTML、报告JSON、Vue构建、工况图、规格书、原理图、PDF.js及当前评审证据；不包含历史执行快照、context_store和运行缓存。解压后可使用本节上方的serve命令查看已有报告，无需重新评审或构建前端。当前报告的待确认及未完成状态原样保留，不代表新一轮复核，也不保证旧任务能跨机器续跑。

交接包分为full与core两种。core为核心开发包：保留代码、规则、提示词、测试、文本资料、架构文档、板级JSON配置和原始材料；移除运行输出、历史快照、缓存、日志、生成图片、重复输入副本和可重建的前端dist。core包不能直接打开旧报告；安装依赖、构建前端后，按运行文档从prep开始生成板级产物，之后再启动服务。需要查看旧报告或原始评审证据时使用full包。下文有关已有报告和完整历史保留的描述适用于full包。

源码、配置、规则、提示词、测试、架构文档、历史文档、板级原始输入、事实缓存、规格书、评审卡、研究材料、图和已有报告均保留。历史与当前状态并存，待确认和未完成的证明不会因为打包而变为完成。

排除 node_modules、Python环境与字节码、Git内部数据、个人.codex目录、明确鉴权文件及临时草稿。具体排除清单、逐文件SHA-256和打包过程中变化记录见压缩包根目录的 PACKAGE_MANIFEST.json。前端现有dist保留方便查看，同时保留源代码和锁文件用于重建。

该包是按文件读取的交接快照，不是运行数据库的原子备份。若存在写入中的评审，清单会记录打包时检测到的文件变化；新机器应根据签名预检决定复用或重跑，不能凭旧完成标记跳过校验。

## 维护入口

架构或职责变更同步更新本目录；打包工具为 scripts/package_handoff.py。执行时传入新的、尚不存在的zip路径；工具会附带清单、排除记录、材料路径检查结果和完整性校验。

```powershell
py scripts/package_handoff.py --profile core --output ../handoff/schematic-intake-core.zip
```
