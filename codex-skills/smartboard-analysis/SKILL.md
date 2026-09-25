---
name: smartboard-analysis
description: 通过本地 smartboard-mcp 二进制（stdio JSON-RPC，经 scripts/smartboard.py 直驱）完成 smartboard 数据分析看板与六段式洞察报告生成：加载数据 → 画像 → 行业技能包 → 候选池 → 公式校验 → HTML/JSON 看板写回。内置 run / report / call 三种命令。当任务涉及 smartboard 数据分析、看板/洞察报告生成、行业 KPI 建议时使用。数值必须来自工具返回，不得编造。
---

# SmartBoard 分析（smartboard-mcp 直驱）

前置：数据为本地文件绝对路径；只执行编译后的 `smartboard-mcp` 二进制（经 `scripts/smartboard.py`），不分析 MCP 服务源码（`.rs` / `Cargo.toml`）。

## 执行通道

Codex 的 `smartboard_*` MCP 工具延迟发现不可靠，统一走通道 B：用本技能内置脚本 `scripts/smartboard.py` 通过 stdio JSON-RPC 直驱同一个 `smartboard-mcp` 二进制（结果与 MCP 等价）。

- 脚本：`~/.codex/skills/smartboard-analysis/scripts/smartboard.py`
- 三种命令：`run`（快速出看板）、`report`（含六段式洞察报告）、`call`（细粒度工具，自定义编排）
- 同一会话内先 `load_data` 再调其他工具（stdio 进程内状态保持，跨进程无状态）。

## 核心铁律

1. 只执行编译后的二进制（经 `scripts/smartboard.py`）；不读 smartboard 源码、不调 `tool_search`、不探索环境。
2. 数值必须来自脚本/工具返回，绝不编造列名、数值或分析结果。
3. 图表只能从 `recommend_candidates` 返回的 `charts` 列表选，且传给 `build_*` 前必须摊平为扁平 spec；KPI 公式用 `SUM(A)/SUM(B)` 聚合形式，禁止裸别名。
4. `--out` / `--json-out` 给可写绝对路径。
5. 输出简体中文，数字加千分位；样本 <30 行或关键列缺失率 >30% 时先提示局限再分析。

## 快速命令（首选）

### run — 单次调用出看板

```bash
python3 ~/.codex/skills/smartboard-analysis/scripts/smartboard.py run \
  --path /绝对路径/数据.csv \
  [--skill-id industry-construction] \
  [--filter "region = 华东 & channel = 线上"] \
  [--title "看板标题"] \
  [--out /可写路径/xxx.html] \
  [--json-out /可写路径/xxx.json]
```

- `--path`（必填）：CSV/Excel 绝对路径（.csv/.tsv/.xlsx/.xls/.ods）。
- `--filter`（可选）：筛选表达式，直接传入 MCP 过滤，无需先生成子集文件；支持 `= != > < >= <= in ~(包含)`，`&` 表示且、`|` 表示或，列名大小写不敏感。例：`region = 华东 & channel = 线上 & revenue > 50000`。
- `--skill-id`（可选）：`industry-construction` / `industry-ecommerce` / `industry-education` / `industry-finance` / `industry-fund` / `industry-hr` / `industry-marketing` / `industry-real-estate` / `industry-saas` / `industry-sales`。
- `--title`（可选）；`--out`（可选，HTML，默认当前目录）；`--json-out`（可选，DashboardConfig JSON）。
- stdout JSON：`{ ok, total_rows, columns, kpi_count, chart_count, html_path, json_path, industry_boost }`。

### report — 含六段式洞察报告

```bash
python3 ~/.codex/skills/smartboard-analysis/scripts/smartboard.py report \
  --path /绝对路径/数据.csv \
  --skill-id industry-education \
  [--filter "region = 华东 & channel = 线上"] \
  [--title "看板标题"] \
  --out /可写路径/xxx.html \
  [--json-out /可写路径/xxx.json] \
  [--report-out /可写路径/xxx.md]
```

- `--skill-id`：report 模式的行业知识来源（推荐必填，否则无行业规则）。
- `--filter`（可选）：同 run，先按表达式过滤再出报告。
- `--report-out`（可选）：同时输出独立 Markdown 洞察报告。
- stdout JSON：`{ ok, total_rows, columns, kpi_count, chart_count, html_path, report_path, json_path, alert_count }`。

## 细粒度（call，自定义编排）

用 `scripts/smartboard.py call <tool> --json '<json>'`（省略 `--json` 时从 stdin 读 JSON）。标准流程（确认门禁 + 8 步）：

