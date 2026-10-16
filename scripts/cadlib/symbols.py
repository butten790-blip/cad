"""符号块定义。

按国标要求把可复用符号做成 **块（BLOCK）** 而非散线，便于复用与校验：

    ELEV_R / ELEV_L            室内楼地面标高（GB/T 50001 第11.8节）
                               等腰直角三角形，直角朝下，三角形高 3mm，引出水平线。
                               R = 引出线向右，L = 引出线向左。
    ELEV_OUT_R / ELEV_OUT_L    室外地坪标高（GB/T 50104 一层平面图要求）
                               同样形状，但三角形**涂黑**，用来区分室内外。
"""

from __future__ import annotations

from . import standards as S
from .layers import LAYER_SYMBOL

ELEV_LEADER_LEN = 12.0  # 引出水平线长度，paper mm
ELEV_TEXT_GAP = 1.5     # 数字距引出线的高度，paper mm

BLOCK_ELEV_R = "ELEV_R"
BLOCK_ELEV_L = "ELEV_L"
BLOCK_ELEV_OUT_R = "ELEV_OUT_R"
BLOCK_ELEV_OUT_L = "ELEV_OUT_L"

# 块名 → (引出线方向, 三角形是否涂黑)
BLOCK_KINDS = {
    BLOCK_ELEV_R: (1.0, False),
    BLOCK_ELEV_L: (-1.0, False),
    BLOCK_ELEV_OUT_R: (1.0, True),
    BLOCK_ELEV_OUT_L: (-1.0, True),
}


def block_for(outdoor: bool, to_right: bool) -> str:
    """按「室外地坪 / 引出方向」挑块名。"""
    if outdoor:
        return BLOCK_ELEV_OUT_R if to_right else BLOCK_ELEV_OUT_L
    return BLOCK_ELEV_R if to_right else BLOCK_ELEV_L


def define_symbols(ctx) -> None:
    """在文档中建立符号块。块内坐标一律用模型空间单位（paper mm × S）。"""
    doc = ctx.doc
    h = ctx.ps(S.ELEV_TRI_HEIGHT)      # 三角形高
    leader = ctx.ps(ELEV_LEADER_LEN)

    # 等腰直角三角形的三个顶点：直角顶点在下 (0,0)，底边水平在 y=h
    tri = [(0.0, 0.0), (h, h), (-h, h)]

    for name, (sign, filled) in BLOCK_KINDS.items():
        if name in doc.blocks:
            continue
        blk = doc.blocks.new(name=name)
        if filled:
            # 室外地坪：涂黑等腰直角三角形
            blk.add_solid(tri, dxfattribs={"layer": LAYER_SYMBOL})
        else:
            blk.add_lwpolyline(tri, close=True,
                               dxfattribs={"layer": LAYER_SYMBOL})
        blk.add_line((sign * h, h), (sign * (h + leader), h),
                     dxfattribs={"layer": LAYER_SYMBOL})


def elevation_text_offset(ctx, block_name: str):
    """给定标高块名，返回数字应放置的偏移 [dx, dy]（模型空间单位）。"""
    h = ctx.ps(S.ELEV_TRI_HEIGHT)
    leader = ctx.ps(ELEV_LEADER_LEN)
    gap = ctx.ps(ELEV_TEXT_GAP)
    sign = BLOCK_KINDS.get(block_name, (1.0, False))[0]
    return [sign * (h + leader / 2.0), h + gap]


# ------------------------------------------------------------------ 索引 / 详图符号
# GB/T 50001-2017 第7.2节。这些符号的内容（上下半圆编号）逐处不同，不做块，直接画。
def _symbol_text(ctx, at, content, height_mm):
    from ezdxf.enums import TextEntityAlignment
    ent = ctx.msp.add_text(
        str(content),
        height=ctx.ps(height_mm),
        dxfattribs={"layer": LAYER_SYMBOL, "style": S.STYLE_NAME},
    )
    ent.set_placement((float(at[0]), float(at[1])),
                      align=TextEntityAlignment.MIDDLE_CENTER)
    return ent


