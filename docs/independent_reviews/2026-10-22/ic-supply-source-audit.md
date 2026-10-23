# U1～U7 / P12-A01 IC供电源材料核查（2026-10-22）

## 条款与结果范围

`boards/0918全量/material_inputs/rules.json` P12-A01原条款：**“IC实际供电范围位于推荐工作范围内且不超过绝对最大值”**。它并未另写整板功耗、所有输出电流、全负载稳压保证等要求；但U1作为稳压器，不能用输入未过绝对最大代替输入足以稳压，也不能把网名+12V直接等同输出全工况已成立。

按confirmed公共标称24V/12V，**U1～U7均无明显供电脚接错或推荐供电电压越界**；U2/U4实际为VCC24V供电，不能全部写成12V。U1输入24V量级在准确12AC规格的14.5～27V电特性保证窗口内，并有充足标称压差；但输出11.4～12.6V保证仍附1～40mA及结温范围条件，整板全负载/启动保证本次未独立完成，不能声称已证实所有内部供电瞬时稳定。

## 供电脚与准确型号逐项

根`K:/project/schematic-intake/schematic-intake/`；节点均直接复核`boards/0918全量/material_inputs/netlist.tel`，型号来自同目录`bom.csv`。

| 对象 | 原始网表供电脚 | 推荐/正常供电 | 绝对最大 | 准确手册页码与结果 |
|---|---|---|---|---|
| U1 ST L78L12ACZ-AP，TO-92 | pin3 VIN=VCC24V；pin2 GND；pin1 VOUT=+12V | 12AC电特性窗口VIN14.5～27V，IO1～40mA，Tj0～125℃ | 12～15V输出族VIN35V；IO100mA绝对额定不等于全条件稳压保证 | `cache/mpn/L78L12ACZ-AP/datasheet.pdf` DS0424 Rev30，第3页TO-92**底视图**，第4页额定，第12页Table18准确12AB/AC参数。标称输入24V量级满足电压/压差窗口 |
| U2 TI LM393DR | pin8 VCC=VCC24V；pin4 GND | 非B/非V版2～30V | 供电36V | `cache/mpn/LM393DR/datasheet.pdf` SLCS005AH第3页脚位、第4页供电范围；第8页非B/非A版电参数。24V标称在范围内 |
| U3 TI LM339DR | pin3 VCC=+12V；pin12 GND | 非B/非V版2～30V | 36V | `cache/mpn/LM339DR/datasheet.pdf` SLCS006Z第4页脚位、第5页Non-B最大额定、第6页Non-B推荐范围。12V在范围内；不要用B版36V推荐范围 |
| U4 TI LM358BIDR | pin8 V+=VCC24V；pin4 V−=GND | LM358B版本单电源3～36V | 40V（即±20V总差40V） | `cache/mpn/LM358BIDR/datasheet.pdf` SLOS068AB第3页脚位、第4页额定、第5页推荐范围。24V在范围内；不能按老LM358非B参数替代 |
| U5 TI CD4066BM96 | pin14 VDD=+12V；pin7 VSS=GND | VDD−VSS 3～18V | 20V | `cache/mpn/CD4066BM96/datasheet.pdf` SCHS051J第3页脚位、第4页范围。12V在范围内；第4页注释出现24V并不能覆盖表中明确18V推荐上限 |
| U6 onsemi MC14584BDR2G | pin14 VDD=+12V；pin7 VSS=GND | 3～18V | −0.5～18V供电范围 | `cache/mpn/MC14584BDR2G/datasheet.pdf` MC14584B/D Rev10，第1页供电与额定、第2页脚位及DR2G订货号。12V在范围内 |
| U7 TI LM392MX/NOPB | pin8 V+=+12V；pin4 GND | 单电源3～32V | 32V或±16V总差32V | **准确LM392-N** `docs/independent_reviews/2026-10-22/sources/TI-LM392-N.pdf` SNOSBT5D，第1页供电、第2页脚位、第3页额定，第4页电参数。12V在范围内 |

U7型号对应官方页：https://www.ti.com/product/LM392-N/part-details/LM392MX/NOPB ；准确手册：https://www.ti.com/lit/ds/symlink/lm392-n.pdf 。原缓存`cache/mpn/LM392MX_NOPB/datasheet.pdf`实际为SLOS466“LM392”，订货号D/DR族，与MX/NOPB的LM392-N不同；两者名义范围相近也不能拿错误文件完成准确后缀审查。

本轮没有发现U1～U6缓存品牌与BOM相冲突；需注意同一PDF含多个版本，必须选准确AC/非B/B行。D10缓存厂家问题另已纠正，它不是本表U编号供电脚额定依据。

## 实际电源来源及不能跳过的条件

原理图`material_inputs/schematic_pdf.pdf`第2页P01_POWER：外部CN1/CN2 VCC同网，经F1到+24V，D2支路至VCC24V，U1再给+12V。任务书YAML将+24V、VCC24V均确认标称24V、+12V标称12V；这是正常核算基线，不是把D2压降/外部容差/稳压温漂取消。U2/U4挂在U1之前的VCC24V，其他四颗IC挂U1之后。

U1准确第12页Table18：25℃输出11.5～12.5V；VIN14.5～27V、IO1～40mA、适用Tj0～125℃时输出11.4～12.6V；在VIN19V、IO1～70mA另有相同输出范围。不能将VIN19V下70mA测试行无条件搬到实际24V输入，或把第7页普通12C的10.8～13.2V参数误用于12AC。Dropout1.7V为典型值，不当全工况保证；本板标称输入与输出相差约12V，使用14.5V输入窗口就足以说明标称压差，没有必要依赖1.7V典型值。

若U1保证11.4～12.6V窗口条件成立，该整段供电也落在U3/U5/U6/U7推荐范围内，距离各自最低供电充足；故下游供电窗口并非必须12.000V才通过。实际负载能否始终处于U1相应窗口及结温内，属于电源树实际实现条件，本轮未扩展成整板功耗深审。其余IC每路输出负载与共模/输入过压问题应在对应卡分析，不能因某信号口越轨就把正常供电电压也误报越界。

## 已解与真正剩余资料

已解：每颗准确供电脚、12/24V分组、准确后缀推荐与绝对范围、U7手册身份、U1标称输入稳压窗口和明显压差充分性。按公共标称电压口径，这些足够支持“供电电压兼容，无明显越界”的工程结论。

未在本次证明：外部VCC完整上下限与纹波、F1/D2在最大负载下压降、U1实际总负载/热稳压/启动轨迹。任务书DOCX§3.2确实将外部电源范围、整板电流留待定，但不能据此取消confirmed标称电压口径下已解部分；也不能仅因`currents:[]`就宣称实际所有电流完全未知，板上固定电阻与IC电流可由硬件继续核算。必要时应给具体影响边界：U1若实际VIN跌至14.5V以下或IO超所用40mA保证窗口，便不能继续引用11.4～12.6V作为全条件输出保证；不应笼统要求业务“提供所有内部供电”。

## 主图路径

主电源树用原理图PDF第2页；U2供电第5页，U3第6页，U4第3页，U5第4页，U6第8页，U7电源跨单元在第6页、运放功能第7页。引脚证据优先各准确手册脚位页；U1 TO-92底视图已保存`sources/U1-pin-page3.png`。报告图区分“标称电压核对通过”与“U1全负载实现条件未在本轮深审”，避免固定轨名被当作所有动态供电已保证。
