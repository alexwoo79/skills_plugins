# 图片描图：线稿 → 墙线 / DXF

定位：**描图助手**。它把栅格图变成"候选几何"，语义（这条是墙、那是门）仍然靠人确认。
不要把扫描的照片、手绘草图直接送进来期待成套平面图。

```text
线稿图片 → 标定（两点 + 真实距离）→ 矢量化 → plan.json（墙中心线 + 厚度 + 洞口）
                                                 ├→ calls.json → cad_direct.py draw / script
                                                 └→ trace.dxf  → CAD 打开另存 DWG
                                     ↘ 预览 PNG（人工确认，这一步不能省）
```

## 依赖

```bash
pip install opencv-python-headless ezdxf     # 只在跑转换的这一侧需要，CAD 机器不用装
```

## 输入与预期

| 输入 | 效果 |
| --- | --- |
| CAD 导出的 PDF/PNG 线稿（建议 ≥150 dpi） | 好：墙线与洞口基本全中，见下面实测 |
| 扫描件（有噪声、轻微倾斜） | 可用但需修补：开 `--deskew --adaptive --despeckle`，召回下降一档 |
| 照片（透视、阴影、手绘） | 不要用：先纠正投影去阴影，或者干脆当底图人工描 |

## 标定（必需）

图像没有单位，不标定就只能输出像素坐标。两种方式：

```bash
# 方式一：直接给比例
--px-per-mm 0.2

# 方式二：在图上量两个点的像素坐标 + 真实距离（毫米）
--calibrate "120,80 1440,80" --real 12000

# 原点：哪个像素对应 CAD 的 (0,0)，默认取图像左下角
--origin 40,1640
```

原点搞错的后果是**整体平移一个常量**（实测少给原点会整体偏 200mm），所以预览里要
连带核对一个已知尺寸。

## 常用命令

```bash
# 一、结构化：出 plan.json（推荐），可直接接 wall_plan.py / cad_direct.py
python3 scripts/image_to_calls.py scan.png --mode walls \
    --px-per-mm 0.2 --origin 40,1640 \
    --out-plan plan.json --out-calls calls.json --preview check.png

# 二、保真：出合并吸附后的线段 DXF（墙对画成闭合矩形，其余进 A-TRACE 图层）
python3 scripts/image_to_calls.py scan.png --mode lines \
    --px-per-mm 0.2 --origin 40,1640 --out-dxf trace.dxf
```

落图沿用现有链路：

```bash
python3 scripts/cad_direct.py draw --calls calls.json
python3 scripts/cad_direct.py script --file plan.scr    # plan_to_scr.py 生成后
```

## 实测精度（可复现）

`tests/test_image_to_calls.py` 拿 `assets/example-two-bedroom/plan.json` 当真值，渲染成图
再跑矢量化。0.2 px/mm、线宽 2px 下实测：

| 输入 | 墙召回 | 洞口召回 | 轴位置误差 | 厚度误差 | 洞口宽度误差 | 洞口位置误差 |
| --- | --- | --- | --- | --- | --- | --- |
| 干净线稿（只画墙身） | 8/8，0 误检 | 10/10 | 0 mm | ≤10 mm | ≤10 mm | ≤5 mm |
| 完整图（含门窗表达） | 8/8，0 误检 | 10/10 | 0 mm | ≤10 mm | ≤125 mm | ≤62 mm |
| 扫描件（3% 噪点 + 0.5° 旋转） | 7/8，2 误检 | 8/10 | ≤10 mm | ≤10 mm | ≤375 mm | ≤192 mm |

误差量级约为半个线宽：线宽 2px、0.2 px/mm 时就是 5–10 mm。要提高精度就提高扫描分辨率。

```bash
python3 tests/test_image_to_calls.py        # 复现上面的数字
```

## 关键参数

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `--min-wall` / `--max-wall` | 80 / 500 mm | 认成墙的平行线间距范围，墙厚不在此区间就配不上 |
| `--min-opening` / `--max-opening` | 500 / 4000 mm | 洞口宽度范围 |
| `--door-min` / `--door-max` | 700 / 1000 mm | 仅按宽度猜门/窗的区间（**只是猜测**） |
| `--merge-tol` | 30 mm | 同一直线的合并容差 |
| `--join-gap` | 150 mm | 允许接上的小间断，要远小于最小洞口宽，否则会把洞口接掉 |
| `--grid` / `--grid-shift` | 10 / 15 mm | 网格吸附间距与允许位移 |
| `--angle-tol` | 3° | 轴线判定容差，超出归为斜线（不参与配墙） |

## 识别逻辑要点（为什么这样做）

- **墙 = 平行线对**：配对优先取重叠最长的组合、再取间距更大的，这样窗中线（只跨洞口）不会
  抢掉真正的两条墙面线。
- **洞口 = 用封边线定位**，而不是墙身缺口：窗的三线画法会把墙身缺口填平，只看缺口会漏掉所有窗。
  两条封边线之间还必须确有"洞口出处"——要么左边正好是某段墙身的终点、右边是另一段的起点（门），
  要么有一条窗线正好从左边跨到右边（窗）。
- **门/窗类型**：先按宽度猜（700–1000 记为门），再用"门扇线"证据修正（洞口边缘有垂直于墙、
  伸进房间且长度约等于门宽的线）。类型仍要人工确认。

## 已知局限（务必告诉用户）

1. **门/窗只是猜测**：600mm 宽可能是卫生间窗，也可能被记成窄门，看图确认。
2. **只认正交墙**：`--angle-tol` 之外的斜墙不参与配墙，会落到 `unpaired_lines` 里。
3. **不读标注文字**：尺寸数字、房间名不做 OCR，需要人工补（尺寸可用现有 `add_dimension`）。
4. **扫描件必然要修**：8/10 的洞口召回意味着平均每面墙要人工补一两个洞口。
5. **预览必须看**：识别结果先落 plan.json / calls.json，确认后再落图——与 skill 一贯的
   "先确认方案、再分步落图"一致。

## 一句话工作流

线稿 → 标定 → 矢量化 → **看预览确认** → 落图 → 在 CAD 里把猜测的部分手工补实。
