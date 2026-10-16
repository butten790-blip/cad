"""家具 / 洁具 / 陈设图块。

三级回退：
    1. assets/blocks/index.json 里登记的真实图块（优先，可随比例缩放）；
    2. 本模块内置的矢量画法（床 / 沙发 / 餐桌 / 马桶 / 洗手盆 / 浴缸 / 灶台 等）；
    3. 都没有时按 width × depth 画矩形占位并记提示。

内置画法在「局部坐标系」下绘制：物件外包矩形为
    x ∈ [-w/2, w/2]，y ∈ [-d/2, d/2]，图块插入点 at 即外包中心。
rotation 通过统一旋转变换套到世界坐标。
"""

from __future__ import annotations

import json
import math
import os

FURNITURE_INDEX = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "assets", "blocks", "index.json",
)


def load_index():
    """读取图块索引。以 _ 开头的键是注释/示例，不参与查找。"""
    if os.path.exists(FURNITURE_INDEX):
        try:
            with open(FURNITURE_INDEX, "r", encoding="utf-8") as fp:
                data = json.load(fp)
            return {k: v for k, v in data.items()
                    if not k.startswith("_") and isinstance(v, str)}
        except (OSError, ValueError):
            return {}
    return {}


def _rect(msp, cx, cy, w, d, cos, sin):
    pts = []
    for dx, dy in ((-w / 2, -d / 2), (w / 2, -d / 2),
                   (w / 2, d / 2), (-w / 2, d / 2)):
        pts.append((cx + dx * cos - dy * sin, cy + dx * sin + dy * cos))
    msp.add_lwpolyline(pts, close=True, dxfattribs={"layer": "FURN"})


def _ellipse(msp, cx, cy, rx, ry, cos, sin):
    """局部椭圆近似（12 段），中心在 (cx, cy)。"""
    pts = []
    for i in range(12):
        a = math.tau * i / 12.0
        lx, ly = rx * math.cos(a), ry * math.sin(a)
        pts.append((cx + lx * cos - ly * sin, cy + lx * sin + ly * cos))
    msp.add_lwpolyline(pts, close=True, dxfattribs={"layer": "FURN"})


def _line(msp, cx, cy, cos, sin, x0, y0, x1, y1):
    msp.add_line(
        (cx + x0 * cos - y0 * sin, cy + x0 * sin + y0 * cos),
        (cx + x1 * cos - y1 * sin, cy + x1 * sin + y1 * cos),
        dxfattribs={"layer": "FURN"},
    )


# ---- 内置洁具/家具画法：签名 (msp, cx, cy, w, d, cos, sin) ----
def _b_double_bed(msp, cx, cy, w, d, c, s):
    _rect(msp, cx, cy, w, d, c, s)
    _rect(msp, cx, cy + d / 2 - d / 12.0, w - d * 0.08, d / 6.0, c, s)   # 双枕
    _line(msp, cx, cy, c, s, -w / 2 + 3, d / 6.0, w / 2 - 3, d / 6.0)     # 被角线


def _b_single_bed(msp, cx, cy, w, d, c, s):
    _b_double_bed(msp, cx, cy, w, d, c, s)


def _b_sofa(msp, cx, cy, w, d, c, s):
    _rect(msp, cx, cy, w, d, c, s)
    _rect(msp, cx, cy - d * 0.06, w * 0.70, d * 0.72, c, s)               # 坐垫
    _rect(msp, cx - w / 2 + d * 0.10, cy, d * 0.18, d, c, s)              # 左扶手
    _rect(msp, cx + w / 2 - d * 0.10, cy, d * 0.18, d, c, s)              # 右扶手


