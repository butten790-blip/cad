"""图框、标题栏（右下角）与会签栏（左上角）。

依据 references/standards.md：
    - 标题栏在右下角，会签栏在左上角
    - 图框线线宽：A0/A1 用 b，A2/A3/A4 用 0.7b
    - 装订边 25mm，其余边 A0/A1=10mm、A2~A4=5mm
"""

from __future__ import annotations

from ezdxf.enums import TextEntityAlignment

from . import standards as S
from .annotation import add_text
from .layers import LAYER_FRAME

TITLE_LABELS = {
    "project": "工程名称",
    "drawing_name": "图名",
    "drawing_no": "图号",
    "scale": "比例",
    "designer": "设计",
    "checker": "审核",
    "date": "日期",
    "revision": "版本",
}


def _rect(msp, ctx, x0, y0, x1, y1, lw):
    msp.add_lwpolyline(
        [(ctx.ps(x0), ctx.ps(y0)), (ctx.ps(x1), ctx.ps(y0)),
         (ctx.ps(x1), ctx.ps(y1)), (ctx.ps(x0), ctx.ps(y1))],
        close=True,
        dxfattribs={"layer": LAYER_FRAME, "lineweight": lw},
    )


def _line(msp, ctx, x0, y0, x1, y1, lw):
    msp.add_line((ctx.ps(x0), ctx.ps(y0)), (ctx.ps(x1), ctx.ps(y1)),
                 dxfattribs={"layer": LAYER_FRAME, "lineweight": lw})


def frame_rect(ctx) -> dict:
    """纯计算：返回图框各关键矩形（paper mm），不产生任何实体。"""
    size = ctx.sheet_size
    W, H = ctx.sheet_w, ctx.sheet_h
    bind = S.FRAME_MARGIN_BIND[size]
    other = S.FRAME_MARGIN_OTHER[size]
    fx0, fy0 = bind, other
    fx1, fy1 = W - other, H - other
    tb_w, tb_h = S.TITLEBLOCK_W, S.TITLEBLOCK_H
    return {
        "sheet": (0.0, 0.0, W, H),
        "x0": fx0, "y0": fy0, "x1": fx1, "y1": fy1,
        "title_x0": fx1 - tb_w, "title_y0": fy0,
        "title_x1": fx1, "title_y1": fy0 + tb_h,
        "bind": bind, "other": other,
    }


