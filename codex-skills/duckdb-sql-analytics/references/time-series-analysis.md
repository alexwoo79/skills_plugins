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

时间序列分析之前，先确认四件事：

1. **事件时间**：业务发生时间，不是入库时间或更新时间。
2. **分析粒度**：订单级、日级、周级或月级。
3. **聚合方式**：销售额用 `SUM`，价格用 `AVG`，不同粒度不能混用。
4. **时间边界**：区间通常采用左闭右开 `[start, end)`，避免月末和次日重复。

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

`ROWS BETWEEN 6 PRECEDING AND CURRENT ROW` 包含当前日在内共 7 行。前 6 行是不完整窗口，报告时应同时输出窗口行数，或只保留完整窗口。

如果时间轴不完整、事件间隔不规则，改用时间范围：

```sql
sum(sales) OVER (
    ORDER BY date_key
    RANGE BETWEEN INTERVAL 6 DAY PRECEDING AND CURRENT ROW
)
```

先做时间分桶仍然更稳妥，因为重复时间戳会让 `RANGE` 把同一时间点的多行全部计入。

YTD 需要在年度分区内累计：

```sql
sum(sales) OVER (
    PARTITION BY year(date_key)
    ORDER BY date_key
    ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
) AS ytd_sales
```

## 6. 环比、同比与周期快照

月度序列完整时，`lag` 是清晰的实现：

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

`lag(..., 12)` 只有在月份连续且前两年同期存在时才正确。缺月或数据起止不完整时，先生成月日期轴再按日期偏移连接。

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
WITH monthly AS (
    SELECT
        date_trunc('month', OrderDate)::DATE AS month_key,
        sum(Sales) AS sales
    FROM ts_raw
    GROUP BY ALL
),
recent AS (
    SELECT
        month_key,
        sales,
        row_number() OVER (ORDER BY month_key) AS x
    FROM monthly
    QUALIFY month_key >= (
        SELECT max(month_key) - INTERVAL 11 MONTH FROM monthly
    )
),
model AS (
    SELECT
        regr_intercept(sales, x) AS intercept,
        regr_slope(sales, x) AS slope,
        max(month_key) AS last_month
    FROM recent
)
SELECT
    last_month + i * INTERVAL 1 MONTH AS forecast_month,
    round(intercept + slope * (12 + i), 2) AS forecast_sales
FROM model,
LATERAL range(1, 4) AS r(i);
```

这是“最近 12 个月线性趋势外推”，不是完整的时间序列模型。上线前应：

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
COPY (TABLE ts_monthly) TO 'out/monthly.parquet'
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
- 同比/环比窗口与业务自然周期一致；
- 异常检测基线不包含当前观测；
- 预测使用时间外回测，而非随机训练/测试切分；
- 业务结论能追溯到订单或维度明细。

## 12. 常见错误

| 错误 | 后果 | 修正 |
|---|---|---|
| 直接在明细行上求移动平均 | 一个日期占多行，窗口范围错误 | 先聚合成日/周/月序列 |
| 把缺失销售日填 `NULL` 后直接画折线 | 图上断线，滚动函数窗口不准 | 用日期轴补 `0`，同时标记是否观测 |
| 对利润率补 `0` | 把“无业务”误读成“零利润” | 比率类指标保留 `NULL` |
| 用 `lag(...,12)` 但月份不连续 | 拿到第 12 行而非去年同期 | 先补月日期轴再按日期偏移连接 |
| 异常基线包含当前点 | 异常值削弱自身偏离程度 | 窗口截止到前一日 |
| 用随机切分评估预测 | 使用未来数据评估过去 | 按时间留出末尾区间回测 |
| 周桶起点未核对 | 周末、月界统计错位 | 用 `time_bucket` 的 `origin` 或显式日期规则 |
| 金额保留为 `DOUBLE` | 汇总出现微小漂移 | 显式 `DECIMAL(18,2)` |

