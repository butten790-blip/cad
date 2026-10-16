# CAD 施工图出图技能 · cad-construction-drawing

按中国国标（GB/T 50001、GB/T 50104）自动生成**建筑室内施工图 DXF**，并配套 PNG / SVG 预览。

本仓库是一个 [WorkBuddy](https://www.workbuddy.cn) **技能（Agent Skill）**：模型只负责把口语需求翻译成 `drawing.spec.json`，几何一律交由仓库内的**确定性引擎**生成——同输入必然产出同输出，可直接送审、签章、做版本对比。

> English: A WorkBuddy skill that generates GB-compliant architectural construction drawings (DXF) — plan / floor-finish / ceiling layouts with axes, dimensions, title block, sections, schedules, etc. Model emits a spec JSON; a deterministic engine draws the geometry.

## 能画什么

- **图别**：平面布置图、地面铺装图（`floor_finish`）、天花布置图（`ceiling`）
- **图框与图签**：图框、标题栏（设计阶段 / 注册签章 / 会签栏）、图名（**右侧标比例**）、指北针
- **轴网**：细点画线 + 细实线圆圈轴号，自动跳过 I / O / Z
- **墙体**：连续多段线（非零散 LINE），门洞打断、门扇与开启弧线、窗的四条细线与洞口封口；
  **剪力墙另叠实心剖切填充（poché）**，填充落在独立的 `WALL-POC` 图层，与轮廓**分层**绘制
- **构件**：结构柱（混凝土涂黑）、剖切符号、门窗编号与门窗统计表
- **标注**：三道尺寸链（细部 / 轴线 / 总尺寸）+ 内墙局部尺寸链 + 墙厚引出标注、标高符号（室内空心 / 室外地坪涂黑）、散水与台阶
- **其它**：房间名与面积（上标单独绘制）、家具/洁具内置矢量图块、地面材质填充（仅铺装图）、设计说明区、版面校验
- **输出**：DXF + PNG + SVG 三种格式

## 安装

把本仓库克隆 / 解压到技能目录（二选一），重启 WorkBuddy 后说"画一张 CAD 平面图"即可触发：

- 用户级：`~/.workbuddy/skills/cad-construction-drawing/`
- 项目级：`<项目>/.workbuddy/skills/cad-construction-drawing/`

## 依赖

- Python 3.10+
- `ezdxf>=1.3,<2`、`jsonschema>=4.20`、`matplotlib>=3.8`
- Windows 上若要让中文按"长仿宋"渲染，系统需装有仿宋字体（缺字体会自动回退，DXF 数据不受影响）

```bash
python -m venv .venv
.venv/Scripts/pip install -r scripts/requirements.txt
```

## 命令行直接出图

```bash
cd scripts
python build_dxf.py ../templates/examples/plan_studio_1f.spec.json \
    -o out/图纸.dxf --png out/图纸.png
```

出图命令**自带自检报告**（按 `references/checklist.md` 的质检清单，含版面校验）：出现 `ERROR` 必须修 spec 后重跑，自检覆盖不到的两项（尺寸数字遮挡、立面/平面标高一致性）需人工复核。

## 目录结构

```
cad-construction-drawing/
├── SKILL.md                    技能说明与全部规范/约束（必读）
├── references/                 规范层：所有取值依据（改参数先改这里）
├── schema/drawing.spec.schema.json   模型与引擎之间的唯一契约
├── scripts/                    引擎层（纯确定性，禁网络/模型调用）
│   ├── build_dxf.py            命令行主入口
│   ├── pipeline.py             出图流水线
│   ├── validate.py             质检实现
│   ├── ingest/                 参考图摄取与复刻校验
│   │   ├── probe.py            参考图体检        ├── pdf_to_dxf.py    矢量保真转绘
│   │   ├── raster_to_dxf.py    位图描线          ├── classify_walls.py 剪力墙/隔墙分类
│   │   └── verify_clone.py     独立校验器（1:1 判定）
│   └── cadlib/                 墙体轮廓 / 标注 / 图层 / 标题栏 / 家具 ……
├── templates/examples/         可运行样例 spec
└── assets/blocks/              图库挂载点（index.json）
```

完整目录树与参数说明见 `SKILL.md`。

## 参考图复刻（参考图 → 可编辑 DXF）

用户给的**参考图**也能吃。**先判输入，再选工具**：矢量 PDF 直接读路径（几何级 1:1），
位图才走描线。用图上的**尺寸标注**反推比例做 1:1 标定。详见 `references/ingest.md`。

```bash
# 矢量 PDF（CAD 导出的图纸空间 PDF）→ 1:1 复刻
python scripts/ingest/pdf_to_dxf.py 参考图.pdf 输出.dxf --k 0.0066067 --x0 49.6 --y0 122.0
# 位图（JPG/PNG）→ 描线
python scripts/ingest/raster_to_dxf.py 参考图.png 输出.dxf --mm-per-px 12.5
```

**墙体识别铁律**：实心黑填充是**剪力墙**（承重/抗震）的 **poché 图例**，
墙的几何取其**轮廓线**；无填充的双线才是**隔墙**。
**轮廓与填充必须分层**——墙的几何在 `WALL-STR` / `WALL-PAR`，剖切填充单独落在 `WALL-POC`。

### 复刻完了必须校验：`verify_clone.py`

```bash
python scripts/ingest/verify_clone.py 参考图.pdf 复刻.dxf \
    --k 0.0066067 --x0 49.6 --y0 122.0 --out out/ --json out/报告.json
```

**纪律：校验不能自己验自己。** 这个脚本**不引用生成侧任何模块**，用两条独立的腿
（PyMuPDF 读参考图 / ezdxf 读复刻件）做**四路交叉验证**：几何双向覆盖率、
尺度自校正、文字层比对、总尺寸核对。输出差分图（黑=重合、灰=容差内、红=漏、蓝=多），
退出码 `0=PASS / 1=WARN / 2=FAIL`。详见 `references/ingest.md` 第六节。

## 踩坑登记（`references/pitfalls.md`）

本技能自带一份实测踩坑与对策登记表（输入判定、标定、单位、墙体表示、缓存、依赖安装……）。
**开工前先扫，命中直接套对策。** 每解决一个新问题就补登一条。

## 硬性约束（违反即产物不可用）

1. 模型只产出 `drawing.spec.json`，**禁止直接写 DXF 实体**。
2. 出图必须**字节可复现**：同 spec → 同 DXF（引擎已冻结元数据并固定哈希种子）。
3. 严禁改派图层、混用单位口径；规范取值先改 `references/` 再改 `cadlib/standards.py`。
4. 轴号禁用 I / O / Z；轴号圆圈必须显式 `CONTINUOUS` 线型。
5. **轮廓与填充不共层**：墙/柱的几何在 `WALL-STR` / `WALL-PAR`，剖切填充在 `WALL-POC`。
   验收标准——**关掉 `WALL-POC` 必须仍能看到完整的墙轮廓**。
6. **复刻结论不得由生成侧自证**：校验必须走 `verify_clone.py`（不 import 生成侧模块），
   且 `PASS` 之后仍须人工复核。

## 已知局限

楼梯 / 坡道、立面图 / 剖面图 / 节点大样尚未实现；尺寸数字遮挡为启发式判断，需人工复核；门扇方向仅按 `left/right` 铰接侧处理，待扩展 `to_room` 语义。详见 `SKILL.md`。

## 许可证

[MIT](LICENSE)
