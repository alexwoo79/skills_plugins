# DuckDB Report Studio 集成

只有任务涉及 DuckDB Report Studio 工作区、Preset/Step、Quarto 报告或发布包时读取本文件。
纯 DuckDB CLI 分析不需要这些约束。

配套项目文档：

- 源码与 README：<https://github.com/alexwoo79/duckdb-report>
- 安装、CLI 与运行时：<https://github.com/alexwoo79/duckdb-report/blob/main/docs/INSTALL.md>
- 桌面项目 Agent 协作说明：
  <https://github.com/alexwoo79/duckdb-report#agent-协作>

## 1. 应用能力与边界

DuckDB Report Studio 是本地 Wails 桌面工作台，核心能力包括：

- 将 CSV、JSON、Parquet、Excel 导入 `workspace.duckdb`，将外部 `.duckdb` 作为 catalog 附加。
- 执行多语句 SQL 与 `.read` 脚本，展示结果并导出 CSV。
- 以 `datasource → sqlset → report → output` Preset 和
  `import → sql → render → export` Step 组成可复跑工作流。
- 用 Quarto 渲染 HTML/PDF，向 QMD 注入一张或多张 SQL 结果表。
- 每次报告生成 `analysis.duckdb` 快照、QMD、HTML/PDF 和日志。
- 导出/导入包含引用文件的完整工作流 ZIP，导出包含报告与数据的发布 ZIP。

应用有两种入口：

- `duckdb-report-cli`：独立 headless CLI，优先用于 Agent、脚本和 CI。
- `duckdb_report`：无参数启动 Wails GUI；带子命令时使用同一 CLI 命令树。

CLI 与 GUI 复用相同的 database、renderer、runner 和 workflow bundle 实现。应用仍不提供
HTTP API；Agent 应调用 CLI，而不是直接调用 Wails 前端绑定或伪造 RPC。

```text
duckdb-report-cli status
duckdb-report-cli doctor
duckdb-report-cli query
duckdb-report-cli import
duckdb-report-cli render
duckdb-report-cli workflow validate
duckdb-report-cli workflow run
duckdb-report-cli workflow import
duckdb-report-cli workflow export
```

## 2. 定位工作区

优先级：

1. `DUCKDB_REPORT_HOME` 环境变量。
2. `settings.json` 中保存的 workspace。
3. 默认目录：

```text
macOS:   ~/Library/Application Support/duckdb_report
Windows: %APPDATA%\duckdb_report
Linux:   ${XDG_CONFIG_HOME:-~/.config}/duckdb_report
```

`settings.json` 与默认工作区同目录。若环境变量存在，它覆盖设置文件。

标准工作区：

```text
workspace.duckdb
sources.json
data/
sql/
templates/
  assets/
workflows/
  current.json
runs/<run-id>/
exports/
examples/monthly_sales/
```

开始编辑前先检查：

```bash
printf '%s\n' "${DUCKDB_REPORT_HOME:-}"
test -f workflows/current.json && jq . workflows/current.json
find sql templates data workflows -maxdepth 3 -type f | sort
```

不要无条件覆盖现有文件。对已有工作流使用新的 Preset/Step ID；对 SQL/QMD 使用带任务语义
的新文件名；需要替换时先向用户确认。

## 3. 工作流模型

顶层结构：

```json
{
  "id": "monthly-analysis",
  "name": "月度经营分析",
  "description": "导入、分析、渲染并打包",
  "runtime": {
    "start_date": "2026-01-01",
    "end_date": "2026-12-31"
  },
  "presets": [],
  "steps": []
}
```

有效 Preset 类型与 Step 类型：

| Preset `type` | 对应 Step `kind` | 用途 |
| --- | --- | --- |
| `datasource` | `import` | 数据文件导入或 DuckDB 附加 |
| `sqlset` | `sql` | 文件或内联 SQL 执行 |
| `report` | `render` | QMD HTML/PDF 渲染 |
| `output` | `export` | 报告与数据 ZIP 发布 |

