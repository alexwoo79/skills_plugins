"""
数据加载和处理层
负责 CSV/Excel 文件的读取、基础数据转换和整合
"""

import polars as pl
import polars.selectors as cs
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# 程序实际读取的系数列；其余列不参与任何计算
KEY_USED_COLUMNS = ("系数key", "计容", "物理", "销售", "赠送")

DIAGNOSTIC_COLUMNS = ("级别", "类别", "对象", "涉及面积", "占面积线比例%", "影响", "处置建议")

# 列表类诊断最多列几个对象，超出折叠
MAX_LISTED_OBJECTS = 10


def _brief(items, limit: int = MAX_LISTED_OBJECTS) -> str:
    items = [str(i) for i in items]
    if len(items) <= limit:
        return "、".join(items)
    return "、".join(items[:limit]) + f" 等 {len(items)} 个"


def build_diagnostics(buildings, attributes, key) -> pl.DataFrame:
    """
    构建输入数据诊断表。

    底表用 left join + fill_null(0) 构建（见 build_analysis_data），匹配不上的行不会报错、
    只会静默按 0 计。本函数把这些情况显式列出，**只做诊断，不改变任何计算结果**。

    :param buildings: 面积线数据，含 楼号 / 系数key / 楼层 / 面积
    :param attributes: 楼栋属性，含 楼号
    :param key: 系数表，含 系数key
    :return: polars.DataFrame，列为 DIAGNOSTIC_COLUMNS
    """
    empty = pl.DataFrame(schema={
        "级别": pl.String, "类别": pl.String, "对象": pl.String,
        "涉及面积": pl.Float64, "占面积线比例%": pl.Float64,
        "影响": pl.String, "处置建议": pl.String,
    })
    if buildings is None or buildings.height == 0:
        return empty

    rows = []
    total_area = float(buildings["面积"].sum() or 0.0)

    def add(level, category, obj, area, impact, action):
        area = float(area or 0.0)
        rows.append({
            "级别": level,
            "类别": category,
            "对象": str(obj),
            "涉及面积": round(area, 2),
            "占面积线比例%": round(area / total_area * 100, 2) if total_area else 0.0,
            "影响": impact,
            "处置建议": action,
        })

    key_set = set()
    if key is not None and "系数key" in key.columns:
        key_set = {str(v) for v in key["系数key"].to_list()}
    attr_units = set()
    if attributes is not None and "楼号" in attributes.columns:
        attr_units = {str(v) for v in attributes["楼号"].to_list()}
    csv_units = {str(v) for v in buildings["楼号"].to_list()}

    # 1) 面积线里的系数key 未在系数表定义 —— 四类面积全部按 0 计
    by_key = (
        buildings.group_by("系数key")
        .agg(pl.col("面积").sum().alias("合计"))
        .sort("合计", descending=True)
    )
    unmatched_keys = [r for r in by_key.iter_rows(named=True) if str(r["系数key"]) not in key_set]
    for r in unmatched_keys:
        add("提示", "未匹配系数key", r["系数key"], r["合计"],
            "该 key 的行在 计容/物理/销售/赠送 四类面积中都按 0 计",
            "若该项已被「主体」覆盖（如 套内 是主体的分项），按 0 计才正确；"
            "否则需在 系数key.xlsx 补该 key 的四个系数")

    # 2) 面积线的楼号在楼栋属性中缺失 —— 类型/奖励/精装为空
    by_unit = (
        buildings.group_by("楼号")
        .agg(pl.col("面积").sum().alias("合计"))
        .sort("楼号")
    )
    missing_attr = [r for r in by_unit.iter_rows(named=True) if str(r["楼号"]) not in attr_units]
    for r in missing_attr:
        add("高危", "楼栋属性缺该楼号", r["楼号"], r["合计"],
            "类型/是否奖励楼栋/是否精装为空，配套楼栋识别与奖励折减均不生效",
            "在 楼栋属性.xlsx 补该楼号记录")

    # 3) 楼栋属性有楼号但没有面积线 —— 户数与面积两个口径并列
    orphan_attr = sorted(attr_units - csv_units)
    if orphan_attr:
        add("警告", "楼栋属性有楼号但无面积线", _brief(orphan_attr), 0.0,
            "这些楼的户配仍全额计入「总户数」，但面积类指标不覆盖它们，"
            "两个口径会并列出现在同一张指标表",
            "确认面积线是否遗漏；若确为分栋提交，需在交付说明中注明指标覆盖范围")

    # 4) 没有 1F 楼层行 —— 不参与 floor=1f 的查询
    if "楼层" in buildings.columns:
        floors = buildings.with_columns(pl.col("楼层").cast(pl.String).str.to_uppercase())
        has_1f = set(floors.filter(pl.col("楼层") == "1F")["楼号"].cast(pl.String).unique().to_list())
        no_1f = [r for r in by_unit.iter_rows(named=True) if str(r["楼号"]) not in has_1f]
        if no_1f:
            # 区分纯地下楼栋（只有 B* / -- 楼层）与地上楼栋漏导
            underground, above = [], []
            for r in no_1f:
                unit = str(r["楼号"])
                unit_floors = set(
                    floors.filter(pl.col("楼号").cast(pl.String) == unit)["楼层"].unique().to_list()
                )
                is_above = any(
                    f and not f.startswith("B") and f not in ("--", "ROOF", "TN")
                    for f in unit_floors
                )
                (above if is_above else underground).append(r)
            if underground:
                add("提示", "纯地下楼栋（无 1F 属正常）", _brief([r["楼号"] for r in underground]),
                    sum(float(r["合计"]) for r in underground),
                    "这些楼没有地上楼层行，不计入依赖 floor=1f 的指标，属正常",
                    "无需处理")
            if above:
                add("警告", "地上楼栋无 1F 图层行", _brief([r["楼号"] for r in above]),
                    sum(float(r["合计"]) for r in above),
                    "依赖 floor=1f 的指标（建筑基底面积、住宅基底面积）不计入这些楼",
                    "核对这些楼是否漏导 1F 图层")

    # 5) 系数表存在程序未读取的列
    if key is not None:
        extra_cols = [c for c in key.columns if c not in KEY_USED_COLUMNS]
        if extra_cols:
            add("提示", "系数表存在程序未读取的列", _brief(extra_cols), 0.0,
                "这些列不参与任何计算，对应口径尚未实现",
                "确认是否需要实现该口径；否则建议在系数表中注明，避免被当作已生效")

    # 6) 系数表中有但面积线未使用的 key
    used_keys = {str(v) for v in buildings["系数key"].to_list()}
    unused_keys = sorted(key_set - used_keys)
    if unused_keys:
        add("提示", "系数表中未被面积线使用的 key", _brief(unused_keys), 0.0,
            "不影响计算，属信息记录",
            "无需处理；若预期应出现，检查图层命名")

    if not rows:
        return empty
    return pl.DataFrame(rows).select(DIAGNOSTIC_COLUMNS)


