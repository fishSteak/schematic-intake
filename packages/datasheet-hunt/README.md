# datasheet-hunt · 料号找规格书 PDF（可复用包）

> 来源：原理图自动化评审（schematic-intake）材料层 Step2  
> 用途：已知 **MPN / 料号**，联网检索并下载 **规格书 PDF**（含可用链接），供其他团队审阅/复用。  
> 版本：1.0.0

---

## 1. 我们在自动化评审里怎么做的

材料流水线入口（板级）：

```bat
python -m kernel.run --board boards\<板> --from-step 2
```

其中 **Step2 = 规格书检索 + 落盘**，逻辑摘要：

| 步骤 | 做什么 |
|------|--------|
| 建队列 | 从 BOM/网表抽出唯一 MPN，优先 IC / 二极管 / 晶体管 / 电感 / 电阻功率 / 电容耐压 |
| url_hints 预取 | 若已有直链（手工、`datasheet_url_hints.json`、立创 pdfUrl、系列知识库）→ 直接 curl 下 PDF |
| 联网搜索 | **AnySearch** 查询 `{MPN} datasheet filetype:pdf`，解析结果里的 PDF / datasheet URL |
| 启发式 CDN | 对 TI / ST / onsemi 等料号族拼厂家 CDN 模板 URL（无搜索时的兜底） |
| 可选 DDG | DuckDuckGo HTML 搜 PDF（网络差时易熔断，评审主路径默认 `--no-ddg`） |
| 校验落盘 | 校验文件头 `%PDF`、过滤 LCSC stub 页；写入 `datasheets/*.pdf` + `library.json` |
| 镜像缓存 | 评审侧再镜像到 `cache/mpn/<safe_mpn>/datasheet.pdf` 供后续抽参 |

搜索优先级（单颗料）：

```
url_hints 直链 → AnySearch URL 列表 → 厂家 CDN 启发式 → (可选) DuckDuckGo
```

韧性设计（不阻断整板评审）：

- 每 URL / 每 MPN / 全局 wall-clock 预算  
- 主机级熔断（403/429/超时）  
- 商品阻容默认跳过（可用 `--fetch-passives` 或补 hints）  
- 失败写入 `deferred.json`，可 `--compensate` 重试  

**本包只拆「找 PDF / 链接」**，不含规格书抽参（额定功率、耐压 OCR/Agent 等，那是评审后半段）。

---

## 2. 给其他团队：最小复用方式

### 2.1 目录

```
datasheet-hunt/
  README.md / setup.bat / run_example.bat
  requirements.txt          ← 指向内置 AnySearch
  examples/
  datasheet_hunt/           ← 找 PDF 引擎 + AnySearch 桥接
  vendor/anysearch/         ← 【已内置】AnySearch skill（Apache-2.0）
```

### 2.2 一次性安装（自闭环）

```bat
cd datasheet-hunt
setup.bat
REM 编辑 vendor\anysearch\.env ，填入 ANYSEARCH_API_KEY=...
REM （无 Key 也可匿名搜索，额度较低；注册见 vendor\anysearch\README_zh.md）
```

或手动：

```bat
pip install -r requirements.txt
copy vendor\anysearch\.env.example vendor\anysearch\.env
```

### 2.3 运行（无需评审仓库、无需再装外置 AnySearch）

```bat
cd datasheet-hunt
set PYTHONPATH=%CD%

REM 单个料号（会走内置 AnySearch）
python -m datasheet_hunt --out-dir .\out_demo --mpn LM393DR

REM 批量
python -m datasheet_hunt --out-dir .\out_demo --mpn-file examples\mpns.txt --fetch-passives

REM 已有直链时更快更稳
python -m datasheet_hunt --out-dir .\out_demo --mpn-file examples\mpns.txt --url-hints examples\url_hints.json
```

Linux / macOS：`export PYTHONPATH=$PWD`，系统需有 `curl`；`.env` 同样放在 `vendor/anysearch/.env`。

### 2.3 输出契约（审阅重点）