每个 Step 必须引用存在的 `presetId`。`enabled=false` 的步骤会被跳过。
常用最小链路：

```text
import → sql → render → export
```

只有 SQL 与报告时省略 `import`/`export`；需要最终 ZIP 时必须包含 `render`，因为
`export` 使用最近一次成功报告的 run 目录。

参数优先级固定为：

```text
workflow.runtime > step.overrides > preset.params
```

`{{name}}` 是纯文本模板替换，适用于 SQL、QMD、路径和查询。它不会 SQL 转义，也不会
自动加引号。动态值来自用户时，优先通过受控参数约束类型、枚举或日期格式。

## 4. Preset 参数契约

### Datasource / import

```json
{
  "kind": "csv",
  "path": "data/sales.csv",
  "table": "sales",
  "sheet": ""
}
```

- `kind`：`csv`、`json`、`parquet`、`xlsx`、`xlsm`、`duckdb` 或 `db`。
- `path`：相对工作区路径或允许访问的绝对文件路径。
- `table`：导入目标表名，或外部 DuckDB 的 catalog alias。
- `sheet`：仅 Excel 使用；空值表示第一个 sheet。
- CSV/JSON/Parquet/Excel 导入会重建同名表；外部 DuckDB 只执行 `ATTACH`，不复制数据。

导入实现与 CLI 的 `read_xlsx(..., all_varchar=true)` 不完全相同：应用用 Excel 解析器
读取首个/指定 sheet，再暂存为 CSV 导入。复杂混类型清理仍应作为后续 SQL 步骤完成。

### SQL set / sql

文件模式：

```json
{
  "mode": "file",
  "file": "sql/monthly_analysis.sql",
  "query_file": "sql/monthly_result.sql",
  "limit": "500"
}
```

内联模式：

```json
{
  "mode": "inline",
  "inline_sql": "CREATE OR REPLACE TABLE kpis AS SELECT ...;\nSELECT * FROM kpis;",
  "query_file": "",
  "limit": "500"
}
```

- `file` 内容通过 `.read` 展开，相对路径以 SQL 文件目录为基准。
- `query_file` 可选；设置后，SQL Step 返回该查询结果作为界面展示结果。
- 不设置 `query_file` 时返回执行 SQL 的查询结果；多语句脚本需要明确展示结果时，
  应单独提供 `query_file`，不要依赖多语句驱动的返回行为。
- `limit` 只限制返回对话/界面的行数，不限制 SQL 内物化表的行数。
- 多语句 SQL 可以建立表/视图；结果是否截断不能说明数据库交付是否完整。

### Report / render

```json
{
  "template": "templates/monthly_report.qmd",
  "format": "html",
  "query": "SELECT * FROM monthly_result ORDER BY month",
  "query_regions": "SELECT * FROM region_summary ORDER BY revenue DESC"
}
```

- `template` 必须位于工作区。
- `format` 仅接受 `html`、`pdf`；`pdf` 由应用转成 Quarto Typst。
- `query` 填充 `{{table}}`。
- `query_<name>` 填充 `{{table_<name>}}`，例如 `query_regions` →
  `{{table_regions}}`。
- `title`、`start_date`、`report_font` 等参数可由 `runtime` 或 Step overrides 覆盖。
- QMD 可以声明 `params:` 默认值；运行参数优先。
- 只有存在 Jinja 风格 R/Python 代码块时才需要 `Rscript` 或 `jupyter`。纯
  `{{table}}` 报告不依赖脚本运行时。

报告输出目录：

```text
runs/<run-id>/
  report.qmd
  report.html 或 report.pdf
  analysis.duckdb
  quarto.log
  assets/
```

`analysis.duckdb` 是报告开始时的完整工作库快照，可独立打开，不依赖
`workspace.duckdb` 或原始 CSV。

### Output / export

```json
{
  "filename": "monthly-report",
  "directory": "exports",
  "include_report": "true",
  "include_database": "true",
  "include_source": "true"
}
```