class ArchitectDataFrame(pl.DataFrame):
    """
    继承自 polars.DataFrame，用于查询汇总处理设计指标的数据。
    """

    def filter(self, *args, **kwargs):
        """
        重写 filter 方法，返回 ArchitectDataFrame 实例。
        """
        filtered_df = super().filter(*args, **kwargs)
        return self.__class__(filtered_df)

    def select(self, *args, **kwargs):
        """
        重写 select 方法，返回 ArchitectDataFrame 实例。
        """
        selected_df = super().select(*args, **kwargs)
        return self.__class__(selected_df)

    def query_area(
        self,
        number=None,
        key=None,
        floor=None,
        btype=None,
        bonus=None,
        skin=None,
        atype=None,
    ):
        """
        根据输入的过滤条件查询建筑面积。

        参数:
        number (num, str 或 list, 可选): 楼号，可以是单个值或列表。
        key (str 或 list, 可选): 系数key，可以是单个值或列表。
        floor (str 或 list, 可选): 楼层，可以是单个值或列表。输入的楼层会被转换为大写。
        btype (str 或 list, 可选): 楼型，可以是单个值或列表。输入的楼型会被转换为大写。
        bonus (str 或 list, 可选): 是否奖励楼栋，可以是单个值或列表['Y','N']。输入的值会被转换为大写。
        skin (str 或 list, 可选): 是否精装，可以是单个值或列表['精装','毛坯']。
        atype (str, 可选): 面积类型，可以是 'orign'（默认值：面积），
        'jr'：计容，'physical'：物理 , 'sale'：销售。'free'：赠送。

        返回:
        float: 根据过滤条件计算的面积总和。
        """
        query = self
        try:
            if number is not None:
                if not isinstance(number, list):
                    number = [str(number)]
                query = query.filter(pl.col("楼号").is_in(number))

            if key is not None:
                if not isinstance(key, list):
                    key = [key]
                query = query.filter(pl.col("系数key").is_in(key))

            if floor is not None:
                if not isinstance(floor, list):
                    floor = [floor]
                floor_upper = [f.upper() for f in floor]
                query = query.with_columns(
                    pl.col("楼层").cast(pl.String).str.to_uppercase()
                )
                query = query.filter(pl.col("楼层").is_in(floor_upper))

            if bonus is not None:
                if not isinstance(bonus, list):
                    bonus = [bonus]
                bonus_upper = [b.upper() for b in bonus]
                query = query.filter(pl.col("是否奖励楼栋").is_in(bonus_upper))

            if btype is not None:
                if not isinstance(btype, list):
                    btype = [btype]
                btype_upper = [b.upper() for b in btype]
                query = query.filter(pl.col("楼型").is_in(btype_upper))

            if skin is not None:
                if not isinstance(skin, list):
                    skin = [skin]
                skin_upper = [s.upper() for s in skin]
                query = query.filter(pl.col("是否精装").is_in(skin_upper))

            if atype is None or atype == "orign":
                return round(query["面积"].sum(), 2)
            elif atype == "jr":
                return round(query["计容面积"].sum(), 2)
            elif atype == "physical":
                return round(query["物理面积"].sum(), 2)
            elif atype == "sale":
                return round(query["销售面积"].sum(), 2)
            elif atype == "free":
                return round(query["赠送面积"].sum(), 2)
            else:
                raise ValueError("Invalid value for atype")
        except pl.exceptions.ColumnNotFoundError as e:
            logger.exception("Column not found while querying area: %s", e)
            return None

    def get(self, metric_name: str):
        result = (
            self.filter(pl.col("指标名称") == metric_name).select("数值").head(1)
        )
        return round(result.item(), 2) if len(result) > 0 else 0


