---
name: building-metrics-calculation
description: 按建筑面积规则与城市指标细则计算完整方案指标。由本地规则引擎直驱（本仓库 design_metrics.py 的 DesignMetrics，经 scripts/metrics_cli.py 调用）：读取 DWG 导出的面积线 CSV 与三张 Excel 输入，按上海/长沙/合肥/贵阳四套规则算出计容面积、容积率、建筑密度、地库、户数等指标，支持参数试算、自定义奖励/配套楼栋、两项目对比与 Excel 导出。当任务涉及建筑面积测算、计容与容积率核算、报批指标表生成、方案指标试算或项目指标对比时使用。数值必须来自引擎计算，不得编造。
---

# building_metrics 方案指标计算

本 skill 封装本仓库的计算引擎，不重写规则。所有数值来自 `DesignMetrics`，
对话里不做任何手算、估算或口径替换。

## 铁律

1. **数值只能来自引擎**。任何指标必须经由 `metrics_cli.py` 算出；不要凭面积线自行加减出结果。
2. **先校验再计算**。数据契约不通过时结果不可信，先跑 `check`，把高危项交给用户处理。
3. **静默按 0 的项必须披露**。图层里的系数key 未在 `系数key.xlsx` 定义、楼栋属性与面积线覆盖不一致，
   这些情况引擎不会报错，只会按 0 计。引擎会把它们写进 **`数据诊断` sheet**，
   交付时必须一并说明。诊断只做提示，不改变任何指标结果。
4. **口径不能换城市**。四套城市规则的计容逻辑不同，同一个项目换个城市跑出来的数没有可比性。
5. **对比只在同城市内做**。`compare` 会拒绝跨城市对比。
6. 输出简体中文，数值加千分位。

## 运行环境

需要一个装了 polars、fastexcel、xlsxwriter、openpyxl 的 Python。在本仓库内开发时用仓库的 `.venv`
（不存在则先在仓库根目录 `uv sync`）；以插件形式独立安装时，用插件自带的
`<插件>/.venv`（`install.sh` 会准备好）。

这套环境同时适配 `keyan-calculation`（可研测算）skill —— 那个 skill 只需要 openpyxl：

```bash
/home/alex/Documents/github/building_metrics/.venv/bin/python \
    /home/alex/Documents/ChatGPT/可研测算skill/keyan-calculation/scripts/keyan.py report \
    --input 项目.json --outdir 输出目录
```

```bash
cd /home/alex/Documents/github/building_metrics
PY=.venv/bin/python
CLI=skills/building-metrics-calculation/scripts/metrics_cli.py
```

### 引擎从哪来

`metrics_cli.py` 按下列顺序定位计算引擎，命中即用：

1. `--repo /路径` 参数
2. 环境变量 `BUILDING_METRICS_REPO`
3. 从 skill 目录向上查找 `design_metrics.py`（skill 装在仓库内时的路径）
4. 同目录 `skill_config.json` 的 `"repo"`
5. 包内快照 `<插件>/engine`（独立安装时走这一步）

每次 `calc` / `check` / `compare` 的输出都带 **`引擎来源`** 字段，标明用的是「外部仓库」还是
「内置快照」。同一台机器上两份引擎并存时，先看这个字段再判断结果出自哪一份——它不是装饰，
是发现引擎漂移的唯一线索。

## 快速命令

```bash
$PY $CLI cities                                            # 列出四城市规则要点与样例数据
$PY $CLI check  --project data/sh [--city sh]              # 数据契约校验
$PY $CLI calc   --project data/sh --city sh [--ratio 1.2] [--factor 1.03] \
               [--bonus 1,2,3] [--facility 5,6] [--outdir 输出目录]
$PY $CLI compare --a data/sh --b data/sh_change --city sh [--outdir 输出目录]
```

| 命令 | 作用 |
| --- | --- |
| `cities` | 四城市规则入口与计容口径要点，用于确认该项目该用哪套规则 |
| `check` | 面积线图层合法性、系数key 与楼号交叉命中、1F 覆盖、补充信息必需键；退出码 1 表示有高危 |
| `calc` | 跑指标，stdout 输出 JSON（指标、楼栋清单、奖励/配套楼栋、数据诊断、数据校验），`--outdir` 时导出三个 xlsx：**指标表（含「指标表」与「数据诊断」两个 sheet）**、楼栋面积表、户配汇总 |
| `compare` | 同城市两项目对比，输出差异明细，可导出对比表 |

参数默认值与界面一致：容积率限值 1.2、奖励系数 1.03（界面区间 1.0—1.2）。
`--bonus` / `--facility` 用于试算：改奖励楼栋或配套楼栋名单后引擎会重算底表。
注意 `奖励后计容面积` 不随奖励系数变化，试算要看 **`计容面积`** 和 **`奖励面积`**；
奖励系数为 1.0 时改奖励楼栋名单不会改变计容面积。

