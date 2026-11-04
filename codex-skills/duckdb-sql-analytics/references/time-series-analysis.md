# DuckDB 时间序列分析

适用范围：销售、流量、监控指标等带时间字段的数据，需要做趋势、滚动指标、周期对比、季节性、异常检测或基线预测。所有步骤都用 SQL 完成。

参考文章：

- <https://www.duckdblab.org/zh/post/duckdb-time-series-guide/>

可运行脚本：

- [../scripts/time-series-analysis.sql](../scripts/time-series-analysis.sql)

验证版本：DuckDB CLI 2.0.0。

示例数据：

- [../assets/sales_blueprint_sample.csv](../assets/sales_blueprint_sample.csv)

在 skill 目录中直接运行：

```bash
duckdb :memory: -f scripts/time-series-analysis.sql
```

传入其他 CSV：

```bash
DUCKDB_TS_SAMPLE=/absolute/path/data.csv \
  duckdb :memory: -f scripts/time-series-analysis.sql
```

如果目标文件列名不同，调整脚本中 `ts_raw` 的字段映射。

## 1. 先定义分析口径

时间序列分析之前，先确认以下口径：

1. **事件时间**：业务发生时间，不是入库时间或更新时间。
2. **业务时区**：带时区的时间戳先统一到业务时区，再截日、周、月；跨时区系统不能默认用 UTC 日期。
3. **分析粒度**：订单级、日级、周级或月级。窗口函数必须建在唯一时间粒度的聚合结果上。
4. **聚合方式**：销售额用 `SUM`，价格用 `AVG`，不同粒度不能混用。
5. **期间定义**：自然月、财月、周一到周日、自定义结算周期，以及同比/环比究竟比较哪两个区间。
6. **时间边界**：区间通常采用左闭右开 `[start, end)`，避免月末和次日重复；首末期间还要标记是否完整。
7. **比较语义**：同比/环比同时输出现值、基期值、绝对差和百分比；基期为 `NULL` 或 `0` 时百分比必须为 `NULL`。

样本数据的结构：

| 项 | 值 |
|---|---|
| 行数 | 5,200 |
| 时间字段 | `OrderDate`，类型 `DATE` |
| 范围 | 2024-01-01 至 2025-12-31 |
| 活跃日期 | 731 天 |
| 维度 | `Region`、`City`、`Salesperson`、`Customer`、`Category`、`Product` |
| 指标 | `Quantity`、`UnitPrice`、`Discount`、`Sales`、`Cost`、`Profit` |
| 粒度 | 一行一条订单记录；样本中的 `OrderID` 唯一 |

样本 SHA-256：

```text
ac16f3f1d92adde6038c470f51bd0ca35bc2d2b0b840497912738254a81f000e
```

先验证：

```sql
SELECT
    min(OrderDate) AS min_date,
    max(OrderDate) AS max_date,
    count(*) AS rows,
    count(DISTINCT OrderID) AS orders,
    count(DISTINCT OrderDate) AS active_days
FROM read_csv('assets/sales_blueprint_sample.csv', header = true);
```

样本中每天都有记录，但分地区后并非每个地区每天都有记录。因此：

- 全局日趋势不存在缺日；
- 分区日趋势需要补齐日期；
- 月序列共 24 个完整月份。

## 2. 类型化读取与日聚合

`read_csv` 自动推断可能把金额识别成 `DOUBLE`。做财务指标时显式转成 `DECIMAL`：

```sql
CREATE OR REPLACE VIEW ts_raw AS
SELECT
    OrderID AS order_id,
    OrderDate AS order_date,
    Region AS region,
    Customer AS customer,
    Product AS product,
    cast(Quantity AS INTEGER) AS quantity,
    cast(UnitPrice AS DECIMAL(18, 2)) AS unit_price,
    cast(Discount AS DOUBLE) AS discount,
    cast(Sales AS DECIMAL(18, 2)) AS sales,
    cast(Cost AS DECIMAL(18, 2)) AS cost,
    cast(Profit AS DECIMAL(18, 2)) AS profit
FROM read_csv(
    'assets/sales_blueprint_sample.csv',
    header = true,
    types = {
        'OrderDate': 'DATE',
        'Quantity': 'INTEGER',
        'UnitPrice': 'DECIMAL(18,2)',
        'Discount': 'DOUBLE',
        'Sales': 'DECIMAL(18,2)',
        'Cost': 'DECIMAL(18,2)',
        'Profit': 'DECIMAL(18,2)'
    }
);
```

