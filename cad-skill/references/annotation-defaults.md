# 注释默认样式（无院标模板时）

没有天正/院标模板时，DXF 输出使用一套“能直接打开、不出现问号”的安全默认。
它不是天正智能对象，只是标准 AutoCAD 的 `STYLE` / `DIMSTYLE`。

## 文字

| 项 | 默认 |
| --- | --- |
| 样式名 | `TZ_HZ` |
| 字体 | `txt.shx` |
| 大字体 | `gbcbig.shx` |
| 宽度因子 | 0.7 |
| 字高 | 0（随实体；默认注释字高见下） |

`txt.shx` 与 `gbcbig.shx` 随 AutoCAD 一起安装，比 `hztxt.shx` 更不容易缺字体。
中文由大字体提供，西文由 `txt.shx` 提供；纯西文样式为 `TZ_EN`。

## 标注

| 项 | 默认 | 说明 |
| --- | --- | --- |
| 样式名 | `CADSKILL` | 与其他输出共用 |
| 出图比例 | 1:100 | `--scale 100` |
| 纸面字高 | 3.5mm | 模型空间 350mm |
| 箭头 | 建筑斜线 1.5mm | `DIMTSZ = 150` |
| 尺寸界线偏移 | 1.0mm | `DIMEXO = 100` |
| 尺寸界线超出 | 1.5mm | `DIMEXE = 150` |
| 文字间距 | 0.5mm | `DIMGAP = 50` |
| 精度 | 0 位小数 | 毫米整数 |
| 文字样式 | `TZ_EN` | 尺寸数字走西文样式 |

换算规则：**模型字高 = 纸面字高 × 比例**。默认 3.5 × 100 = 350mm。

## 怎么改

```bash
# 1:50 出图，纸面 3.5mm → 模型 175mm
python3 scripts/image_to_calls.py plan.png --px-per-mm 0.16 \
    --scale 50 --paper-height 3.5 --ocr-text --out-dxf plan.dxf

# 纸面 2.5mm，1:100
python3 scripts/image_to_calls.py plan.png --px-per-mm 0.16 \
    --paper-height 2.5 --out-dxf plan.dxf
```

`plan_render.py` 的字高仍以 plan.json 里的 `dims.textheight` / `draw_text.height` 为准；
样式表提供的是没显式给字高时的默认值。

## 与天正的差距

- 输出的是标准 `TEXT` / `DIMENSION`，不是天正智能标注，不能在 T20 里联动墙门窗。
- 样式名与院标不同；有模板时应改用 `--template` 继承模板的 STYLE/DIMSTYLE/图层。
- 天正专用字体（如 `hztxt.shx`、`tssdchn.shx`）需要目标机器已安装，否则会被替换或显示异常。
- 缺少 SHX 时，AutoCAD 会提示替换字体；`txt.shx + gbcbig.shx` 的组合最不容易缺。
