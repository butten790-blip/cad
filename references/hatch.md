# 填充图例（GB/T 50104）

> 来源：用户提供的 `AGENTS_CAD.md`。填充统一放在 `HATCH` 图层，线宽 `0.13b`。

| 材料 | 填充方式 |
|---|---|
| 砖墙剖切 | 45° 斜线，间距 2~4mm |
| 混凝土剖切 | 三角形加小点 |
| 石材/地砖 | 方格网 |
| 木地板 | 45° 斜线，间距大 |
| 玻璃 | 45° 斜线，间距大，两条平行线 |
| 防水层 | 小点 |
| 保温材料 | 锯齿形 |

## 映射到 DXF HATCH pattern

| 材料 key | DXF pattern 名 | scale 起点 | 备注 |
|---|---|---|---|
| `brick` | `ANSI31` | 参考间距 3.0 | 45° 斜线；调 `scale` 使间距落在 2~4mm |
| `concrete` | `AR-CONC` | 1.0 | 三角形+小点，自定义 PAT 更接近国标 |
| `stone_tile` | `NET` | 1.0 | 方格网 |
| `wood_floor` | `ANSI31` | 8.0 | 同 45° 斜线，放大间距 |
| `glass` | 自定义 `GLASS`（推荐） | 1.0 | 原表"45°斜线+两条平行线"，标准 pattern 无直接对应，需自定义 PAT |
| `waterproof` | `DOTS` | 1.0 | 小点 |
| `insulation` | 自定义 `INSUL` | 1.0 | 锯齿形，需自定义 PAT |

### 自定义 PAT 说明

`GLASS` 与 `INSUL` 标准库没有，需在 `scripts/cadlib/hatch.py` 里以 `set_pattern_fill(..., definition=[...])` 形式传入 pattern 定义行，或随技能分发 `.pat` 文件后加载。

填充填充密度由 `scale` 控制，**单位是 DXF 图纸单位（此处即 mm）**，因此 1:50 出图时若在模型空间按实际尺寸绘制，`scale` 直接取毫米级数值即可，不需要再乘比例。