def _b_table(msp, cx, cy, w, d, c, s):
    _rect(msp, cx, cy, w, d, c, s)
    # 四把椅子（简化为小方块），只在够大时画
    if w >= 800 and d >= 800:
        ch = min(w, d) * 0.18
        for dx, dy in ((0, d / 2 + ch / 2), (0, -d / 2 - ch / 2),
                       (w / 2 + ch / 2, 0), (-w / 2 - ch / 2, 0)):
            _rect(msp, cx + dx * c - dy * s, cy + dx * s + dy * c, ch, ch, c, s)


def _b_desk(msp, cx, cy, w, d, c, s):
    _rect(msp, cx, cy, w, d, c, s)
    _line(msp, cx, cy, c, s, -w / 2 + 3, 0, w / 2 - 3, 0)


def _b_toilet(msp, cx, cy, w, d, c, s):
    _rect(msp, cx, cy - d / 2 + d / 10.0, w, d / 5.0, c, s)               # 水箱
    _ellipse(msp, cx, cy + d / 8.0, w / 2, d * 0.36, c, s)                # 便盆


def _b_washbasin(msp, cx, cy, w, d, c, s):
    _rect(msp, cx, cy, w, d, c, s)                                        # 台面
    _ellipse(msp, cx, cy + d * 0.08, w * 0.34, d * 0.28, c, s)            # 盆


def _b_bathtub(msp, cx, cy, w, d, c, s):
    _rect(msp, cx, cy, w, d, c, s)
    _rect(msp, cx, cy, w * 0.80, d * 0.76, c, s)
    _ellipse(msp, cx - w * 0.22, cy, w * 0.10, d * 0.20, c, s)            # 排水


def _b_stove(msp, cx, cy, w, d, c, s):
    _rect(msp, cx, cy, w, d, c, s)
    _ellipse(msp, cx - w / 4, cy, w * 0.16, d * 0.20, c, s)               # 灶眼
    _ellipse(msp, cx + w / 4, cy, w * 0.16, d * 0.20, c, s)


def _b_fridge(msp, cx, cy, w, d, c, s):
    _rect(msp, cx, cy, w, d, c, s)
    _line(msp, cx, cy, c, s, -w / 2 + 2, 0, w / 2 - 2, 0)


BUILTIN_FURNITURE = {
    "双人床": _b_double_bed,
    "单人床": _b_single_bed,
    "床": _b_double_bed,
    "沙发": _b_sofa,
    "餐桌": _b_table,
    "办公桌": _b_desk,
    "书桌": _b_desk,
    "马桶": _b_toilet,
    "坐便器": _b_toilet,
    "洗手盆": _b_washbasin,
    "洗脸盆": _b_washbasin,
    "浴缸": _b_bathtub,
    "灶台": _b_stove,
    "冰箱": _b_fridge,
}


def draw_furniture(ctx, items, index=None) -> None:
    if not items:
        return
    msp = ctx.msp
    index = index if index is not None else load_index()

    for item in items:
        name = item["block"]
        at = (float(item["at"][0]), float(item["at"][1]))
        rot = float(item.get("rotation", 0) or 0)

        # 1) 真实图块
        if name in index and os.path.exists(index[name]):
            try:
                msp.add_blockref(name, at,
                                 dxfattribs={"layer": "FURN", "rotation": rot})
                continue
            except Exception:  # noqa: BLE001
                pass

        w = item.get("width")
        d = item.get("depth")
        if not (w and d):
            if name in BUILTIN_FURNITURE:
                w = w or 1200
                d = d or 600
            else:
                ctx.warn(f"家具「{name}」既不在图库索引中，也未提供 width/depth，"
                         f"已跳过（图库索引：assets/blocks/index.json）")
                continue

        # 2) 内置矢量画法
        if name in BUILTIN_FURNITURE:
            cx, cy = at
            rad = math.radians(rot)
            BUILTIN_FURNITURE[name](msp, cx, cy, float(w), float(d),
                                    math.cos(rad), math.sin(rad))
            continue

        # 3) 矩形占位
        _rect(msp, cx, cy, float(w), float(d), math.cos(rad), math.sin(rad))