def draw_frame(ctx, meta) -> dict:
    """绘制图框 + 标题栏 + 会签栏，返回图框关键矩形（paper mm）。"""
    msp = ctx.msp
    size = ctx.sheet_size
    W, H = ctx.sheet_w, ctx.sheet_h

    bind = S.FRAME_MARGIN_BIND[size]
    other = S.FRAME_MARGIN_OTHER[size]
    lw_frame = ctx.lw(S.FRAME_LW_FACTOR[size])
    lw_thin = ctx.lw(0.13)

    # ---- 纸张边界（细线）与图框线（粗线）
    _rect(msp, ctx, 0, 0, W, H, lw_thin)
    fx0, fy0 = bind, other
    fx1, fy1 = W - other, H - other
    _rect(msp, ctx, fx0, fy0, fx1, fy1, lw_frame)

    # ---- 标题栏：右下角 180 × 40，四行
    # 栏位按 GB/T 50001 第3.2节与建施图通行做法设置，含审图意见指出的
    # 「设计阶段」与「注册签章」（后者留空框供盖执业印章）。
    tb_w, tb_h = S.TITLEBLOCK_W, S.TITLEBLOCK_H
    tx0, ty0 = fx1 - tb_w, fy0
    tx1, ty1 = fx1, fy0 + tb_h
    _rect(msp, ctx, tx0, ty0, tx1, ty1, lw_frame)

    row_h = tb_h / 4.0
    y_top = ty1
    y1 = ty1 - row_h                     # 工程名称行 / 设计阶段行 分界
    y2 = y1 - row_h                      # 图名行 / 图号行 分界
    y3 = y2 - row_h                      # 设计审核行 分界
    for yy in (y1, y2, y3):
        _line(msp, ctx, tx0, yy, tx1, yy, lw_thin)

    def cell(label, value, ya, yb, xa, xb, xv0, xv1, vh, lh=3.5):
        _rect(msp, ctx, xa, ya, xb, yb, lw_thin)
        if xv0 is not None:
            _line(msp, ctx, xv0, ya, xv0, yb, lw_thin)
        add_text(ctx, (ctx.ps((xa + xv0) / 2.0), ctx.ps((ya + yb) / 2.0)),
                 label, ctx.ps(lh), layer="TEXT")
        text = str(value) if value not in (None, "") else "-"
        add_text(ctx, (ctx.ps((xv0 + xv1) / 2.0), ctx.ps((ya + yb) / 2.0)),
                 text, ctx.ps(vh), layer="TEXT")

    # 第一行：工程名称 | 设计阶段
    # 值区只到 tx0+105 —— 右侧 105mm 起是「设计阶段」，铺满会横向压字
    cell("工程名称", meta.get("project"), y2, y_top, tx0, tx0 + 105.0,
         tx0 + 30.0, tx0 + 105.0, 5.0)
    _line(msp, ctx, tx0 + 105.0, y2, tx0 + 105.0, y_top, lw_thin)
    add_text(ctx, (ctx.ps(tx0 + 120.0), ctx.ps((y2 + y_top) / 2.0)),
             "设计阶段", ctx.ps(3.5), layer="TEXT")
    add_text(ctx, (ctx.ps(tx0 + 157.5), ctx.ps((y2 + y_top) / 2.0)),
             str(meta.get("design_stage") or "施工图"), ctx.ps(3.5),
             layer="TEXT")

    # 第二行：图名 | 图号
    # 图签内的图名用 5mm（出图基线：标注/说明 3.5、图名 5、图签大标题 7）；
    # 7mm 是图样正下方独立图名的字高，两者不要混用。
    cell("图名", meta.get("drawing_name"), y1, y2, tx0, tx0 + 105.0,
         tx0 + 30.0, tx0 + 105.0, 5.0)
    _line(msp, ctx, tx0 + 105.0, y1, tx0 + 105.0, y2, lw_thin)
    add_text(ctx, (ctx.ps(tx0 + 120.0), ctx.ps((y1 + y2) / 2.0)),
             "图号", ctx.ps(3.5), layer="TEXT")
    add_text(ctx, (ctx.ps(tx0 + 157.5), ctx.ps((y1 + y2) / 2.0)),
             str(meta.get("drawing_no") or "-"), ctx.ps(5.0), layer="TEXT")

    # 第三行：设计 | 审核 | 比例 | 日期（四等分，每格内再分标签/值）
    quad = tb_w / 4.0
    bottoms = [("designer", "设计"), ("checker", "审核"),
               ("scale", "比例"), ("date", "日期")]
    for i, (key, label) in enumerate(bottoms):
        xa = tx0 + quad * i
        xv = xa + 16.0
        cell(label, meta.get(key), y3, y2, xa, xa + quad, xv, xa + quad, 3.5)

    # 第四行：注册签章（留空框）+ 会签
    cell("注册签章", "", ty0, y3, tx0, tx0 + 105.0, tx0 + 30.0,
         tx0 + 105.0, 3.5)
    add_text(ctx, (ctx.ps(tx0 + 120.0), ctx.ps((ty0 + y3) / 2.0)),
             "会签", ctx.ps(3.5), layer="TEXT")
    _line(msp, ctx, tx0 + 105.0, ty0, tx0 + 105.0, y3, lw_thin)
    add_text(ctx, (ctx.ps(tx0 + 141.0), ctx.ps((ty0 + y3) / 2.0)),
             str(meta.get("countersign") or "-"), ctx.ps(3.5), layer="TEXT")

    # ---- 会签栏：左上角 75 × 20
    sb_w, sb_h = S.SIGNBOX_W, S.SIGNBOX_H
    sx0, sy1 = fx0, fy1
    sx1, sy0 = fx0 + sb_w, fy1 - sb_h
    _rect(msp, ctx, sx0, sy0, sx1, sy1, lw_thin)
    _line(msp, ctx, sx0, sy0 + sb_h / 2.0, sx1, sy0 + sb_h / 2.0, lw_thin)
    for i in range(1, 3):
        _line(msp, ctx, sx0 + sb_w / 3.0 * i, sy0,
              sx0 + sb_w / 3.0 * i, sy1, lw_thin)
    for i, label in enumerate(("专业", "姓名", "日期")):
        add_text(ctx, (ctx.ps(sx0 + sb_w / 3.0 * (i + 0.5)),
                       ctx.ps(sy1 - sb_h / 4.0)),
                 label, ctx.ps(3.5), layer="TEXT")

    # 图名不在标题栏之外另画：标题栏的「图名」栏是正式位置，
    # 图样正下方的图名由 pipeline 调 draw_drawing_name() 单独绘制。
    return frame_rect(ctx)