类型化之后先做时间覆盖审计，不要从趋势图反推数据是否完整：

```sql
WITH b AS (
    SELECT
        min(order_date) AS min_date,
        max(order_date) AS max_date,
        count(DISTINCT order_date) AS observed_days
    FROM ts_raw
), coverage AS (
    SELECT
        *,
        date_diff('day', min_date, max_date) + 1 AS calendar_days
    FROM b
)
SELECT
    min_date,
    max_date,
    observed_days,
    calendar_days,
    calendar_days - observed_days AS missing_days
FROM coverage;
```

还要检查目标粒度上的重复键。例如明细聚合到每日一维度后，不应再出现相同 `date_key + dimension` 的两行；否则后续连接和窗口都会重复计数。

时间序列的窗口函数必须建立在**分析粒度**上。先聚合原始行，再计算移动平均：

```sql
CREATE OR REPLACE VIEW ts_daily AS
SELECT
    order_date,
    count(*) AS order_lines,
    sum(quantity) AS units,
    sum(sales) AS sales,
    sum(profit) AS profit
FROM ts_raw
GROUP BY ALL;
```

不要直接对订单明细执行“7 行移动平均”，因为一个时间点可能对应多条订单，窗口会覆盖错误的日期范围。

## 3. 时间分桶

### 按日

`DATE` 字段可以按日直接分组：

```sql
SELECT order_date, sum(sales) AS sales
FROM ts_raw
GROUP BY ALL;
```

### 按周

使用 `time_bucket` 可以把任意时间戳分到固定宽度的时间桶：

```sql
SELECT
    time_bucket(INTERVAL '7 days', cast(order_date AS TIMESTAMP)) AS week_start,
    sum(sales) AS sales
FROM ts_raw
GROUP BY ALL
ORDER BY week_start;
```

需要注意周的起点。`time_bucket` 可用 `origin` 指定参考起点；如果业务周从周一开始，应显式确认结果，不要默认认为所有环境都按同一周起点分桶。

如果目标 DuckDB 版本没有 `time_bucket`，可退化为 `date_trunc('week', order_date)`，或按业务定义的固定起点计算日期差后分桶。

财年与自然年不同时，不要只改标签。先建立 `fiscal_year`、`fiscal_quarter` 和 `fiscal_month` 等业务键，再用这些键分组和比较；否则跨年期间的排名、累计和同比都会沿用自然年边界。

### 按月、季度和年

```sql
SELECT
    date_trunc('month', order_date)::DATE AS month_key,
    sum(sales) AS sales,
    sum(profit) AS profit
FROM ts_raw
GROUP BY ALL
ORDER BY month_key;
```

`date_trunc` 返回时间戳；展示日期粒度时用 `::DATE` 去掉时间部分。季度和年度只需替换 `'month'`。

`TIMESTAMPTZ` 应先转到业务时区再截断：

```sql
SELECT
    date_trunc(
        'day',
        order_at AT TIME ZONE 'Asia/Shanghai'
    )::DATE AS business_date
FROM events;
```

如果原始时间是无时区文本，先用 `strptime` 明确格式和时区，再转 `TIMESTAMPTZ`。把不同来源的时间拼在一起前，确认它们表示的是同一事件时间点，而不是把 UTC 和本地时间当成同类值。

### 用 ASOF JOIN 关联历史快照

价格、预算、状态快照往往按变化时间保存，并不与每条订单时间一一对应。需要“截至事件发生时最近的一条历史记录”时使用 `ASOF JOIN`：

```sql
SELECT
    e.order_id,
    e.order_time,
    p.price
FROM events AS e
ASOF JOIN price_history AS p
    ON e.product_id = p.product_id
   AND e.order_time >= p.effective_at;
```

普通等值连接会把历史记录复制成多行，或者只命中恰好同一时间的记录；候选价格必须按事件时间做最近一次匹配，才能避免未来信息泄漏。

## 4. 补齐日期

图表和滚动窗口应该建立在完整时间轴上。先生成日期序列，再左连接实际数据：