`--out-dir` 下：

| 路径 | 含义 |
|------|------|
| `datasheets/<MPN>.pdf` | 下载成功的 PDF |
| `datasheets/library.json` | **主结果表**：每颗料的 `download_ok` / `source_url` / `search_backend` / `local_path` |
| `datasheets/search_log.json` | AnySearch 查询与候选 URL |
| `datasheets/deferred.json` | 未拿到 PDF 的料 + 原因（可补偿） |
| `datasheets/url_hints.json` | 本 run 使用的直链表（若提供） |

`library.json` 单条示例字段：

```json
{
  "mpn": "LM393DR",
  "download_ok": true,
  "local_path": "datasheets/LM393DR.pdf",
  "source_url": "https://www.ti.com/lit/ds/symlink/lm393.pdf",
  "search_backend": "anysearch",
  "anysearch_query": "LM393DR datasheet filetype:pdf"
}
```

同事只要料号、没有 PDF 时：重点看 **`source_url`（链接）+ `local_path`（文件）**。

---

## 3. 内置 AnySearch（联网搜索）

本压缩包已 vendoring **AnySearch** 到 `vendor/anysearch/`（许可证见该目录 `LICENSE`，Apache-2.0）。  
桥接默认优先用包内 CLI，**不再要求**对方机器先装 `~/.agents/skills/anysearch`。

| 项 | 说明 |
|----|------|
| CLI | `vendor/anysearch/scripts/anysearch_cli.py` |
| 依赖 | `pip install -r requirements.txt`（实质为 `requests`） |
| 密钥 | 复制 `.env.example` → `.env`，填 `ANYSEARCH_API_KEY`（推荐） |
| 覆盖 | 可选环境变量 `ANYSEARCH_CLI=...` 指向外部安装 |

自检：

```bat
set PYTHONPATH=%CD%
python -c "from datasheet_hunt import anysearch_available; from datasheet_hunt.anysearch_bridge import resolve_anysearch_cmd; print(anysearch_available(), resolve_anysearch_cmd())"
```

`--no-anysearch` 时退化为 **url_hints + 厂家 CDN +（可选）DDG**，命中率会下降。

---

## 4. 与评审主仓的关系（供对照）

| 本包 | 评审主仓 |
|------|----------|
| `datasheet_hunt/_fetch_core.py` | `vendor/datasheet-fetch/search_and_fetch_datasheets.py` |
| `datasheet_hunt/anysearch_bridge.py` | `vendor/datasheet-fetch/anysearch_bridge.py` |
| CLI `--mpn` / `--mpn-file` | Step2：`kernel/steps/s2_datasheet.py` 从板图 BOM 建队列后调用同上脚本 |

抽参、Agent 查参兜底（`mpn_param` / `rated_hunt`）**不在本包**。

---

## 5. 能力边界（复用前请看）

- **输入**：料号字符串（或可选已知 PDF URL）。不是「只知道阻值/封装、没有料号」的语义搜索。  
- **商品阻容**：默认 skip（公开独立 PDF 少）；需要时 `--fetch-passives` 或补 `url_hints`。  
- **版权 / 站点策略**：下载的是公开链路上的 PDF；部分站点反爬会导致 miss，请用 hints 或厂家站。  
- **不保证 100% 命中**：网络、料号冷门、代理页都会 miss；看 `deferred.json`。  
- **Windows**：下载优先 `curl.exe`；非 Windows 用 `curl`。

---

## 6. 建议对方团队验收清单

1. `python -m datasheet_hunt --out-dir out --mpn LM393DR` → `library.json` 中 `download_ok=true`，且有 `source_url`  
2. 关掉 AnySearch（`--no-anysearch --no-ddg`）+ 仅 `url_hints` → 仍能下到 PDF（验证直链路径）  
3. 批量 `examples/mpns.txt` → 看 ok/miss 比例是否可接受  
4. 将 `library.json` 接到己方物料库（存 URL + 本地路径）

联系 / 问题回流：原理图评审（schematic-intake）材料层维护方。