def text_width_mm(text: str, height_mm: float) -> float:
    """粗估文字横向宽度（图纸毫米）。

    汉字按宽度因子 0.7、拉丁按 0.55 估，再统一放宽 8% —— 这是给「图名 + 比例」
    这类需要左右排布的场景用的，估窄了会互相压字。
    """
    raw = sum(height_mm * (0.70 if ord(ch) > 0x2E80 else 0.55) for ch in str(text))
    return raw * 1.08


def draw_drawing_name(ctx, name, bbox, frame=None, scale_label=None) -> float:
    """图名写在**图的正下方**，图名右侧注写比例，图名下画一条粗实线。

    依据 GB/T 50001：
        - 第6章 比例：比例应注写在图名的**右侧**，字的基准线取平；
        - 制图通行要求：图名写在每个图的正下方，图名下画粗线以显目。
    纵向位置取在轴号圆圈之下，避免压住尺寸链与轴号。
    传入 frame 时会把整块（图名 + 比例）横向挪开，避免与右下角标题栏相撞。
    返回图名占位矩形 (x0, y0, x1, y1)，单位图纸毫米。
    """
    if not name:
        return None
    msp = ctx.msp
    h = ctx.ps(S.HEIGHT_DRAWING_NAME)                 # 图名 7mm
    sh = ctx.ps(S.HEIGHT_NOTE)                        # 比例 5mm
    gap = ctx.ps(8.0)

    w_name = ctx.ps(text_width_mm(name, S.HEIGHT_DRAWING_NAME))
    scale_txt = str(scale_label or "")
    w_scale = ctx.ps(text_width_mm(scale_txt, S.HEIGHT_NOTE)) if scale_txt else 0.0
    total = w_name + (gap + w_scale if scale_txt else 0.0)

    pad = ctx.ps(4.0)
    half_mm = ctx.ms(total) / 2.0 + 4.0
    cx_mm = ctx.ms((bbox[0] + bbox[2]) / 2.0)
    if frame:
        if cx_mm + half_mm > frame["title_x0"] - 2.0:
            cx_mm = frame["title_x0"] - 2.0 - half_mm
        if cx_mm - half_mm < frame["x0"] + 2.0:
            cx_mm = frame["x0"] + 2.0 + half_mm
    cx = ctx.ps(cx_mm)

    y = bbox[1] - ctx.ps(S.AXIS_EXTEND_MIN) - ctx.ps(4.0) - h / 2.0
    x0 = cx - total / 2.0

    # 图名与比例的基准线取平
    baseline = y - h * 0.5
    add_text(ctx, (x0, baseline + h * 0.5), str(name), h, layer=LAYER_FRAME)
    if scale_txt:
        add_text(ctx, (x0 + w_name + gap, baseline + sh * 0.5),
                 scale_txt, sh, layer=LAYER_FRAME)

    y_line = baseline - ctx.ps(1.2)
    msp.add_line((x0 - pad, y_line), (x0 + total + pad, y_line),
                 dxfattribs={"layer": LAYER_FRAME, "lineweight": ctx.lw(1.0)})

    return (cx_mm - half_mm, ctx.ms(y_line), cx_mm + half_mm,
            ctx.ms(baseline + h))


def draw_north_arrow(ctx, frame) -> None:
    """指北针（一层平面图必备）。

    依据 GB/T 50104：细实线绘制，圆直径 24mm，指针尾部宽 3mm，指针头部注「北」。
    布置在图框内右上角。
    """
    msp = ctx.msp
    r = ctx.ps(S.NORTH_ARROW_DIA / 2.0)
    cx = ctx.ps(frame["x1"] - 22.0)
    cy = ctx.ps(frame["y1"] - 30.0)

    msp.add_circle((cx, cy), r, dxfattribs={"layer": LAYER_FRAME})

    tail = ctx.ps(S.NORTH_ARROW_TAIL / 2.0)
    msp.add_solid([(cx, cy + r), (cx - tail, cy - r), (cx + tail, cy - r)],
                  dxfattribs={"layer": LAYER_FRAME})

    add_text(ctx, (cx, cy + r + ctx.ps(5.0)), "北",
             ctx.ps(S.HEIGHT_NOTE), layer=LAYER_FRAME)
