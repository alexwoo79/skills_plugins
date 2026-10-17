# 图纸识别基准集

`tests/run_image_benchmark.py` 可以对带真值的图片清单运行 `--mode walls`，输出逐样例
墙体/洞口精确率、召回率、类型准确率与最大几何误差。它用于比较版本与定位退化；合成集
和真实图纸必须分开报告，不能把合成结果当成真实准确率。

报告按 `category` 同时给出比率的样例宏平均（`rate_mean`）和按对象数汇总的 micro 比率
（`rate_micro`），以及几何误差均值与最差样例值。宏平均让每张图权重相同，micro 让每面墙/
每个洞口权重相同；请结合两者观察各类输入，不要只看 overall 汇总。

## 清单格式

```json
{
  "schema_version": 1,
  "cases": [
    {
      "id": "scan-residential-001",
      "category": "noisy-scan",
      "image": "images/scan-residential-001.png",
      "truth_plan": "truth/scan-residential-001.json",
      "calibration": {"px_per_mm": 0.2, "origin": [40, 1640]},
      "arguments": ["--deskew", "--adaptive", "--despeckle"],
      "thresholds": {
        "wall_recall_min": 0.8,
        "opening_recall_min": 0.7,
        "max_axis_error_mm": 80
      }
    }
  ]
}
```

- `image` 与 `truth_plan` 相对清单文件路径解析；真值使用 `wall_plan.py` 的 `plan.json` 结构。
- 每个案例必须显式给 `px_per_mm`；`origin` 是原图像素坐标 `[x, y]`，省略时为 `[0, 0]`。
- `arguments` 用于固定该案例的预处理开关/算法参数。模式、标定、输入与输出路径由 runner 管理，不能覆盖。
- `thresholds` 可选；阈值在积累足够样本并测出基线后再设，不要按合成样例结果直接外推。
- `category` 建议至少区分干净 CAD 导出、扫描/倾斜、实心填充墙图；只纳入当前承诺支持的正交平面，超出范围的输入应单独标记。

运行：

```bash
python3 tests/run_image_benchmark.py path/to/manifest.json --pretty
```

需要逐例目检或保存失败案例时，添加 `--artifacts-dir path/to/review-output`；每例会保留
识别后的 `plan.json` 和 PNG 预览，路径也会写进 JSON 报告。默认不传时，临时 plan 会在
运行结束后清理。

空案例清单会报错，不会返回空基准通过。退出码 `0` 表示所有案例成功且满足已配置门槛；
退出码 `1` 表示运行错误或阈值失败。报告中的几何误差是已匹配对象的最大绝对误差；召回
分母包含没有匹配到墙上的真值洞口。

## 数据要求

真实案例需要可复核的原始图片、标定信息和人工校验的墙/洞口真值。记录来源类别、分辨率、
扫描倾角、预处理选项与标注复核人；按图纸/项目划分调参集和留出集，避免同一项目的近似
图纸同时进入两边。敏感图纸先脱敏并获得使用许可；默认不要把客户原图提交到仓库。

仓库目前没有真实图纸评测集。新增真实样例前应先确认脱敏与许可，并单独报告其结果；目前
已有的两居室合成真值只验证可控场景下的回归，不代表真实扫描件表现。