def draw_index_symbol(ctx, center, number, sheet=None):
    """索引符号（第7.2.1条）：细实线圆 + 水平直径，线宽 0.25b。

    number 注在**上半圆**；sheet 给定时注在**下半圆**（详图在另一张图纸），
    sheet 为 None 时在下半圆中间画一段**水平细实线**（详图与本图同一张）。
    """
    r = ctx.ps(S.INDEX_CIRCLE_DIA) / 2.0
    cx, cy = float(center[0]), float(center[1])
    attrs = {"layer": LAYER_SYMBOL, "lineweight": ctx.lw(S.LEADER_LW_FACTOR)}
    ctx.msp.add_circle((cx, cy), r, dxfattribs=attrs)
    ctx.msp.add_line((cx - r, cy), (cx + r, cy), dxfattribs=attrs)
    _symbol_text(ctx, (cx, cy + r * 0.45), number, S.SYMBOL_TEXT_HEIGHT)
    if sheet is None:
        w = r * 0.55
        ctx.msp.add_line((cx - w, cy - r * 0.45), (cx + w, cy - r * 0.45),
                         dxfattribs=attrs)
    else:
        _symbol_text(ctx, (cx, cy - r * 0.45), sheet, S.SYMBOL_TEXT_HEIGHT)


def draw_detail_symbol(ctx, center, number, source_sheet=None):
    """详图符号（第7.2.4条）：**粗实线**圆，直径 14mm，线宽 b。

    source_sheet 给定时（详图与被索引图样不在同一张图纸），
    用**细实线**在圆内画水平直径：上半圆注详图编号，下半圆注被索引图纸编号。
    """
    r = ctx.ps(S.DETAIL_CIRCLE_DIA) / 2.0
    cx, cy = float(center[0]), float(center[1])
    ctx.msp.add_circle((cx, cy), r,
                       dxfattribs={"layer": LAYER_SYMBOL,
                                   "lineweight": ctx.lw(1.0)})
    if source_sheet is None:
        _symbol_text(ctx, (cx, cy), number, S.SYMBOL_TEXT_HEIGHT)
        return
    ctx.msp.add_line((cx - r, cy), (cx + r, cy),
                     dxfattribs={"layer": LAYER_SYMBOL,
                                 "lineweight": ctx.lw(S.LEADER_LW_FACTOR)})
    _symbol_text(ctx, (cx, cy + r * 0.45), number, S.SYMBOL_TEXT_HEIGHT)
    _symbol_text(ctx, (cx, cy - r * 0.45), source_sheet, S.SYMBOL_TEXT_HEIGHT)


def draw_part_mark(ctx, center, number):
    """零件 / 钢筋 / 杆件 / 设备（消火栓、配电箱、管井）编号，第7.2.3条。

    细实线圆，直径 4~6mm（取 5mm），线宽 0.25b，阿拉伯数字顺序编号。
    """
    r = ctx.ps(S.PART_CIRCLE_DIA) / 2.0
    ctx.msp.add_circle(tuple(float(v) for v in center), r,
                       dxfattribs={"layer": LAYER_SYMBOL,
                                   "lineweight": ctx.lw(S.LEADER_LW_FACTOR)})
    _symbol_text(ctx, center, number, S.PART_TEXT_HEIGHT)


def draw_leader(ctx, points, text=None, text_height_mm=None):
    """引出线（第7.3节）：线宽 0.25b，水平直线或斜线再折成水平线。

    points 为折点序列；text 给定时注写在末段水平线上方。
    """
    attrs = {"layer": LAYER_SYMBOL, "lineweight": ctx.lw(S.LEADER_LW_FACTOR)}
    pts = [(float(p[0]), float(p[1])) for p in points]
    for a, b in zip(pts, pts[1:]):
        ctx.msp.add_line(a, b, dxfattribs=attrs)
    if text:
        h = text_height_mm if text_height_mm is not None else S.HEIGHT_NOTE
        tip = pts[-1]
        prev = pts[-2] if len(pts) > 1 else tip
        sign = 1.0 if tip[0] >= prev[0] else -1.0
        _symbol_text(ctx, (tip[0] + sign * ctx.ps(2.0), tip[1] + ctx.ps(h * 0.7)),
                     text, h)


def draw_index_symbols(ctx, items):
    for it in items or []:
        draw_index_symbol(ctx, it["at"], it.get("number", 1), it.get("sheet"))


def draw_detail_symbols(ctx, items):
    for it in items or []:
        draw_detail_symbol(ctx, it["at"], it.get("number", 1),
                           it.get("source_sheet"))


def draw_part_marks(ctx, items):
    for it in items or []:
        draw_part_mark(ctx, it["at"], it.get("number", ""))
