"""绘图上下文：集中持有比例、线宽组与「图纸毫米 → 模型空间」的换算。

核心概念
--------
本引擎把整张图都画在 **模型空间**（modelspace），不做布局视口。
因此所有「图纸上多长」的量都必须乘以比例分母 S 才能落到模型空间：

    模型空间长度 = 图纸毫米(paper mm) × S
    建筑实际长度  = 建筑毫米(mm)，直接使用，不再换算

举例（比例 1:50，S = 50）：
    A3 图框 420mm 宽 → 模型空间 21000
    建筑 6900mm 宽    → 模型空间 6900（纸上显示 138mm）
    7mm 的图名文字    → 模型空间字高 350
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Tuple

from . import standards as S


@dataclass
class Context:
    """一次出图的全部共享状态。"""

    doc: Any
    msp: Any
    scale: float            # 比例分母，如 50
    b: float                # 基本线宽 mm
    sheet_size: str         # A0..A4
    # 图纸尺寸（横向摆放后的 宽 × 高，单位 paper mm）
    sheet_w: float = 0.0
    sheet_h: float = 0.0
    warnings: list = field(default_factory=list)

    # ------------------------------------------------------------ 换算
    def ps(self, paper_mm: float) -> float:
        """图纸毫米 → 模型空间单位。"""
        return paper_mm * self.scale

    def ms(self, model_mm: float) -> float:
        """模型空间单位 → 图纸毫米（出图后的实际长度）。"""
        return model_mm / self.scale

    # ------------------------------------------------------------ 线宽
    @property
    def lineweights(self) -> Tuple[float, float, float, float]:
        """当前线宽组 (b, 0.7b, 0.5b, 0.25b)，单位 mm。"""
        return S.LINEWEIGHT_GROUPS[self.b]

    def lw(self, factor: float) -> int:
        """按线宽系数取 DXF lineweight 值（1/100mm）。

        factor 为相对基本线宽 b 的倍数，例如 0.5 表示 0.5b。
        图层表中出现的 0.18 / 0.13 等系数同样适用。
        """
        return S.to_dxf_lineweight(factor * self.b)

    # ------------------------------------------------------------ 自检
    def warn(self, message: str) -> None:
        self.warnings.append(message)
