# D9准确厂家资料核查（2026-10-22）

## 结果

已找到并归档**ANBON官方、无Q1后缀、明确覆盖MMBD4148CC**的准确家族PDF。第3页CC专属符号为共阴极，两个分支阳极A/B汇至公共阴极C，标记 **D5/KD5**。第1页封装图及第3页简图均用A/B/C字母标脚，并未直接印1/2/3；C为SOT-23单独一侧的公共脚，按本图SOT-23封装编号对应pin3。A/B为pin1/2且两阳极对称交换不影响功能。因此D9.3应为公共阴极、D9.1/D9.2为阳极，与原始网表及原理图CC连接一致。

本次消除D9“没有准确厂家CC拓扑依据”的缺口；没有宣称实物安装、PCB焊盘映射已检验。

## 原始料号与连接

项目根`K:/project/schematic-intake/schematic-intake/`。

- `boards/0918全量/material_inputs/bom.csv` D9：MMBD4148CC，AnBon(安邦)，LCSC **C397640**。
- `netlist.tel`：D9.1=N_DS_FEED（D8.3/R46.2），D9.2=GND，D9.3=SPDL（CN1.1/D8.2/Q4.3/R1.1）。
- `material_inputs/schematic_pdf.pdf` PDF第9页P08_SENSOR：D9与D8分别位于传感器供电分流/保护支路。

准确方向：D9由N_DS_FEED阳极→SPDL公共阴极是向SPDL分流的第二条正向支路，与D8公共阳极N_DS_FEED→其阴极SPDL方向相同；另一支路由GND阳极→SPDL公共阴极，在SPDL低于地约一正向压降时提供负向钳位。不能把共阴极误读为SPDL向N_DS_FEED正向导通，也不能忽略D9.1→D9.3与D8并联后可能分担的电流。

## 官方来源与哈希

准确产品页（官方）：
https://www.anbonsemi.com/product_detail/jp/MMBD4148CC

该页数据手册链接（已实际下载，非猜测文件名）：
https://www.anbonsemi.com/web_files/jp/?func=datasheet&tb=product&file_id=138239&fs=MMBD4148CC.pdf

归档：`docs/independent_reviews/2026-10-22/sources/ANBON-MMBD4148CC-official.pdf`

SHA256：`ABDED07ABB67E976684554E1272C74A18F7022B08D40489CAE934593F70DA3EC`

PDF共3页，ANBONSEMI标志及www.anbonsemi.com，题头MMBD4148/CA/CC/SE；Document ID **AS-3120029**，Issued 2003/03/08，Revised **2025/04/08**，Revision **E1**。第3页明确CC和D5/KD5，不依赖相邻CA/SE的符号推断。

已存可视证据`ANBON-official-page1.png`、`ANBON-official-page3.png`于同sources目录。PDF主体为图像，纯文本抽取只得到页眉页脚；本结论经过图像核查。

## 准确额定及适用条件

第1页Maximum ratings（Ta=25℃除另注）：VRM100V，重复/工作峰值及直流阻断电压75V；IFM300mA、Io150mA、8.3ms非重复浪涌2A、PD350mW、RθJA357℃/W、Tj/Tstg−55～150℃。Note1要求电极保持环境温度，不能脱离安装条件承诺350mW全温可耗散。

同页Electrical characteristics：VF最大0.715V@1mA、0.855V@10mA、1.000V@50mA、1.250V@150mA，均为Ta25℃条件。这比仅有10mA点的别家手册更直接支持约50mA支路的正常标称计算；但不能无条件外推全温、任意并联均流或短路故障保证。

## 本次发现的来源陷阱

LCSC精确BOM商品入口 https://www.lcsc.com/datasheet/C397640.pdf 的页面确实标“AnBon MMBD4148CC”，但其当前iframe返回：
https://datasheet.lcsc.com/datasheet/pdf/eecf18dde5f91bad7fe95d72b291e3af.pdf?productCode=C397640

实际PDF品牌却是 **Formosa MS**，文件FM-3120029，2012/05/16 RevD。不能因为商品码对应就当准确ANBON厂家文件。保留反证归档：
`docs/independent_reviews/2026-10-22/sources/LCSC-C397640-returned-FORMOSA-mismatch.pdf`

SHA256：`9F0B24E95DDE3657ABC15625CF6BBE4247E7F2C83CC5BF6792087E46B97E3C26`

页面HTML保留为`sources/D9-lcsc-viewer.html`，能审计iframe URL。原正式缓存`boards/0918全量/cache/mpn/MMBD4148CC/datasheet.pdf` SHA256为`272220B87BB05286F87119647BE905C3F3A3D122C6CC7A47E77A68801F2B7BBB`，此前检查其为onsemi，同样不能替代准确ANBON证据；本次未更改正式缓存。

搜索还返回旧`/file/PDF/MMBD4148CC-Q1.pdf`官方链接，但实际请求404，且Q1不是BOM后缀；没有用搜索摘要当已取得PDF。本次最终证据来自当前准确普通CC产品页的有效下载链接。

## 后续模型应如何使用

R4工况关系图可将D8的N_DS_FEED→SPDL支路与D9的N_DS_FEED→SPDL支路都画为正向分流，D9下侧GND→SPDL支路画为负向保护。若求每颗二极管功耗，需考虑两正向支路并联的实际分流；若求R4～R46电阻链总电流，只需总N_DS_FEED节点KCL，不必预设均流。准确原理图第9页与本官方PDF第3页共同作为方向证据，第1页作50mA正向压降及额定证据。
