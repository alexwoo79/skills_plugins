---
name: duckdb-sql-analytics
description: >-
  Use for SQL-first dataset analysis with DuckDB or DuckDB Report Studio:
  typed ingestion, quality checks, window and time-series analytics, text
  normalization, queryable .duckdb delivery, Quarto reports, and reusable
  workflow packages. Do not use for generic web/dashboard development or
  Go/Wails source changes unless the task specifically needs the Studio
  workflow contract.
---

# DuckDB SQL Analytics

## 配套桌面程序

DuckDB Report Studio 提供工作区 UI、`duckdb-report-cli`、Quarto 渲染和发布包执行：

- 源码与使用说明：<https://github.com/alexwoo79/duckdb-report>
- 安装与运行时：<https://github.com/alexwoo79/duckdb-report/blob/main/docs/INSTALL.md>
- 桌面项目中的 Agent 协作说明：
  <https://github.com/alexwoo79/duckdb-report#agent-协作>

纯 CLI 分析可以不安装桌面程序；涉及工作区、Workflow、QMD 或发布包时优先使用配套
CLI，并阅读 [references/duckdb-report-integration.md](references/duckdb-report-integration.md)。

## 目标

所有转换、计算和结果持久化都通过 DuckDB SQL 完成。默认交付一个可再次查询的
`.duckdb` 数据库；CSV/Parquet 只在用户要求交换文件或展示层需要时生成。

## 选择执行面与调用边界

- **一次性分析、脚本化复跑**：直接使用 DuckDB CLI 和 `.sql` 文件。
- **DuckDB Report Studio 工作区、Quarto 报告、可迁移工作流或 ZIP 发布包**：
  优先使用 `duckdb-report-cli`；开发环境也可用 `duckdb_report <子命令>`。
- 应用无参数启动 Wails GUI；带子命令时进入 headless CLI。两者复用同一套工作区、
  DuckDB、Quarto 和 Workflow 执行层。
- Agent 可以读写工作区文件，并直接运行 CLI；不要再声称应用只有 GUI 入口，也不要
  尝试直接调用 Wails 前端绑定或伪造 RPC。
- 应用正在运行时不要直接用 CLI 读写同一个 `workspace.duckdb`；DuckDB 文件锁会导致
  失败或状态不一致。外部修改 `workflows/current.json` 也可能被界面中的旧状态覆盖。
  改用应用界面，或先退出应用再编辑和运行 CLI。

涉及 `duckdb-report`、Preset、Step、QMD、报告发布包或现有应用工作区时，阅读
[references/duckdb-report-integration.md](references/duckdb-report-integration.md)。
纯 CLI 分析无需加载该参考。

## 节省上下文的默认做法

- 每个会话只定位一次 DuckDB；之后复用 `$DDB`。
- 先跑聚合后的 schema/质量探针，不执行无条件 `SELECT *`。
- 默认只向对话返回少量摘要、计数、异常样本和文件路径；大结果留在 DuckDB 或导出文件。
- 把步骤写入小型 `.sql` 文件，用 `-f` 或 `.read` 执行；不要每轮重发整段数据或完整脚本。
- 普通 CLI 分析只读 `SKILL.md`；时间序列才加载时间序列 reference，发布纯数据库成果时
  才加载交付 reference，应用工作流任务才加载集成 reference。

## 定位 DuckDB

按顺序探测，找到后设置 `DDB`：

```bash
command -v duckdb \
  || test -x runtime/duckdb \
  || find "$HOME/.duckdb/cli" -type f -name duckdb -perm -111 2>/dev/null

"$DDB" --version
```

若当前项目或桌面应用自带 CLI，可把本次会话确认过的可执行路径赋给 `DDB`；不要把某个
开发机的绝对路径写进可复用流程。

`runtime/ui.duckdb_extension` 只服务于 notebook UI，与 CSV、Excel、Parquet 或业务
分析扩展无关，不需要在 SQL 分析中手动加载。

## 独立 SQL 分析工作流

默认在当前工作目录生成这些文件：

1. `00_setup.sql`：用 `read_csv` / `read_xlsx` 和 `all_varchar = true` 读入，再用
   `TRY_CAST`、`COALESCE` 显式定型；中文列名始终加双引号。
2. `01_quality.sql`：检查行数、键重复、空值、勾稽、越界和列错位；产出问题明细。
3. `02_kpi.sql`：生成统一口径的指标长表。
4. `03_analysis.sql` 到 `09_analysis.sql`：按任务增减维度、窗口、ABC、TopN、
   pivot、文本抽取等分析；不创建无用的空文件。
5. `99_publish.sql`：把最终结果物化为 `out/analysis.duckdb` 中的表，并写入运行元数据。
6. `run_all.sql`：用 `.read` 串起实际存在的步骤，供一键复跑。

先做质量检查，再解释业务结果。不要把视图或中间表数量当成分析成果。

## DuckDB Report Studio 工作流

应用工作流至少包含一个 Step。常用顺序为：

```text
import → sql → render → export
```

