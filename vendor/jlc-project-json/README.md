# 嘉立创工程转JSON：源码参考包

将已支持的嘉立创专业版 `.eprj2` 工程只读解析为页面、符号、实际器件、位置、引脚及连接关系。供其他项目参考和集成，不含85项规则引擎、AI调用、网页服务、数据库、原始工程或账号配置。

## 支持边界（请先读）

- 已验证编辑器版本：`3.2.149.88089769`；主分支 `main`，一条无父历史、无快照标记的完整历史记录。
- `.eprj2` 为SQLite容器。本实现读取工程历史表，以文件中记录的键和认证信息解码，校验AES-GCM认证、gzip、UTF-8、页面集合及标题。不是官方SDK，不是所有版本通用转换器。
- 文件和解压历史各限制64 MiB；暂不支持增量历史链、多分支选择及任意新版结构。遇到不支持情况拒绝，不能删掉版本/认证检查来强行成功。
- 连接分析以当前已实现的单原理图作用域、几何规则为限；不支持或有歧义的内容列为缺口。不能据解析成功宣称ERC/DRC通过。
- 输出是用于分析的JSON，不是完整无损工程序列化；不支持编辑JSON后回写嘉立创工程。原生坐标不是PDF像素坐标。
- 本包保留原有位号/多单元合并逻辑；`instances`按位号分组，非法重复位号仍须结合`symbols`中的设备及单元身份检查，不能把分组数量本身当唯一性证明。
- 不含PCB布局、规格书参数、工作电压/电流推导或BOM/TEL一致性检查；这些属于上层应用。

## 快速运行

需要Node.js 24或以上及npm；本包实测Node.js 24.18.0。唯一第三方运行依赖为 `adm-zip@0.5.16`，版本由锁文件固定。

```bash
npm ci --ignore-scripts
node cli.mjs "/path/to/project.eprj2" "./project.json"
```

输出文件不得已存在，避免误覆盖。无需嘉立创正在运行，无需启动8980，也不调用AI或联网服务；首次安装依赖通常需要npm仓库访问。

退出码：`0`完成且无已识别解析缺口；`2`已输出JSON但存在覆盖/连接问题；`1`解析、版本、认证或文件错误。即使为0，也只代表解析器当前检查范围，不是设计审查通过。JSON可能包含项目名称、设计者字段和电路设计信息，分享时按项目资料管理要求处理。

## 在代码中调用

```javascript
import {convertProject} from './src/index.mjs';
const data = convertProject('/path/to/project.eprj2');
console.log(data.summary); // pageCount / symbolCount / physicalCount
if (!data.complete) console.log(data.coverage, data.issues, data.connectionIssues);
```

可传 `{expectedSha256: '预先确认的完整SHA256'}` 校验外部快照；不传则对当前文件计算指纹，并核对解析前后文件未变。自动算指纹用于一致性核对，不能证明文件来源可信。

低层 `readEprjProject(path, expectedSha256)` 同时返回原始文档和内部适配数据，主要用于调试。其 `adapterBytes` 只是内存中的解析适配ZIP，绝不能当嘉立创原生导出文件交付。推荐集成 `convertProject`。

## JSON字段

| 字段 | 含义 |
| --- | --- |
| source / parserVersion | 来源SHA256、工程/历史标识、编辑器及解析器版本 |
| summary | 页面、符号和按位号合并的实际器件数量 |
| complete / coverage / issues / connectionIssues | 覆盖结论、未归类对象和已识别解析问题 |
| pages / pageFields | 页面标识、名称、图框字段 |
| symbols | 每个符号单元的属性、位号、型号、原生位置及属性来源 |
| instances | 按位号分组的器件及其符号、位置、引脚与网络 |
| annotations | 图纸文字及可见属性标注 |
| connectivity | pinRecords、pinToNet、networks、ports及连接问题 |

与8980接口的关系：本包输出是独立解析结果，不是 `/review85/objects` 接口的完整响应。该接口还包含规格书、场景和评审业务信息，本包有意不携带这些内容。

## 文件导航

- `src/eprj-reader.mjs`：SQLite工程历史读取、认证解码、页面覆盖核验。
- `src/native-readonly-archive.mjs`：内部档案与EPRU记录解析。
- `src/schematic-model.mjs`：从原生记录提取符号、属性、型号和坐标。
- `src/schematic-connectivity.mjs`：引脚几何投影、网络及跨页连接。
- `src/page-fields.mjs`：图框字段读取。
- `src/index.mjs`：独立JSON接口及器件分组。
- `cli.mjs`：文件转换入口。
- `SOURCE-MAP.json`：相对原项目的来源路径、指纹和抽取说明。

解析核心直接复用现有源码，只抽离审查依赖；新加独立封装和命令行入口。未变更运行中的8980代码。源代码供用户指定的其他项目参考；未附加新的开源许可，不表示授予第三方源码额外权利。`adm-zip`按其自身MIT许可证使用，由npm安装，不随此包复制分发。

## 验证

```bash
npm test
JLC_TEST_PROJECT="/path/to/project.eprj2" npm test
```

不设置工程路径时真实工程测试会明确跳过；包内不携带用户工程。构包时另用现有真实工程完成解析结果等价性与命令行验证，见 `VERIFICATION.md`。