class DataLoader:
    """数据加载和处理"""

    BUILDINGS_DIR_NAMES = ("buildings_csv", "Buildings_csv")

    def __init__(self, project_path: str):
        self.fold_path = project_path
        self.buildings = None
        self.attributes = None
        self.key = None
        self.hp_data = None
        self.info = None

    def resolve_buildings_path(self, folder_path: Path) -> Path:
        """找到实际存在的 buildings 目录"""
        for directory_name in self.BUILDINGS_DIR_NAMES:
            candidate = folder_path / directory_name
            if candidate.is_dir():
                return candidate
        raise FileNotFoundError(
            f"Buildings folder not found under: {self.fold_path}"
        )

    def load_buildings(self):
        """
        从 CSV 读取建筑数据，拆分图层信息
        """
        folder_path = Path(self.fold_path)
        if not folder_path.exists():
            raise FileNotFoundError(f"Folder not found: {self.fold_path}")

        buildings_path = self.resolve_buildings_path(folder_path)
        paths = list(buildings_path.glob("*.csv"))
        dfs = [
            pl.read_csv(path).with_columns(
                楼号=pl.lit(path.stem).cast(pl.Categorical())
            )
            for path in paths
        ]

        df = pl.concat(dfs).select(pl.col("楼号", "图层", "面积"))
        df_layer_check = df.with_columns(段落=pl.col("图层").str.split("-").list.len())

        if df_layer_check.filter(pl.col("段落") == 3).height > 0:
            df_part1 = (
                df_layer_check.filter(pl.col("段落") == 3)
                .with_columns(
                    pl.col("图层").str.split("-").list.get(0).alias("AA/AB"),
                    pl.col("图层").str.split("-").list.get(1).alias("楼型"),
                    pl.col("图层").str.split("-").list.get(2).alias("系数key"),
                )
                .with_columns(楼层=pl.lit("--"))
                .select(["楼号", "AA/AB", "楼型", "楼层", "系数key", "面积"])
            )

            df_part2 = (
                df_layer_check.filter(pl.col("段落") == 4)
                .with_columns(
                    pl.col("图层").str.split("-").list.get(0).alias("AA/AB"),
                    pl.col("图层").str.split("-").list.get(1).alias("楼型"),
                    pl.col("图层").str.split("-").list.get(2).alias("楼层"),
                    pl.col("图层").str.split("-").list.get(3).alias("系数key"),
                )
                .select(["楼号", "AA/AB", "楼型", "楼层", "系数key", "面积"])
            )
            buildings = pl.concat([df_part1, df_part2]).with_columns(
                pl.col("AA/AB").cast(pl.Categorical()),
                pl.col("楼型").cast(pl.Categorical()),
                pl.col("楼层").cast(pl.Categorical()),
                pl.col("系数key").cast(pl.Categorical()),
            )
        else:
            buildings = (
                df_layer_check.filter(pl.col("段落") == 4)
                .with_columns(
                    pl.col("图层").str.split("-").list.get(0).alias("AA/AB"),
                    pl.col("图层").str.split("-").list.get(1).alias("楼型"),
                    pl.col("图层").str.split("-").list.get(2).alias("楼层"),
                    pl.col("图层").str.split("-").list.get(3).alias("系数key"),
                )
                .select(["楼号", "AA/AB", "楼型", "楼层", "系数key", "面积"])
                .with_columns(
                    pl.col("AA/AB").cast(pl.Categorical()),
                    pl.col("楼型").cast(pl.Categorical()),
                    pl.col("楼层").cast(pl.Categorical()),
                    pl.col("系数key").cast(pl.Categorical()),
                )
            )

        self.buildings = buildings
        logger.debug("Buildings data loaded: %d rows", len(buildings))

    def load_related_data(self):
        """
        从 Excel 读取楼栋属性、系数 key、补充信息
        """
        folder_path = Path(self.fold_path)

        attributes_path = folder_path / "楼栋属性.xlsx"
        if not attributes_path.exists():
            raise FileNotFoundError(f"File not found: {attributes_path}")

        attributes = (
            pl.read_excel(attributes_path)
            .with_columns(
                cs.numeric().cast(pl.Int16), cs.string().cast(pl.Categorical())
            )
            .with_columns(pl.col("楼号").cast(pl.String).cast(pl.Categorical()))
            .fill_null(0)
            .with_columns(
                pl.col("是否奖励楼栋")
                .cast(pl.String())
                .str.replace("1", "Y")
                .str.replace("0", "N")
                .cast(pl.Categorical)
            )
        )

        key_path = folder_path / "系数key.xlsx"
        if not key_path.exists():
            raise FileNotFoundError(f"File not found: {key_path}")
        key = (
            pl.read_excel(key_path)
            .with_columns(
                cs.numeric().cast(pl.Float32),
                pl.col("系数key").cast(pl.Categorical()),
            )
            .fill_null(0)
        )

        info_path = folder_path / "补充信息.xlsx"
        if not info_path.exists():
            raise FileNotFoundError(f"File not found: {info_path}")
        info = pl.read_excel(info_path).with_columns(
            cs.string().cast(pl.Categorical()), cs.numeric().cast(pl.Float32)
        )

        hp_data = attributes.select(
            cs.all().exclude(
                [
                    "楼号",
                    "排序",
                    "单元数",
                    "每单元户数",
                    "层数",
                    "类型",
                    "是否精装",
                    "是否奖励楼栋",
                ]
            )
        )

        self.attributes = attributes
        self.key = key
        self.hp_data = hp_data.sum()
        self.info = info
        logger.debug("Related data loaded (attributes, key, info)")

    def build_analysis_data(
        self, bonus_factor: float, bonus_list: Optional[list[str]] = None
    ) -> pl.DataFrame:
        """
        合并建筑数据、楼栋属性、系数，生成分析用数据
        """
        data = (
            self.buildings.join(
                self.attributes, how="left", left_on="楼号", right_on="楼号"
            )
            .join(self.key, how="left", left_on="系数key", right_on="系数key")
            .with_columns(
                计容面积=pl.col("面积") * pl.col("计容"),
                物理面积=pl.col("面积") * pl.col("物理"),
                销售面积=pl.col("面积") * pl.col("销售"),
                赠送面积=pl.col("面积") * pl.col("赠送"),
            )
        )

        if bonus_list is not None:
            normalized_bonus_list = [str(i) for i in bonus_list]
            data = data.with_columns(
                是否奖励楼栋=pl.when(
                    pl.col("楼号").cast(pl.String).is_in(normalized_bonus_list)
                )
                .then(pl.lit("Y"))
                .otherwise(pl.lit("N"))
            )

        return data.with_columns(
            计容面积=(
                pl.when(pl.col("是否奖励楼栋") == "Y")
                .then(pl.col("计容面积") / bonus_factor)
                .otherwise(pl.col("计容面积"))
            ).fill_null(0)
        ).select(
            [
                "楼号",
                "楼型",
                "楼层",
                "系数key",
                "面积",
                "计容面积",
                "物理面积",
                "销售面积",
                "赠送面积",
                "是否奖励楼栋",
                "类型",
                "是否精装",
                "计容",
            ]
        )
