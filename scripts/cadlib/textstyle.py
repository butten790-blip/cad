"""文字样式：长仿宋（通过宽度因子 0.7 近似国标高宽比）。

国标要求汉字用长仿宋体、高宽比约 1:0.7，且最小字高不小于 2.5mm。
TTF 时代没有通用免费的国标长仿宋字体，本引擎采用业界通行做法：
    用仿宋 TTF + 文字样式宽度因子 0.7 近似。
若后续拿到单线 SHX 字体（hztxt.shx 等），只需替换 font 字段即可。
"""

from __future__ import annotations

import os

from . import standards as S


def create_text_styles(ctx) -> str:
    """建立 CAD_FANGSONG 文字样式，返回样式名。"""
    doc = ctx.doc
    if S.STYLE_NAME not in doc.styles:
        try:
            style = doc.styles.add(S.STYLE_NAME, font=S.FANGSONG_TTF)
        except TypeError:  # 兼容不同 ezdxf 版本的签名差异
            style = doc.styles.new(S.STYLE_NAME, {"font": S.FANGSONG_TTF})
    else:
        style = doc.styles.get(S.STYLE_NAME)

    style.dxf.width = S.FANGSONG_WIDTH_FACTOR
    style.dxf.height = 0.0  # 0 = 由实体自带字高控制
    style.dxf.oblique = 0.0

    if not _font_available(S.FANGSONG_TTF):
        ctx.warn(
            f"未找到字体 {S.FANGSONG_TTF}，DXF 中文字可能在其他机器上回退为默认字体；"
            f"预览图可能出现方框。建议把仿宋 TTF 随技能分发。"
        )
    return S.STYLE_NAME


def _font_available(filename: str) -> bool:
    """粗查 Windows 字体目录，仅用于给出警告，不影响出图。"""
    if os.name != "nt":
        return True
    win = os.environ.get("WINDIR", r"C:\Windows")
    return os.path.exists(os.path.join(win, "Fonts", filename))