- `directory` 必须是工作区相对路径。
- 最终文件名会追加 `-<workflow-run-id>.zip`。
- `filename` 仅稳定保留 ASCII 字母、数字、`-`、`_`；中文等字符会被折叠，建议提供英文 slug。
- `include_report` 收集 run 目录中的 HTML/PDF。
- `include_database` 收集 `analysis.duckdb`。
- `include_source` 收集 `report.qmd` 与 `assets/`。
- 三项全部为 false 会报错；`export` 之前没有成功 `render` 也会报错。

## 5. Agent 推荐调用流程

### CLI 调用契约

优先解析 `duckdb-report-cli`；开发目录中也可以使用 `duckdb_report <子命令>`。

```bash
duckdb-report-cli --workspace /path/to/workspace status --json
duckdb-report-cli --workspace /path/to/workspace doctor --json
duckdb-report-cli --workspace /path/to/workspace doctor --explain --json
duckdb-report-cli --workspace /path/to/workspace query --file sql/q.sql --json
duckdb-report-cli --workspace /path/to/workspace workflow validate --json
duckdb-report-cli --workspace /path/to/workspace workflow run --json
duckdb-report-cli --workspace /path/to/workspace render \
  --template templates/report.qmd --format pdf \
  --query-file sql/result.sql --json
```

- 显式传 `--workspace`；未传时依次使用 `DUCKDB_REPORT_HOME` 和保存的工作区。
- 自动化使用 `--json`。stdout 只放 JSON，步骤进度和错误写 stderr。
- 退出码：`0` 成功，`1` 运行错误，`2` 参数或命令使用错误。
- GUI 打开时不要运行会写 `workspace.duckdb` 的 CLI 命令；先关闭 GUI。
- `workflow run --file` 只执行给定 JSON，不自动写入 `current.json`；需要设为当前工作流时
  使用 `workflow import --file`。
- `doctor` 只读检查 Workflow、SQL 语法、QMD 变量/表格/资源、Step 顺序和运行时；
  `--explain` 使用当前工作库验证查询绑定，不应代替业务结果等价比对。

### 场景 A：在已有应用工作区做一次性分析

1. 读取现有 SQL、表清单和 `current.json`，不要先改工作流。
2. 新建 `sql/<task>.sql`，先用 `CREATE OR REPLACE TABLE` 或视图形成可复跑口径。
3. 用 `duckdb-report-cli query --file sql/<task>.sql --json` 执行；应用打开时不要在外部
   并发写入。
4. 给出少量摘要和异常样本；大结果留在 DuckDB。
5. 用户需要长期复用时，再把 SQL 接入工作流。

### 场景 B：创建周期经营分析工作流

产出：

- `sql/<metric>_analysis.sql`
- `sql/<metric>_result.sql`
- `templates/<metric>_report.qmd`
- `templates/assets/report.css`（需要自定义样式时）
- 更新后的 `workflows/current.json`

步骤形状：

```text
import(data) → sql(analyze + query_file) → render(html/pdf) → export(zip)
```

把统计区间放 `runtime`，SQL/QMD 中用 `{{start_date}}`、`{{end_date}}`。不要让 Agent
每期复制整套 SQL。

```bash
duckdb-report-cli --workspace <workspace> workflow validate --json
duckdb-report-cli --workspace <workspace> workflow run \
  --param start_date=2026-01-01 \
  --param end_date=2026-12-31 \
  --json
```

### 场景 C：单报告输出多张表

SQL Preset 只生成物化结果。Report Preset 增加：

```json
{
  "query": "SELECT * FROM kpi_summary",
  "query_regions": "SELECT * FROM region_summary",
  "query_products": "SELECT * FROM product_ranking"
}
```

QMD：

```markdown
{{table}}

## 区域

{{table_regions}}

## 产品

{{table_products}}
```

每个 `{{table_<name>}}` 必须有对应的 `query_<name>`；主 `{{table}}` 使用 `query`。
缺少任一参数时渲染应失败，不要悄悄生成空表。

### 场景 D：SQL 预处理 + Python/R 报告