## 标准工作流

1. 确认项目目录：需含 `Buildings_csv/` 与 `楼栋属性.xlsx`、`系数key.xlsx`、`补充信息.xlsx`。
2. 确认城市：用 `cities` 对照项目所在地，四套规则不可混用。
3. `check`：有高危项先解决；警告项（如补充信息缺键、楼栋属性与面积线覆盖不一致）要在交付时说明。
4. `calc`：按需调 `--ratio` / `--factor`，试算不同奖励楼栋时用 `--bonus`。
5. 交付时说明：所用城市规则、容积率与建筑密度、计容面积、总户数、地库与车位、
   `数据校验` 里未通过或静默按 0 的项、以及必须人工复核的专项。

## 数据诊断 sheet

指标表工作簿的第二个 sheet，由 `data_loader.build_diagnostics` 生成，字段：
`级别 / 类别 / 对象 / 涉及面积 / 占面积线比例% / 影响 / 处置建议`。覆盖六类情况：

| 级别 | 类别 | 含义 |
| --- | --- | --- |
| 提示 | 未匹配系数key | 面积线里的 key 未在系数表定义，四类面积全部按 0 计，给出涉及面积与占比 |
| 高危 | 楼栋属性缺该楼号 | 类型/奖励/精装为空，配套识别与奖励折减不生效 |
| 警告 | 楼栋属性有楼号但无面积线 | 户数全额计入、面积不覆盖，两个口径并列 |
| 警告 | 地上楼栋无 1F 图层行 | 不计入 `floor=1f` 的基底面积指标 |
| 提示 | 纯地下楼栋（无 1F 属正常） | 如地库，正常情形，无需处理 |
| 提示 | 系数表存在程序未读取的列 | 如 `超低能耗面积`，对应口径尚未实现 |

报告结论前先看这张表：出现「高危」时不要把指标当作可交付结果。

## 数据契约要点

面积线图层名就是主键，格式两种：

```
AA/AB-楼型-楼层-系数key      例：AB-GC-BZC-设备平台
AA/AB-楼型-系数key           例：AB-LP-设备平台（楼层记为 --）
```

`系数key.xlsx` 给每个 key 配 计容/物理/销售/赠送 四个系数，引擎据此把「面积线」换算成四类面积。
key 不在表里 → 该行四类面积全为 0。若该 key 是「主体」的子项分项（如 `套内`），按 0 计才正确，
否则会重复计算 —— 判断依据是该项是否已被 `主体` 覆盖。

完整字段与四城市规则差异见 [references/数据契约.md](references/数据契约.md)
与 [references/城市规则与口径.md](references/城市规则与口径.md)。

## 常见情况

- **`check` 报缺 `补充信息.xlsx` 的键**：该键缺失时引擎按 0 参与计算（`用地面积` 缺失会直接抛异常）。
  涉及分母的指标要格外当心，例如缺 `商业基底面积` 会让建筑密度偏小。
- **`check` 报楼栋属性有楼号但无面积线**：户配仍全额计入总户数，面积类指标却只覆盖有面积线的楼栋，
  两个口径会并列出现在同一张指标表里。这是引擎不校验覆盖范围导致的，交付时必须单独说明。
- **某楼栋没有 1F 图层行**：依赖 `floor=1f` 的指标（建筑基底面积、住宅基底面积）不计入该楼栋。
- **指标表里看不到某项**：`metrics_output` 会过滤掉数值为 0 的指标，所以「算出来是 0」和「没算」
  在输出里分不出来。需要区分时直接查 `--outdir` 导出的楼栋面积表。
- **需要改奖励系数或奖励楼栋**：用 `--factor` / `--bonus`，不要手工改结果。
- **规则本身要改**（如合肥的 0.97/0.03 政策系数）：改 `city_rules.py` 后重跑 `scripts/smoke_test.py`，
  快照会告诉你有没有改坏别的城市。

## 测试

一条命令跑完整自测（12 项，覆盖仓库自带测试 + CLI 接口契约 + 异常路径）：

```bash
$PY $CLI_DIR/self_test.py
```

`self_test.py` 会依次跑：

1. `scripts/validate_project_data.py` —— 数据契约校验（5 个项目应无高危）
2. `scripts/smoke_test.py` —— 四城市指标快照回归（容差 0.01）
3. CLI 契约：`cities` 列四城市、`check` 正常数据退出码 0、`calc` 出关键指标、
   指标表含「指标表 + 数据诊断」两个 sheet、同城市对比可运行
4. 异常路径：非法城市、跨城市对比、目录不存在、缺陷数据（非法图层名/未匹配系数key/
   楼栋属性缺楼号/缺分母键）应被 `check` 抓出并返回 1、`calc` 应给出可操作错误而非 traceback

改动引擎或 CLI 后跑它；新增城市规则时同步更新 `metrics_snapshot.json`。