- Preset 类型：`datasource`、`sqlset`、`report`、`output`。
- Step 类型：`import`、`sql`、`render`、`export`。
- 参数覆盖顺序：运行参数 > Step overrides > Preset params。
- SQL Preset 支持 `mode=file` 与 `mode=inline`；`query_file` 决定界面展示的查询结果。
- Report Preset 的 `format` 只能是 `html` 或 `pdf`；应用把 `pdf` 映射到 Typst。
- `{{table}}` 由 Go 后端用主查询结果替换，不需要 R/Python。多个表使用
  `query_<name>` 参数与 `{{table_<name>}}` 占位符。
- `export` 前必须有成功的 `render`；发布包可包含报告、`analysis.duckdb` 快照和 QMD 源。

修改现有工作流前先读取 `workflows/current.json`，保留无关 Preset/Step 和用户参数。
使用稳定、唯一的 ID 和文件名，避免覆盖现有分析。工作流 JSON 只在结构层面做浅校验，
路径、预设语义和运行时依赖仍需自行检查。

常用 CLI：

```bash
duckdb-report-cli --workspace <workspace> status --json
duckdb-report-cli --workspace <workspace> doctor --json
duckdb-report-cli --workspace <workspace> workflow validate --json
duckdb-report-cli --workspace <workspace> workflow run --json
duckdb-report-cli --workspace <workspace> render \
  --template templates/report.qmd --format pdf --query-file sql/result.sql --json
```

自动化只依赖 stdout JSON 和退出码：`0` 成功、`1` 运行错误、`2` 参数/命令错误。
`doctor` 默认只读检查 Workflow、SQL 语法、QMD 占位符/资源和运行时；`--explain`
额外验证查询能否生成执行计划。先修复 error，再决定是否处理 warning/suggestion。

## 默认交付物

除非用户指定其他路径，最终文件为：

```text
out/analysis.duckdb
```

数据库至少包含可直接查询的结果表，例如：

```text
meta
quality_issues
kpis
analysis_*
```

外部 CSV/Excel 支撑的分析结果必须物化成表，不能只保存依赖外部文件的视图。
完成后用 DuckDB 重新打开该文件，确认表和行数可读，再报告路径与摘要。

创建数据库、命名结果表和验证交付物时，阅读
[references/duckdb-delivery.md](references/duckdb-delivery.md)。

## 场景路由

- **临时探数、一次性质量审计**：直接 CLI；交付可复跑 SQL 与问题明细表。
- **周期经营分析、参数化月报**：创建 DuckDB Report Studio 工作流，将时间范围放入
  `runtime`，SQL/QMD 用 `{{start_date}}` 等变量引用。
- **单报告多张表**：用 `query_*` + `{{table_*}}`；不要为每张表渲染一个报告。
- **需要 Python/R 图表或计算**：保留 SQL 预处理和报告表格注入，仅在 QMD 中使用必要
  脚本块；运行时缺依赖时先给替代方案或明确说明。
- **趋势、同比环比、异常与基线预测**：组合时间序列 reference 与 Report Studio
  `sql → render` 步骤，报告中保留期间完整性和方法说明。
- **交付可查询数据**：纯 CLI 默认写 `out/analysis.duckdb`；应用报告自动在 run 目录生成
  `analysis.duckdb`，也可以从应用导出整个工作库快照。
- **交付报告与数据包**：使用 `render → export`，开启 `include_report`、
  `include_database`、`include_source` 所需部分。
- **把已有 CLI 分析迁入应用**：优先复用已验证 SQL；把输入、SQL、QMD、输出拆成 Preset，
  再加入有序 Step，最后用 `workflow run --json` 验证。

## 时间序列

涉及趋势、补日期、移动窗口、同比环比、季节性、异常或预测时：

1. 阅读 [references/time-series-analysis.md](references/time-series-analysis.md)，先明确事件时间、
   时区、粒度、完整期间和缺失语义。
2. 先审计时间覆盖和重复键，再补完整日历轴。
3. 可用 [scripts/time-series-analysis.sql](scripts/time-series-analysis.sql) 作为起点；示例数据为
   [assets/sales_blueprint_sample.csv](assets/sales_blueprint_sample.csv)。
4. 同比/环比优先按日期键精确连接；不完整本期不与完整上期直接比较。
5. 预测默认称为基线，未经时间外回测不得称为正式模型。

## 约束

- 不写 Python/pandas 分析脚本；文件读取、转换、聚合、导出全部使用 DuckDB SQL。
- 大结果通过物化表、`COPY` 或聚合摘要处理，不把整表打印到对话。
- 图表所需明细先从 DuckDB 输出，再交给展示层；分析口径仍留在 SQL。
- 完成标准是可复跑 SQL、可查询 `.duckdb` 文件和经过验证的结果表。
- 应用工作流完成后还要确认 Step 顺序、引用文件存在、模板占位符与 `query_*` 参数一一对应。
- 在 Agent 中优先调用 CLI，不依赖 GUI 点击；只有需要在界面审阅时才交给用户操作。
- 修改工作流或模板后先运行 `doctor --json`；不要直接把静态建议当成业务口径改写依据。

需要具体 SQL 写法时，只按需读取
[references/sql-patterns.md](references/sql-patterns.md)，不要默认加载。
