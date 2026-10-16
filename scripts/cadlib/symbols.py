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
