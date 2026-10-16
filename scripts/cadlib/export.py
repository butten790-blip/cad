"""输出：保存 DXF 与渲染预览位图。

DXF 版本固定为 R2018（AC1032）。如需下游软件兼容，可在 build_dxf 的
--dxf-version 参数中切到 R2013 / R2010。
"""

from __future__ import annotations

import os

# 预览渲染时的字体替换表：DXF 里写仿宋（符合长仿宋体要求），渲染时临时换成
# 渲染不出黑块的字体。实测 simfang / simsun 都会把「图」字的内框填黑
# （ezdxf 的字形填充规则所致），simhei / msyh 干净。
# 详见 export_png 内的说明。
PREVIEW_FONT_SWAP = {
    "simfang.ttf": "msyh.ttc",
}


def save_dxf(doc, path: str) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    doc.saveas(path)
    return path


def export_png(doc, path: str, dpi: int = 200, size_inch=(16.5, 11.7),
               extents=None) -> str:
    """用 matplotlib 后端渲染模型空间为 PNG，供人眼验收。

    extents = (x0, y0, x1, y1) 时，视图严格锁定到该范围（用来锁定整张图纸），
    这样预览图与图纸纸面一一对应，便于按纸面坐标核对版面。
    不传则按内容自动适配（可能裁掉空白，导致坐标核对失真）。
    """
    os.environ.setdefault("MPLBACKEND", "Agg")
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from ezdxf.addons.drawing import Frontend, RenderContext
    from ezdxf.addons.drawing.config import (BackgroundPolicy, ColorPolicy,
                                             Configuration)
    from ezdxf.addons.drawing.matplotlib import MatplotlibBackend

    matplotlib.rcParams["font.sans-serif"] = [
        "SimFang", "FangSong", "Microsoft YaHei", "SimSun", "DejaVu Sans",
    ]
    matplotlib.rcParams["axes.unicode_minus"] = False

    # 用「纸面配色」渲染：背景声明为白纸。
    # 图层表按 AutoCAD 习惯把墙体定为 ACI 7（白）——这是"黑底模型空间里的白线"，
    # 若渲染器默认按黑底处理，白线画在白纸上就完全看不见（墙、图框、图名、
    # 指北针会全部消失）。把 background_policy 显式设为 WHITE，渲染器才会把
    # ACI 7 映射成黑色，其余图层保持各自颜色。
    # 注意：不要叠加 COLOR_SWAP_BW —— 它会把这次映射再翻回去，等于没修。
    # min_lineweight / lineweight_scaling：DXF 最细档 0.05mm 在 200dpi 下不足 1px，
    # 若不设下限，预览里墙、尺寸线、家具全被压成同一根发丝，看不出粗细层次
    # （审图意见「线宽无区分」的观感来源）。设下限让细线至少可见、再放大差异，
    # 预览才能反映真实的线宽层次；这只影响预览，DXF 内的线宽值不受影响。
    cfg = Configuration(background_policy=BackgroundPolicy.WHITE,
                        min_lineweight=0.6,
                        lineweight_scaling=1.4)

    # 预览专用字体替换。
    # ezdxf 把字形轮廓转成填充路径时用的是 nonzero 填充规则，仿宋（simfang.ttf）
    # 的「图」字轮廓方向与之不符，内框会被填成黑块（黑体、雅黑同样受影响，
    # 宋体正常）。这是渲染器的填充规则问题，DXF 数据本身没错，
    # AutoCAD 打开是按字库正常显示的。为了预览可读，渲染前临时把文字样式
    # 指到渲染干净的字体，渲染完再改回去（DXF 已在调用本函数前落盘）。
    swapped = {}
    for style in doc.styles:
        font = (style.dxf.get("font", "") or "").lower()
        if font in PREVIEW_FONT_SWAP:
            swapped[style.dxf.name] = style.dxf.font
            style.dxf.font = PREVIEW_FONT_SWAP[font]

    fig = plt.figure(figsize=size_inch)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_axis_off()
    try:
        backend = MatplotlibBackend(ax)
        ctx = RenderContext(doc)
        frontend = Frontend(ctx, backend, config=cfg)
        frontend.draw_layout(doc.modelspace(), finalize=False)
        if extents is not None:
            ax.set_xlim(extents[0], extents[2])
            ax.set_ylim(extents[1], extents[3])
            ax.set_aspect("equal", adjustable="box")
        else:
            backend.finalize()
        fig.savefig(path, dpi=dpi, facecolor="white")
    finally:
        plt.close(fig)
        for name, font in swapped.items():
            doc.styles.get(name).dxf.font = font
    return path


def export_svg(doc, path: str) -> str:
    """矢量预览（可选）。部分环境字体映射不完整，主要用于几何核对。

    ezdxf 1.4 的接口是「无参构造后端 → 前端绘制 → get_string(page)」；
    旧版本则把文件对象直接交给后端构造。这里两种都兼容。
    """
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    from ezdxf.addons.drawing import Frontend, RenderContext
    from ezdxf.addons.drawing.svg import SVGBackend

    try:
        from ezdxf.addons.drawing.layout import Page
    except ImportError:
        Page = None

    if Page is not None:
        try:
            backend = SVGBackend()
            Frontend(RenderContext(doc), backend).draw_layout(
                doc.modelspace(), finalize=True)
            svg = backend.get_string(Page(0, 0))   # fit_page 默认自动适配
            with open(path, "w", encoding="utf-8") as fp:
                fp.write(svg)
            return path
        except (TypeError, AttributeError):
            pass

    with open(path, "w", encoding="utf-8") as fp:   # 旧版 API 兜底
        Frontend(RenderContext(doc), SVGBackend(fp)).draw_layout(
            doc.modelspace(), finalize=True)
    return path
