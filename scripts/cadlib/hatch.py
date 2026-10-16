"""材料填充（GB/T 50104 图例）。

填充统一落在 HATCH 图层、线宽 0.13b。
所有图案都以显式 definition 定义，不依赖 CAD 环境里是否加载了某个 .pat，
保证跨机器结果一致。

ezdxf 的图案定义行是 4 元组：
    (角度, 基点(x,y), 偏移(dx,dy), 短划线序列)
    短划线序列中 >0 为实线段、<0 为间隔、0.0 为点；空列表表示连续线。

角度与偏移的几何关系（决定填充密度，单位 mm）：
    - 0°   线：偏移 (0, dy) → 相邻线垂直间距 = dy
    - 90°  线：偏移 (dx, 0) → 相邻线垂直间距 = dx
    - 45°  线：偏移 (0, dy) → 相邻线垂直间距 = dy × cos45° = dy × 0.707
"""

from __future__ import annotations

SQRT2 = 1.41421356

# 45° 线：给定垂直间距 gap，换算成 y 方向偏移
def _diag_offset(gap: float) -> tuple:
    return (0.0, gap * SQRT2)


def _perp_shift(dist: float) -> tuple:
    """45° 线的垂直平移量（用于生成紧邻的平行线）。"""
    return (dist / SQRT2, -dist / SQRT2)


# 材料 key -> (图案名, definition, scale, angle)
PATTERN_LIBRARY = {
    # 砖墙剖切：45° 斜线，间距 2~4mm（取 3mm）
    "brick": ("BRICK_GB", [(45.0, (0.0, 0.0), _diag_offset(3.0), [])], 1.0, 0.0),

    # 混凝土剖切：三角形加小点
    "concrete": ("CONC_GB", [
        (45.0, (0.0, 0.0), _diag_offset(5.0), [1.2, -3.8]),
        (0.0, (0.0, 0.0), (0.0, 5.0), [0.1, -4.9]),
    ], 1.0, 0.0),

    # 石材/地砖：方格网 6×6mm
    "stone_tile": ("TILE_GB", [
        (0.0, (0.0, 0.0), (0.0, 6.0), []),
        (90.0, (0.0, 0.0), (6.0, 0.0), []),
    ], 1.0, 0.0),

    # 木地板：45° 斜线，间距大（9mm）
    "wood_floor": ("WOOD_GB", [
        (45.0, (0.0, 0.0), _diag_offset(9.0), []),
    ], 1.0, 0.0),

    # 玻璃：45° 斜线间距大，两条平行线
    "glass": ("GLASS_GB", [
        (45.0, (0.0, 0.0), _diag_offset(8.0), []),
        (45.0, _perp_shift(1.1), _diag_offset(8.0), []),
    ], 1.0, 0.0),

    # 防水层：小点
    "waterproof": ("WP_GB", [
        (0.0, (0.0, 0.0), (0.0, 3.0), [0.1, -2.9]),
        (45.0, (0.0, 0.0), _diag_offset(3.0), [0.1, -4.1]),
    ], 1.0, 0.0),

    # 保温材料：锯齿形（用两组错位的短划线近似，DXF 直线图案无法表达真正锯齿）
    "insulation": ("INSUL_GB", [
        (0.0, (0.0, 0.0), (0.0, 4.0), [2.0, -2.0]),
        (0.0, (0.0, 2.0), (0.0, 4.0), [1.0, -3.0]),
    ], 1.0, 0.0),
}

# 与 references/hatch.md 的对照说明，供人工核对
PATTERN_DESCRIPTION = {
    "brick": "45° 斜线，间距 2~4mm",
    "concrete": "三角形加小点",
    "stone_tile": "方格网",
    "wood_floor": "45° 斜线，间距大",
    "glass": "45° 斜线，间距大，两条平行线",
    "waterproof": "小点",
    "insulation": "锯齿形（DXF 直线图案近似表达）",
}


def add_solid_poch(msp, points, layer: str = "WALL-POC", color: int = 7) -> None:
    """墙体的**剖切填充**（poché）——实心黑。

    这是钢筋混凝土墙、柱被水平剖切后的图例画法：
    墙的几何来自**轮廓线**（直线 / 多段线命令画的），
    实心黑只是叠在轮廓里的一个**填充命令**（HATCH），不是墙本身。

    因此本函数只负责"填",
    轮廓由 `geometry.draw_walls` 经 `outline.build_wall_polylines` 并集后画出，
    两者**落在不同图层**（轮廓 WALL-STR / 填充 WALL-POC），便于单独开关与打印控制。
    """
    if not points or len(points) < 3:
        return
    hatch = msp.add_hatch(color=color, dxfattribs={"layer": layer})
    hatch.set_solid_fill(color=color)
    hatch.paths.add_polyline_path(
        [(float(p[0]), float(p[1])) for p in points], is_closed=True)


def add_area_hatch(msp, points, material: str, scale: float = 1.0,
                   layer: str = "HATCH") -> None:
    """按闭合多边形填充指定材料。

    points 为 [[x, y], ...]（模型空间 mm）。
    scale 传入出图比例分母 S —— 因为图中的间距 2~4mm 是**图纸毫米**，
    在 1:50 的模型空间里必须放大 50 倍，否则填充会密到糊成一片黑。
    """
    if material not in PATTERN_LIBRARY:
        return
    name, definition, base_scale, angle = PATTERN_LIBRARY[material]
    hatch = msp.add_hatch(color=256, dxfattribs={"layer": layer})
    hatch.set_pattern_fill(name, color=256, scale=base_scale * scale,
                           angle=angle, definition=definition)
    hatch.paths.add_polyline_path([(float(p[0]), float(p[1])) for p in points],
                                  is_closed=True)
