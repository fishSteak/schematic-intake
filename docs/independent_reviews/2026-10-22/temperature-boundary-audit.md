# P12-A03温度边界独立核查（2026-10-22）

## 当前快照与判定重点

读取本轮`boards/0918全量/review_out/agent_jobs/P12-A03/`时，LED1/U1/U2/U5/U7/Q4/D10仅有context与research schema，尚无已完成正式json/analysis。因此本文件不把工作台抽取错误冒称最终卡错误；以下是准确原文与后续卡应避免的具体混淆。

**LED1明确不覆盖公共环境最高70℃。** 准确光华SLSB07BRYA2BD-7.3手册给工作温度−20～60℃，所以公共0～70℃中60～70℃段超过器件工作温度范围。不需要LED电流/自热才能证明这一环境等级不覆盖，不能用存储最高85℃改判通过。LED1已经被选为本轮P12-A03对象，不需要再停在“有源是否包括LED”的抽象适用性问题。

U2/U5/U7具有正常环境/操作温区证据；U1规格用Tj、Q4只明确最高Tj而低端常来自Tstg、D10准确先科同样只有最高Tj及存储范围。这些不能逐字改名成保证Ta范围。应把温度等级覆盖与实际自热热安全分开，不无条件把所有器件温区卡扩为整板热评审，也不能用Tj最大高于70℃便保证有功耗时Ta70℃一定安全。

## 需求来源已闭合

`boards/0918全量/material_inputs/operating_conditions.yaml`：confirmed_by_business=true，`defaults.ambient_celsius` min0/max70，作为本轮公共硬标准。`design_task_book.yaml` t_amb_c=null及DOCX§3.2旧“工作温度待定”不能把已确认公共标准抹成环境缺料。P12-A03原名称：有源器件温度等级覆盖项目最低/最高**环境温度**。本轮不自行增加户外、凝露、海拔或任意机内热点要求。

## 准确型号逐项边界

根路径`K:/project/schematic-intake/schematic-intake/`。

| 对象 | 准确原文定位 | 可以支持什么 | 不得混淆 |
|---|---|---|---|
| LED1 SUNLIGHT SLSB07BRYA2BD-7.3 | `boards/0918全量/cache/mpn/SLSB07BRYA2BD-7.3/datasheet.pdf`，文件1805018 RevA，2018-05-21；PDF第1页精确型号/厂家，第2页TOPR−20～+60℃、Tstg−25～+85℃ | **不覆盖**0～70℃，最高工作温度不足10℃ | 第2页为极限参数表仍明确写TOPR工作温度；Tstg85不是允许85℃工作。电气VF20mA/25℃也不是环境等级替代 |
| U1 ST L78L12ACZ-AP | `cache/mpn/L78L12ACZ-AP/datasheet.pdf` DS0424 Rev30，第4页TOP operating junction temperature：AC/C为0～125℃，AB为−40～125℃；第12页Table18 AC电特性适用Tj0～125℃ | 准确AC商业等级下限0℃与需求下限相符，结温上限125℃明确；可记“等级边界没有低温冲突，实际Ta70℃需满足Tj≤125℃” | 不能取AB的−40℃给AC；不能把Tj0～125改为Ta0～125；存储−65～150也不是工作等级 |
| U2 TI LM393DR | `cache/mpn/LM393DR/datasheet.pdf` SLCS005AH第4页推荐Ta0～70℃，第8页LM393非B非A全温电参数 | 正常环境等级覆盖0～70℃，端点相等也算覆盖 | 不能套LM393B的−40～85℃，也不把Tj150或存储范围当Ta |
| U5 TI CD4066BM96 | `cache/mpn/CD4066BM96/datasheet.pdf` SCHS051J第4页5.3推荐Ta−55～125℃；同页绝对Tj150、Tstg−65～150 | 推荐Ta明确覆盖0～70℃，可作温区等级通过 | 不用Tstg替Ta；同页供电/信号范围是其他物理量 |
| U7 TI LM392MX/NOPB | 准确`docs/independent_reviews/2026-10-22/sources/TI-LM392-N.pdf` SNOSBT5D，第3页Operating Temperature Range0～70℃、Storage−65～150℃；第3～4页温度规格脚注限0≤Ta≤70℃ | 准确型号正常温区覆盖，商业级边界相符 | 缓存`cache/mpn/LM392MX_NOPB/datasheet.pdf`是SLOS466 LM392D/DR族，不能因0～70数字相同就保留错误身份 |
| Q4 Hottech 2SC1623(L6) | `cache/mpn/2SC1623_L6_/datasheet.pdf`，Hottech/合科泰，第1页最高Tj150℃、Tstg−55～150℃、PC200mW、RθJA625℃/W；第2页PC对Ta降额典型/额定曲线 | 最高结温及温升约束可查；L6是hFE等级，不是温度等级后缀 | 第1页−55低端属于Tstg；本准确页没有单独给Tj最低或Ta工作区间。不可直接声称“工作温度−55～150已证实”。Tj最高150不能代替Ta150 |
| D10 ST(先科) 1N5241B | 准确`docs/independent_reviews/2026-10-22/sources/ST-1N5241B-C118660.pdf`第1页Tj200℃、Tstg−65～200℃、RθJA0.3K/mW且特定引线散热条件；第2页Characteristics at Ta25℃ | 最高结温与散热条件明确；25℃电参数有适用点 | 正式旧缓存为onsemi，不能用其“Operating Junction Temperature Range−65～200”替换准确先科只有最高Tj的写法；存储−65低端不等于先科工作低端。第2页TKVZ不能当未给区间的全温保证 |