优先把过滤、连接、聚合、窗口和可审计指标放在 SQL。QMD 脚本块只负责：

- 读取已物化结果；
- 绘图；
- 不能合理用 SQL 表达的文档计算；
- 最终版式。

Python 报告依赖 Jupyter、DuckDB、pandas、matplotlib；R 报告依赖 DBI、duckdb、dplyr、
ggplot2、knitr、scales、rmarkdown。缺少运行时时，优先改成 `{{table}}` 表格报告或让用户
安装依赖，不要静默跳过执行。

### 场景 E：质量审计与问题回传

输出至少包括：

- `quality_summary`：行数、键重复、空值、勾稽失败、越界、列错位；
- `quality_issues`：逐行问题明细、严重度、来源字段；
- `clean_*` 或修正映射表。

报告中先说明质量问题，再给业务结论。不要把被脏数据污染的趋势当成业务变化。

### 场景 F：时间序列月报

复用 [time-series-analysis.md](time-series-analysis.md) 的口径，物化：

- `ts_coverage_audit`
- `ts_monthly_comparison`
- `ts_anomaly_scan`
- `ts_forecast_3m`（如用户需要）

Report Preset 用多个 `query_*` 输出覆盖、同比环比、异常和归因。预测只能标记为基线，
除非完成时间外回测。

### 场景 G：发布给非技术用户

使用 `render → export`，默认可全部包含：

```json
{
  "include_report": "true",
  "include_database": "true",
  "include_source": "true"
}
```

交付说明必须区分：

- `report.html/pdf`：给人看的报告；
- `analysis.duckdb`：可再次查询的数据快照；
- `report.qmd` + `assets/`：可审计和可复跑的源。

### 场景 H：迁移已有 CLI 分析

1. 保留已验证 SQL，不改变业务口径。
2. 将输入文件路径转成 `datasource.path`。
3. 将主 SQL 与展示查询拆成 `file` 与 `query_file`。
4. 将最终表查询映射到 Report Preset 的 `query`/`query_*`。
5. 将 CSV/Parquet 导出替换为应用 `output`/`export`，数据库以 `analysis.duckdb` 为准。
6. 用样例参数对照旧 CLI 结果，再运行
   `duckdb-report-cli workflow run --json` 验证完整链路。

## 6. 工作流包与迁移

应用导出的工作流 ZIP 包含：

- `workflow.json`
- 数据源 Preset 引用的文件；
- SQL、查询文件及其同目录 `.read` 依赖；
- QMD 与相邻 assets；
- 其他被 `path`、`file`、`query_file`、`template` 参数引用的工作区文件。

导入时文件解压到 `workflows/imports/<id>/`，包内 `assets/...` 路径自动改写，并设置为
当前工作流。不要手工构造不完整的 ZIP。CLI：

```bash
duckdb-report-cli --workspace <workspace> workflow export --output /tmp/workflow.zip --json
duckdb-report-cli --workspace <workspace> workflow import --file /tmp/workflow.zip --json
```

JSON 工作流也可以通过 `workflow import --file workflow.json` 直接设为当前。

## 7. 验证清单

- `duckdb-report-cli doctor --json` 无 error。
- 可选运行 `doctor --explain --json`，确认报告和结果查询能在当前工作库绑定。
- `jq . workflows/current.json` 通过。
- 每个 Step 的 `presetId` 存在，ID 无重复。
- 每个 `path`/`file`/`query_file`/`template` 都能从工作区解析。
- SQL 已在样例数据库或工作库副本上跑通。
- 所有 `{{variables}}` 都有 runtime、Step override 或 Preset 默认值。
- 每个 `{{table_*}}` 有对应 `query_*`。
- `format` 是 `html` 或 `pdf`。
- `export` 前有 `render`，输出目录是工作区相对路径。
- 用户要求 PDF 时确认 Typst/Quarto 可用；需要 R/Python 时确认对应运行时可用。
- 自动化执行使用 `workflow run --json`；需要人工审阅时再打开 GUI。
