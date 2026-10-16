# 转换产物

`cad-skill` 的图片矢量化的实际输出，供对照与复现。生成命令都基于
`cad-skill/scripts/image_to_walls.py` 与 `image_to_vector.py`（同仓库）。

| 文件 | 来源 | 说明 |
| --- | --- | --- |
| `floorplan.dxf` | 灰调家装平面图（1012×1252） | 未标定（单位为像素）：42 段墙 + 线work。参数 `--min-wall-px 10 --min-segment-px 15 --component-min-area 600` |
| `floorplan-walls.json` | 同上 | 墙中心线 + 厚度的 `plan.json`，可接 `wall_plan.py` 继续改图 |
| `floorplan2.dxf` | 彩色家装平面图（2400×1808，带尺寸链） | 已标定 **0.1335 px/mm（即 1px≈7.5mm）**，单位为毫米：61 段墙（`A-WALL`）+ 1546 条清理后的线work（`A-TRACE`）。尺寸链逐段复核误差 +1/−7/+5/−7mm（全程 0.19%） |
| `floorplan2-walls.json` | 同上 | 毫米制的墙中心线 + 厚度（实测 151/267mm，该图为效果图、墙厚不按比例，未做吸附） |
| `deepseek-logo.svg` / `deepseek-logo.dxf` | DeepSeek 标识位图（461×465） | 轮廓描摹：9 个外轮廓 + 9 个内孔，IoU 0.982、面积比 0.997，尺寸 100.1×103.6mm；DXF 含 18 条多段线与 9 个带岛填充 |

## 复现

```bash
# 彩色平面图（带尺寸链定标）
python3 cad-skill/scripts/image_to_walls.py <平面图.png> \
    --px-per-mm 0.1335 --min-wall-px 10 --min-segment-px 15 --component-min-area 600 \
    --out-dxf out/floorplan2.dxf --out-plan out/floorplan2-walls.json
# 需要房间几何/面积时另加 --rooms（默认不生成闭合线、不输出面积）

# 标识位图
python3 cad-skill/scripts/image_to_vector.py <标识.png> \
    --px-per-mm 3.99 --out-svg out/deepseek-logo.svg --out-dxf out/deepseek-logo.dxf
```

原始位图未纳入仓库（体积与来源原因），上表已记录每份产物的尺寸与参数，按上面的命令
用同一张图可复现。

**注意**：`deepseek-logo.*` 是第三方标识的像素描摹结果，仅供个人测试参照；
平面图为本机示例，如需移出公开仓库可直接删除这些文件。