## 工作台抽取的具体风险

当前context含全板共享MPN段，因此不能在全文搜到一个`t_op`就认为属于本对象。已看到D10工作台温区抽取引用onsemi第1页“Operating Junction Temperature Range−65～200”，准确BOM厂家是先科；应回查独立归档准确先科源，不应沿用该抽取。U7工作台MPN缓存为SLOS466族也同理。Q4若抽取`−55～150`，必须回查它是Storage栏，不能只保留数值。

LED1扫描手册主体表格必须视觉核对：它的工作上限60℃与存储上限85℃恰好分列，很容易漏取或替换。正确结果不是“缺厂家手册”，也不是“默认工业环境故应该耐70℃”；准确型号已经给出反例。

## 温区卡与自热边界的建议处理

U2/U5/U7若仅做器件温度等级覆盖，可按准确正常温区通过；不需为了这张卡重新证明全部功率。U1/Q4/D10如果手册只给Tj，不应直接伪造Ta表，但也不要仅因Ta字段没结构化就停止：先将准确Tj/温度等级证据与已知负载热模型关联，或者明确该型号工作低端/全温适用资料的技术缺口。其间不得要求业务重新提供已确认0～70℃。

自热核算应使用`Tj=Ta+P×RθJA`及准确安装条件；是否在P12-A03内完成取决于本卡既定契约，不能自行增加全部热证明为所有器件最终通过前提。对LED1不覆盖结论，任何自热只会进一步恶化，不会让TOPR60℃覆盖Ta70℃；因此不需要等待其他功率卡、详细结温或PCB散热才能判定等级不足。

真正未获厂家明确的工作低温/适用区间属于技术资料或选型证据缺口，不是环境需求缺料。可以登记“准确型号手册未给该范围”，不能写业务未确认温度，也不能用别家同名型号补保证。

## 图页建议

LED1原理图第11页P10_INDICATOR及准确光华PDF第1/2页；U1原理图第2页、手册第4/12页；U2第5页、手册第4/8页；U5第4页、手册第4页；U7第6/7页、准确LM392-N第3/4页；Q4与D10原理图第9页，准确厂家各第1页。建议温区对照图把Ta/Tj/Tstg分别标清，LED1用60℃工作上界对70℃需求画明确10℃缺口。