```sql
CREATE OR REPLACE VIEW ts_calendar AS
WITH bounds AS (
    SELECT min(order_date) AS start_date, max(order_date) AS end_date
    FROM ts_raw
)
SELECT ts::DATE AS date_key
FROM bounds,
LATERAL range(
    start_date,
    end_date + INTERVAL 1 DAY,
    INTERVAL 1 DAY
) AS r(ts);

CREATE OR REPLACE VIEW ts_daily_complete AS
SELECT
    c.date_key,
    coalesce(d.sales, 0) AS sales,
    coalesce(d.profit, 0) AS profit,
    d.order_date IS NOT NULL AS has_observations
FROM ts_calendar AS c
LEFT JOIN (
    SELECT order_date, sum(sales) AS sales, sum(profit) AS profit
    FROM ts_raw
    GROUP BY ALL
) AS d
    ON d.order_date = c.date_key;
```

填充值取决于指标语义：

- 销售额、订单数、流量：无记录通常表示 `0`；
- 平均客单价、利润率、转化率：无数据应保留 `NULL`，不能填 `0`；
- 库存、账户余额、价格：可考虑前向填充，但不能简单补零。

样本全局每天都有订单。要观察缺口补齐，按地区构造“地区 × 日期”完整网格：

```sql
SELECT r.region, c.date_key
FROM (SELECT DISTINCT Region AS region FROM ts_raw) AS r
CROSS JOIN ts_calendar AS c
LEFT JOIN (
    SELECT Region AS region, OrderDate AS date_key
    FROM ts_raw
    GROUP BY ALL
) AS observed
    ON observed.region = r.region
   AND observed.date_key = c.date_key;
```

完整表达式见随包 SQL 脚本中的 `ts_region_complete`。

## 5. 滚动指标与累计指标

完整日序列上可以使用行窗口：

```sql
SELECT
    date_key,
    sales,
    count(*) OVER (
        ORDER BY date_key
        ROWS BETWEEN 6 PRECEDING AND CURRENT ROW
    ) AS window_days_7,
    avg(sales) OVER (
        ORDER BY date_key
        ROWS BETWEEN 6 PRECEDING AND CURRENT ROW
    ) AS moving_avg_7,
    sum(sales) OVER (
        ORDER BY date_key
        ROWS BETWEEN 27 PRECEDING AND CURRENT ROW
    ) AS rolling_sales_28
FROM ts_daily_complete;
```

`ROWS BETWEEN 6 PRECEDING AND CURRENT ROW` 包含当前日在内共 7 行。前 6 行是不完整窗口，报告时应同时输出窗口行数，或只保留完整窗口：

```sql
QUALIFY count(*) OVER (
    ORDER BY date_key
    ROWS BETWEEN 6 PRECEDING AND CURRENT ROW
) = 7
```

`ROWS` 数的是行数，不是自然日。只要日期轴有缺口，7 行可能横跨 8 天、30 天甚至一年。做“最近 7 天/28 天”指标时，必须先补齐日历，或者明确改用按时间范围的窗口。

如果时间轴不完整、事件间隔不规则，改用时间范围：

```sql
sum(sales) OVER (
    ORDER BY date_key
    RANGE BETWEEN INTERVAL 6 DAY PRECEDING AND CURRENT ROW
)
```

先做时间分桶仍然更稳妥，因为重复时间戳会让 `RANGE` 把同一时间点的多行全部计入。

滚动窗口只回答“最近一段时间”；MTD/QTD/YTD 则要从期间起点累计。期间起点可以来自业务日历，而不是简单假设自然月：

YTD 需要在年度分区内累计：

```sql
sum(sales) OVER (
    PARTITION BY year(date_key)
    ORDER BY date_key
    ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
) AS ytd_sales
```

对“当前部分期间”做趋势判断时，应增加 `max(date_key)` 锚点，输出实际截止日、已覆盖天数和去年同期同截止日的结果。完整期间与截至今天的部分期间混在同一张排行或同比表里，会把“少统计几天”误判成下滑。

## 6. 环比、同比与周期快照

先统一指标定义。通常同时输出四项，而不是只给百分比：

- `current_value`：本期值；
- `base_value`：上期或去年同期的值；
- `diff = current_value - base_value`；
- `pct = 100 * (current_value / base_value - 1)`，基期为 `0` 或缺失时返回 `NULL`。

