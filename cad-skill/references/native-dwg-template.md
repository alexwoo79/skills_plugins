# 原生 DWG 模板与标准块

正式出图优先使用原生 DWG 链路，不生成 DXF：

```text
模板 .dwt / .dwg
  → 新建或打开工作图
  → 语义平面直接执行到 CADCraft
  → 门、窗、柱、家具、楼梯按名称插入标准块
  → 保存工作 DWG
```

## 模板

- `.dwt`：用 `new {template}` 新建未命名图纸，模板文件不会被覆盖。
- `.dwg`：先 `app.open`，再立即保存为独立工作 DWG，模板文件保持只读。
- CADCraft 0.7 不随安装包提供 `.dwt`；可用内置 `cadcraft-cli sample floorplan`
  生成示例 DWG，再另存为测试 `.dwt`。

## 命令

```bash
python3 scripts/plan_native.py plan.json \
    --template /path/template.dwt \
    --output /path/work.dwg \
    --labels labels.calls.json \
    --clear \
    --screenshot /path/check.png
```

`--clear` 只清除模板示例图元，保留图层、线型、文字样式、标注样式和布局。

使用真实模板标准块时指定块名：

```bash
python3 scripts/plan_native.py plan.json \
    --template /path/template.dwt \
    --output /path/work.dwg \
    --door-block-ccw DOOR_900_CCW \
    --door-block-cw DOOR_900_CW \
    --window-block WINDOW_1200 \
    --no-create-missing-blocks
```

模板缺块且允许创建时，脚本会建立单位为 1000 mm 的测试门窗块，仅用于验证流程。

## 块插入规则

- 模板已有块时，`insert` 只传 `name`，不要重复传 `file`。
- `file` 只用于首次定义外部 DWG 块库中的块；重复用文件插入会重定义同名块，
  可能重置基点和描述。
- 门块基点放在合页点，按门宽缩放，按墙方向加合页侧旋转。
- 窗块按 `(窗宽, 墙厚)` 非等比缩放，插入点为洞口起点。
- 不炸开块；动态参数用 `blocks.reference.set`，属性用 `attedit`。

## 验收

- `stats` 中存在 `Block Reference`
- `block_count` 的门、窗数量与 `plan.json` 洞口数一致
- 保存后重新打开，块定义名称、基点、描述、动态状态仍在
- 门窗块没有被炸成 `Line/Arc`
- 模板图层和标注样式未被替换成通用默认样式