0. **确认门禁**：先确认行业、分析目标、受众/用途；目标含糊时向用户澄清，不自行脑补。
1. **数据质量诊断**：`smartboard_load_data`（`{"path":..., "filter":"region = 华东"}`，filter 可选，直接按表达式过滤后再分析）→ `smartboard_get_profile`；行数 <30、关键列缺失率高或日期范围异常先提示局限。
2. **画像**：`smartboard_get_profile` / `smartboard_get_schema`。
3. **行业知识**：`smartboard_list_skills` → `smartboard_get_skill`（`{"skill_id":...}`）；无合适行业可跳过。
4. **候选池**：`smartboard_recommend_candidates`（可选 `{"skill_id":...}`）→ 唯一图表/KPI 来源，返回 `{charts, kpis, industryBoost}`（含 score/reason）。
5. **框架建议（LLM 可选）**：`smartboard_propose_framework`（回传步骤 4 结果）；无 `LLM_CONFIG_PATH` 时跳过，走确定性回退。
6. **公式校验**：每个公式 KPI 调 `smartboard_validate_kpi_formula`。
7. **数值证据**：`smartboard_run_analysis`（`summary`/`timeseries`/`decile`），禁止从画像/候选池外自行估算。
8. **看板产物**：`smartboard_build_html_dashboard`（`title`/`kpis`/`charts`/`insights`）或 `smartboard_build_dashboard_config`。

`run` ≈ 步骤 1+4+8 自动化；`report` ≈ 步骤 1+3+4+6+7+8 + 六段式报告自动化。需要按条件分析时，在 `load_data` / `run_pipeline` 传入 `filter` 即可，如 `{"filter": "region in 华东,华南 & revenue > 50000"}`。

### 自定义看板（用户指定图形/KPI）

用户描述“要哪些图、哪些指标”时：`load_data`（可选 filter）→ `recommend_candidates` 取候选池 → 按描述从候选池选图 → 公式 KPI 过 `validate_kpi_formula` → `build_html_dashboard` / `build_dashboard_config`。

传给 `build_*` 的图表必须是**扁平 spec**（与管线内部口径一致），例如：

```json
{ "type": "bar", "title": "bar · product_line", "dimension": "product_line", "metric": "revenue", "agg": "sum", "selected": true, "topN": 10, "topNOthers": false }
```

- 禁止把候选池原始对象直接传入 `build_*`：其字段嵌套在 `config` 且无 `title`，渲染器读不到 → 图表空白。取候选后需摊平：`config` 字段提到顶层，并补 `type` / `title` / `selected` / `agg` / `topN` / `topNOthers`。
- 时间类图（line / timeseries / control_chart）顶层必须有 `dateColumn`；bar / doughnut / pie / ranking 顶层必须有 `dimension`；复杂图（radar / cluster / quadrant / sankey 等）按质量规则带完整字段。
- 单图生成可用 `smartboard_generate_chart`（bar / line / pie，按 type/dimension/metric/agg 描述；需先 `load_data`，同会话内有效）。

## 确定性回退（无 LLM 时）

- 框架 = 步骤 4 候选池 + 步骤 3 技能包口径（比率公式、预警规则、Go-to 图）。
- 比率 KPI 按技能包口径构造，必须过步骤 6 校验。
- 回复开头注明「LLM 框架未启用，本框架由推荐引擎候选池 + 行业技能包确定性组装」。

## 报告结构（六段式）

1. **核心结论**：先说结论，3-5 条要点。
2. **关键发现**：逐条给数值证据（来自 run_analysis / 候选池），标注环比、峰谷或分布。
3. **归因分析**：仅数据有证据（相关性、分段对比）时写；无证据只描述「相关/伴随」，不写因果。
4. **风险与局限**：样本量、缺失、口径不一致（产值≠收入≠回款等）明确提示。
5. **行动建议**：可执行、带优先级（P0/P1/P2）。
6. **数据附录**：列名/指标口径/公式说明。

## 图表/KPI 质量规则

- 每个图表带 reason；无理由不推荐。
- 传给 `build_*` 的图表一律扁平 spec（顶层 type/title/dimension 或 dateColumn/metric/agg/selected/topN/topNOthers）；候选池原始对象必须先摊平再传入，禁止嵌套 config 直传。
- 时间数据 → timeseries/line；占比 → doughnut/treemap；双指标量级差异大 → dual_axis；分布 → histogram/boxplot/decile；排名 → ranking/horizontal_bar。
- 复杂图表必须带完整 config：control_chart/时序需 dateColumn、quadrant 需 xMetric/yMetric、radar/cluster 需 metrics 数组。
- KPI 优先用候选池公式候选；比率 KPI ≤6 个，口径与技能包 semantics 一致。

## 交付

把返回的 `html_path`（有则连同 `report_path` / `json_path`）给用户；不要复述内部步骤。