月度序列**已经验证连续**时，`lag` 是简洁实现：

```sql
WITH m AS (
    SELECT
        date_trunc('month', OrderDate)::DATE AS month_key,
        sum(Sales) AS sales
    FROM ts_raw
    GROUP BY ALL
)
SELECT
    month_key,
    sales,
    lag(sales) OVER (ORDER BY month_key) AS previous_month_sales,
    lag(sales, 12) OVER (ORDER BY month_key) AS same_month_last_year_sales,
    sales - lag(sales) OVER (ORDER BY month_key) AS mom_diff,
    sales - lag(sales, 12) OVER (ORDER BY month_key) AS yoy_diff,
    round(
        100.0 * cast(sales AS DOUBLE)
        / nullif(lag(sales) OVER (ORDER BY month_key), 0) - 100.0,
        2
    ) AS mom_pct,
    round(
        100.0 * cast(sales AS DOUBLE)
        / nullif(lag(sales, 12) OVER (ORDER BY month_key), 0) - 100.0,
        2
    ) AS yoy_pct
FROM m
ORDER BY month_key;
```

`lag(..., 12)` 只表示“前 12 行”。缺月时它命中的不是去年同月；数据起止不完整时也可能没有足够的 12 行。

稳健做法是先建完整月日期轴，再按日期精确偏移连接。无论某个月是否缺记录，结果仍保留该期间，并用 `has_observations` 区分“真实为 0”与“源数据没有记录”：

```sql
WITH bounds AS (
    SELECT
        date_trunc('month', min(OrderDate))::DATE AS start_month,
        date_trunc('month', max(OrderDate))::DATE AS end_month
    FROM ts_raw
),
month_calendar AS (
    SELECT cast(m AS DATE) AS month_key
    FROM bounds,
    LATERAL range(
        start_month,
        end_month + INTERVAL 1 MONTH,
        INTERVAL 1 MONTH
    ) AS r(m)
),
observed AS (
    SELECT
        date_trunc('month', OrderDate)::DATE AS month_key,
        count(DISTINCT OrderDate) AS observed_days,
        sum(Sales) AS sales
    FROM ts_raw
    GROUP BY ALL
),
monthly_complete AS (
    SELECT
        c.month_key,
        coalesce(o.sales, 0::DECIMAL(18, 2)) AS sales,
        o.month_key IS NOT NULL AS has_observations,
        coalesce(o.observed_days, 0) AS observed_days,
        date_diff('day', c.month_key, c.month_key + INTERVAL 1 MONTH)
            AS days_in_month
    FROM month_calendar AS c
    LEFT JOIN observed AS o USING (month_key)
)
SELECT
    cur.month_key,
    cur.sales,
    prev.sales AS previous_month_sales,
    last_year.sales AS same_month_last_year_sales,
    cur.sales - prev.sales AS mom_diff,
    cur.sales - last_year.sales AS yoy_diff,
    CASE WHEN cur.has_observations AND prev.has_observations
         THEN round(
             100.0 * CAST(cur.sales AS DOUBLE)
             / NULLIF(CAST(prev.sales AS DOUBLE), 0) - 100.0,
             2
         )
    END AS mom_pct,
    CASE WHEN cur.has_observations AND last_year.has_observations
         THEN round(
             100.0 * CAST(cur.sales AS DOUBLE)
             / NULLIF(CAST(last_year.sales AS DOUBLE), 0) - 100.0,
             2
         )
    END AS yoy_pct,
    round(100.0 * cur.observed_days / cur.days_in_month, 2) AS row_coverage_pct
FROM monthly_complete AS cur
LEFT JOIN monthly_complete AS prev
    ON prev.month_key = cur.month_key - INTERVAL 1 MONTH
LEFT JOIN monthly_complete AS last_year
    ON last_year.month_key = cur.month_key - INTERVAL 1 YEAR
ORDER BY cur.month_key;
```

分区指标也使用同样的日期键连接。`PARTITION BY region` 的 `lag` 在缺月时仍有错位风险；更稳妥的是：

```sql
LEFT JOIN previous AS p
    ON p.region = cur.region
   AND p.month_key = cur.month_key - INTERVAL 1 MONTH
```

