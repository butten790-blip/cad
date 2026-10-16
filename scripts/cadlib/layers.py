"""图层、线型与标注样式创建。

图层表严格来自 references/layers.md（用户 AGENTS_CAD.md 原表），不得改派。
"""

from __future__ import annotations

import ezdxf

from . import standards as S

# (图层名, 线宽系数(相对 b), ACI 颜色, 线型, 用途)
LAYER_TABLE = (
    ("WALL-STR", 0.50, 7, "CONTINUOUS", "结构墙/承重墙剖切到"),
    ("WALL-PAR", 0.25, 7, "CONTINUOUS", "隔墙/非承重墙"),
    ("WIN-DOOR", 0.7,  2, "CONTINUOUS", "门窗洞口和图例"),
    ("FURN",     0.18, 8, "CONTINUOUS", "家具/洁具/陈设"),
    ("DIM",      0.13, 1, "CONTINUOUS", "尺寸标注"),
    ("TEXT",     0.13, 5, "CONTINUOUS", "文字说明"),
    # AXIS 原表为「红」，按审图意见改为与图线同色（7=白/黑）。
    # 依据：轴线是辅助线、轴号是标注元素，出图时都应是细黑线；
    # 轴号圆圈更要求细实线（GB/T 50001 第8章），红线圆不符合。
    ("AXIS",     0.13, 7, "CENTER",     "定位轴线（细点画线）与轴号（细实线圆圈）"),
    ("HATCH",    0.13, 8, "CONTINUOUS", "材料填充"),
    ("ELEC",     0.13, 2, "DASHED",     "电气线路（虚线）"),
    ("WATER",    0.13, 3, "CONTINUOUS", "给排水（双线）"),
)

LAYER_NAMES = tuple(r[0] for r in LAYER_TABLE)

# 辅助图层（非用户规范表内，仅用于图框与符号，单独命名避免污染上表）
LAYER_FRAME = "FRAME"
LAYER_SYMBOL = "SYMBOL"
LAYER_SITE = "SITE"   # 散水 / 台阶 / 坡道等室外构配件（细实线）


def ensure_linetypes(doc) -> None:
    """确保 CENTER / DASHED 等线型存在。new(setup=True) 通常已加载，此处兜底。"""
    for name, desc, pattern in (
        ("CENTER", "__center_____", [2.0, 1.25, -0.25, 1.25]),
        ("DASHED", "__dashed_____", [0.6, 0.5, -0.25]),
    ):
        if name not in doc.linetypes:
            doc.linetypes.add(name, pattern=pattern, description=desc)


def create_layers(ctx) -> None:
    """按图层表建立图层，线宽按当前线宽组换算。"""
    doc = ctx.doc
    for name in (LAYER_FRAME, LAYER_SYMBOL, LAYER_SITE):
        if name not in doc.layers:
            doc.layers.add(name, color=7, linetype="CONTINUOUS",
                           lineweight=ctx.lw(0.13))

    for name, lw_factor, aci, linetype, purpose in LAYER_TABLE:
        if name in doc.layers:
            layer = doc.layers.get(name)
        else:
            layer = doc.layers.add(
                name,
                color=aci,
                linetype=linetype,
                lineweight=ctx.lw(lw_factor),
            )
        layer.description = purpose


def create_dimstyle(ctx, name: str = "GB_DIM") -> str:
    """建立符合 GB/T 50001 第11章的标注样式。

    关键设置：
        dimtsz  = 2.5   45° 斜短线起止符号长度（国标 2~3mm）
        dimblk  = ""    置空后 DXF 才会用斜短线而非箭头
        dimtad  = 1     尺寸数字在尺寸线上方
        dimexe  = 2.5   尺寸界线超出尺寸线 2~3mm
        dimscale= S     整体按出图比例放大
    """
    doc = ctx.doc
    if name not in doc.dimstyles:
        try:
            doc.dimstyles.add(name, dxfattribs={})
        except TypeError:  # 兼容不同 ezdxf 版本的签名差异
            doc.dimstyles.new(name, {})
    ds = doc.dimstyles.get(name)

    attribs = {
        "dimtxt": S.HEIGHT_DIM,        # 尺寸数字字高 3.5
        "dimasz": S.DIM_TICK_LEN,      # 箭头尺寸（斜短线模式下同长度）
        "dimtsz": S.DIM_TICK_LEN,      # >0 → 使用 45° 斜短线
        "dimblk": "",
        "dimblk1": "",
        "dimblk2": "",
        "dimexe": S.DIM_EXT_BEYOND,    # 尺寸界线超出尺寸线
        "dimexo": 1.0,                 # 尺寸界线起点偏移
        "dimgap": 1.0,                 # 文字与尺寸线间隙
        "dimtad": 1,                   # 文字置于尺寸线上方
        "dimjust": 0,                  # 文字水平居中
        "dimtih": 0,                   # 文字与尺寸线对齐
        "dimtoh": 0,
        "dimdec": 0,                   # 毫米整数，不保留小数
        "dimzin": 8,                   # 消除后续零
        "dimscale": ctx.scale,         # 整体比例
        "dimclrd": 256,                # 尺寸线随层（DIM 图层为红）
        "dimclre": 256,                # 尺寸界线随层
        "dimclrt": 256,                # 尺寸文字随层
        "dimtxsty": S.STYLE_NAME,      # 长仿宋
        "dimlunit": 2,                 # 十进制
        "dimlfac": 1.0,
    }
    for key, value in attribs.items():
        try:
            setattr(ds.dxf, key, value)
        except (AttributeError, TypeError):
            ctx.warn(f"当前 ezdxf 版本不支持标注变量 {key}，已跳过设置")
    try:
        ds.dxf.dimdsep = ord(".")
    except (AttributeError, TypeError):
        pass
    return name