日粒度的周同比同理：先保证每日一行，再用 `current.date_key - INTERVAL 7 DAY = previous.date_key`，不要假定 `lag(..., 7)` 一定跨了 7 天。

边界期间需要单独处理：

- 当前月尚未结束时，不能把整月实际值与完整上月直接比较；可截取上月 1 日至同一天的等长窗口，或明确标记本期不完整。
- 首月可能从月中开始，末月可能停在月中；覆盖率只说明有记录的天数，不自动证明业务数据完整。
- YTD/MTD/QTD 要按业务日历分区，并同时输出本期截止日和基期截止日；同比应比较“去年同一截止日”，而不是去年完整期间。
- 库存、余额等快照指标不能把缺失日补成 `0`；应保留 `NULL`、前向填充或使用 ASOF JOIN 取最近快照。

比较“最近 28 天”与“前 28 天”时，用数据最后一天锚定，避免把不完整的当前自然月误当成整月：

```sql
WITH anchor AS (
    SELECT max(order_date) AS end_date
    FROM ts_daily_complete
),
periods AS (
    SELECT
        d.date_key,
        d.sales,
        CASE
            WHEN d.date_key > a.end_date - INTERVAL 28 DAY THEN 'current'
            WHEN d.date_key > a.end_date - INTERVAL 56 DAY THEN 'previous'
        END AS period
    FROM ts_daily_complete AS d
    CROSS JOIN anchor AS a
)
SELECT
    period,
    sum(sales) AS sales
FROM periods
WHERE period IS NOT NULL
GROUP BY ALL
ORDER BY period;
```

## 7. 季节性

先把明细压缩成日序列，再比较不同星期和月份的日均销售额：

```sql
WITH d AS (
    SELECT OrderDate, sum(Sales) AS sales
    FROM ts_raw
    GROUP BY ALL
)
SELECT
    dayofweek(OrderDate) AS day_number,
    dayname(OrderDate) AS weekday,
    round(avg(sales), 2) AS avg_daily_sales
FROM d
GROUP BY ALL
ORDER BY day_number;
```

季节指数：

```sql
100.0 * avg_daily_sales / sum(avg_daily_sales) OVER ()
```

样本结果中，周六的日均销售额最高，周二最低；这只说明样本内的模式，不自动代表统计显著性或未来一定重复。

## 8. 异常检测

基线要与被检测点分离。下面使用过去 28 天（不含当天）的滚动均值和标准差：

```sql
WITH rolling AS (
    SELECT
        date_key,
        sales,
        avg(sales) OVER baseline AS rolling_mean_28,
        stddev_pop(sales) OVER baseline AS rolling_std_28
    FROM ts_daily_complete
    WINDOW baseline AS (
        ORDER BY date_key
        ROWS BETWEEN 28 PRECEDING AND 1 PRECEDING
    )
)
SELECT
    date_key,
    sales,
    round(rolling_mean_28, 2) AS baseline,
    round(
        cast(sales - rolling_mean_28 AS DOUBLE)
        / nullif(rolling_std_28, 0),
        2
    ) AS z_score
FROM rolling
WHERE abs(
    cast(sales - rolling_mean_28 AS DOUBLE)
    / nullif(rolling_std_28, 0)
) > 3;
```

使用注意：

- 必须排除当前行的基线，否则当前异常会把均值和标准差拉向自己；
- 低波动序列可能产生大量“异常”，应同时考虑绝对业务阈值；
- 促销、节假日、价格变化应作为解释变量或排除窗口；
- 数据量较大时用滚动中位数和 MAD 更稳健；阈值需要根据历史误报率重新校准。

样本中高波动的典型日期包括 `2025-11-03`、`2025-07-03` 和 `2024-10-06`。异常不等于数据错误，应回到订单、地区、产品和客户维度解释。

## 9. 可解释的基线预测

在没有额外模型依赖时，可以先给出透明基线：

```sql
WITH bounds AS (
    SELECT max(OrderDate) AS max_date
    FROM ts_raw
),
monthly AS (
    SELECT
        date_trunc('month', OrderDate)::DATE AS month_key,
        sum(Sales) AS sales
    FROM ts_raw
    GROUP BY ALL
),
modelable_months AS (
    SELECT m.*
    FROM monthly AS m
    CROSS JOIN bounds AS b
    WHERE m.month_key < date_trunc('month', b.max_date)
       OR b.max_date = last_day(b.max_date)
),
recent AS (
    SELECT
        month_key,
        sales,
        row_number() OVER (ORDER BY month_key) AS x
    FROM modelable_months
    QUALIFY month_key >= max(month_key) OVER () - INTERVAL 11 MONTH
),
model AS (
    SELECT
        regr_intercept(sales, x) AS intercept,
        regr_slope(sales, x) AS slope,
        count(*) AS model_months
    FROM recent
),
forecast_origin AS (
    SELECT date_trunc('month', max_date) + INTERVAL 1 MONTH AS start_month
    FROM bounds
)
SELECT
    cast(start_month + (i - 1) * INTERVAL 1 MONTH AS DATE) AS forecast_month,
    round(intercept + slope * (model_months + i), 2) AS forecast_sales
FROM model, forecast_origin,
LATERAL range(1, 4) AS r(i);
```

这是“最近 12 个完整月线性趋势外推”，不是完整的时间序列模型。若数据最后一天在月中，当前月会从训练集中排除，并从下一个完整月开始预测。上线前应：

1. 留出最后若干个月做回测；
2. 比较 naïve、移动平均、季节性和回归基线的 MAE/WAPE；
3. 检查残差是否仍有趋势或季节性；
4. 对促销、缺货和节假日提供人工调整。

## 10. 从洞察到导出

建议固定输出以下结果：

- 数据质量与时间覆盖；
- 日/周/月趋势；
- 最新周期与上一周期 KPI；
- 同比、环比；
- 星期和月份季节指数；
- 异常清单及业务维度归因；
- 预测结果及方法、窗口、回测误差。

```sql
COPY (TABLE ts_monthly_comparison) TO 'out/monthly.parquet'
  (FORMAT parquet, COMPRESSION zstd);

COPY (
    SELECT * FROM ts_anomaly_scan WHERE is_anomaly
) TO 'out/anomalies.csv'
  (FORMAT CSV, HEADER true);
```

`COPY` 不会自动创建 `out/` 目录，且同名 CSV/Parquet 文件会被覆盖。

## 11. 验收清单

- 时间字段已显式转成 `DATE`、`TIMESTAMP` 或 `TIMESTAMPTZ`；
- 所有窗口计算发生在正确分析粒度上；
- 日/月日期轴对缺口和边界有明确处理；
- 聚合值与平均值使用正确填充规则；
- 同比/环比使用精确日期键，并与业务自然周期或财年一致；
- 当前期间和首尾期间已标记完整度，或用等长窗口比较；
- 异常检测基线不包含当前观测；
- 预测使用时间外回测，而非随机训练/测试切分；
- 带时区时间戳已统一到业务时区后再分桶；
- 业务结论能追溯到订单或维度明细。

## 12. 常见错误

| 错误 | 后果 | 修正 |
|---|---|---|
| 直接在明细行上求移动平均 | 一个日期占多行，窗口范围错误 | 先聚合成日/周/月序列 |
| 把缺失销售日填 `NULL` 后直接画折线 | 图上断线，滚动函数窗口不准 | 用日期轴补 `0`，同时标记是否观测 |
| 对利润率补 `0` | 把“无业务”误读成“零利润” | 比率类指标保留 `NULL` |
| 用 `lag(...,12)` 但月份不连续 | 拿到第 12 行而非去年同期 | 先补月日期轴再按日期偏移连接 |
| 本月截至 10 日与上月整月比较 | 把统计天数差误判为业务下滑 | 截取上月 1 日至 10 日，或排除不完整期间 |
| 异常基线包含当前点 | 异常值削弱自身偏离程度 | 窗口截止到前一日 |
| 用随机切分评估预测 | 使用未来数据评估过去 | 按时间留出末尾区间回测 |
| 周桶起点未核对 | 周末、月界统计错位 | 用 `time_bucket` 的 `origin` 或显式日期规则 |
| 直接用 UTC 日期统计本地业务 | 夜间订单落到错误日期 | 先 `timezone(...)` 或 `AT TIME ZONE` 转业务时区 |
| 金额保留为 `DOUBLE` | 汇总出现微小漂移 | 显式 `DECIMAL(18,2)` |

