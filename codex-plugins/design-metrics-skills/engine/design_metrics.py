import polars as pl
import polars.selectors as cs
import logging
from pathlib import Path
from typing import Optional

from data_loader import DataLoader, ArchitectDataFrame, build_diagnostics
from metrics_helpers import MetricsHelper
from city_rules import CityRules

# 启用字符串缓存(必须)
pl.enable_string_cache()

logger = logging.getLogger(__name__)


class DesignMetrics:

    BUILDINGS_DIR_NAMES = ("buildings_csv", "Buildings_csv")
    OUTPUT_FIELD_CONFIG = {
        "sh": [
            "差值",
            "S_计容面积",
            "销售面积",
            "物理面积",
            "赠送面积",
            "用地面积",
            "不计容配套面积",
            "计容面积",
            "配套总面积",
            "奖励后计容面积",
            "奖励前计容面积",
            "奖励面积",
            "屋顶机房面积",
            "s_不奖励楼栋计容面积",
            "s_奖励楼栋计容面积",
            "保障房不计容半阳台",
            "地上总建筑面积",
            "保障房屋顶机房",
            "住宅总建筑面积",
            "容积率",
            "电缆夹层面积",
            "地库面积",
            "人防地下室面积",
            "地下机动车数量",
            "人防车位数",
            "非人防地下室面积",
            "建筑基底面积",
            "住宅基底面积",
            "总建筑面积",
            "地上电梯厅",
            "地库大堂面积",
            "地上连廊面积",
            "首层大堂",
            "地上楼梯间",
            "S_建筑工程面积",
            "S_计一半面积",
            "地下储藏间",
            "套内面积",
            "保障房计容面积",
            "联排计容面积",
            "洋房计容面积",
            "联排奖励面积",
            "洋房屋顶机房面积",
            "门卫",
            "总户数",
            "保障房户数",
            "商品房户数",
        ],
        "cs": [
            "差值",
            "计容住宅面积",
            "屋顶机房面积",
            "地下楼梯间面积",
            "架空面积",
            "不计容住宅面积",
            "商业面积",
            "计容配套面积",
            "不计容配套面积",
            "地库面积",
            "骑楼面积",
            "销售面积",
            "住宅物理面积",
            "赠送面积",
            "人防地下室面积",
            "非人防地下室面积",
            "地下机动车数量",
            "人防车位数",
            "非人防车位数",
            "地面停车",
            "机动车总数",
            "用地面积",
            "绿地面积",
            "配套基底面积",
            "商业基底面积",
            "住宅基底面积",
            "建筑基底面积",
            "建筑密度",
            "绿地率",
            "容积率",
            "配套总面积",
            "计容总面积",
            "地上建筑面积",
            "总建筑面积",
            "总户数",
            "保障房户数",
            "商品房户数",
        ],
        "hf": [
            "差值",
            "住宅计容面积",
            "架空面积",
            "保障房面积",
            "住宅装配式奖励面积",
            "商业面积",
            "计容配套面积",
            "不计容配套面积",
            "地库面积",
            "销售面积",
            "住宅物理面积",
            "赠送面积",
            "人防地下室面积",
            "非人防地下室面积",
            "地下机动车数量",
            "人防车位数",
            "非人防车位数",
            "地面停车",
            "机动车总数",
            "用地面积",
            "绿地面积",
            "配套基底面积",
            "商业基底面积",
            "住宅基底面积",
            "建筑基底面积",
            "建筑密度",
            "绿地率",
            "容积率",
            "配套总面积",
            "计容总面积",
            "地上建筑面积",
            "总建筑面积",
            "总户数",
            "保障房户数",
            "商品房户数",
        ],
        "gy": [
            "差值",
            "S_计容面积",
            "销售面积",
            "物理面积",
            "赠送面积",
            "用地面积",
            "计容面积",
            "配套总面积",
            "屋顶机房面积",
            "地上总建筑面积",
            "住宅总建筑面积",
            "商办面积",
            "容积率",
            "地库面积",
            "人防地下室面积",
            "地下机动车数量",
            "人防车位数",
            "建筑基底面积",
            "总建筑面积",
            "非人防地下室面积",
            "总户数",
            "架空层",
        ],
    }

    # ArchitectDataFrame 已外迁到 data_loader.py，通过导入引入
    # 这里保留别名以保证向后兼容
    ArchitectDataFrame = ArchitectDataFrame
    def __init__(self, fold_path: str, city: str):
        """
        初始化 DesignMetrics 对象
        :param fold_path: 工作路径，包含需要处理的文件
        :param city: 当前计算的城市名称
        """
        self.fold_path = fold_path
        self.city = city
        self.bonus_factor = 1.0  # 奖励系数
        self.buildings = None  # 存储面积线合并后的数据
        self.attributes = None  # 存储楼栋属性数据
        self.key = None  # 存储系数key数据
        self.hp_data = None  # 存储户配数据
        self.info = None  # 存储补充信息数据
        self.data = None  # 存储最终的数据
        self.buildings_list = None  # 存储楼号列表
        self.bonus_list = None  # 存储奖励楼号列表
        self.facility_list = None  # 存储配套楼号列表
        self.reset_data = None

        """
        reset.data 功能解释
        # 存储重置数据(奖励系数更新为 1,),奖励楼栋列表更新为自定义列表(pl.col('是否奖励楼栋')数据列被重写)
        # 然后更新为自定义奖励系数(update_bous_factor)，配套楼号更新为自定义列表.(update_facility_list)
        # 最后根据新奖励系数重新加载数据
        """
        self.new_bonus_list = None  # 存储新的奖励楼号列表
        self.new_facility_list = None  # 存储新的配套楼号列表
        self.facilities = None  # 存储配套楼号数据
        self.ratio = 1.2  # 容积率系数

        self.dicts = None  # 存储计算指标字典输出结果
        self.output = None  # 存储输出dataframe结果
        self.diagnostics = None  # 存储输入数据诊断结果（不参与计算）

    def __repr__(self):
        return f"DesignMetrics({self.fold_path}, {self.city})"
        # 用于返回对象的字符串表示形式，可以通过print()函数输出
    def __str__(self):
        return f"DesignMetrics({self.fold_path}, {self.city})"
        # 用于返回对象的字符串表示形式，可以通过print()函数输出
    def load_data(self):
        """
        加载不同类型的数据，并进行交叉合并
        委派给 DataLoader 处理
        """
        loader = DataLoader(self.fold_path)
        loader.load_buildings()
        loader.load_related_data()
        
        # 保存到 self 以保持接口兼容
        self.buildings = loader.buildings
        self.attributes = loader.attributes
        self.key = loader.key
        self.hp_data = loader.hp_data
        self.info = loader.info
        
        # 合并分析数据
        self._merge_data()
        self._trans_ArchitectDataFrame()
        logger.info("Data loaded and merged. Total rows: %s", len(self.data))

    def _resolve_buildings_path(self, folder_path: Path) -> Path:
        for directory_name in self.BUILDINGS_DIR_NAMES:
            candidate = folder_path / directory_name
            if candidate.is_dir():
                return candidate
        raise FileNotFoundError(
            f"Buildings folder not found under: {self.fold_path}"
        )

    def _tweak_buildings(self):
        """已委派给 DataLoader.load_buildings()，此方法保留以兼容"""
        logger.debug("_tweak_buildings called but delegated to DataLoader")

    def _tweak_related_data(self):
        """已委派给 DataLoader.load_related_data()，此方法保留以兼容"""
        logger.debug("_tweak_related_data called but delegated to DataLoader")

    def _build_analysis_data(
        self, bonus_factor: float, bonus_list: Optional[list[str]] = None
    ) -> pl.DataFrame:
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

    def _refresh_building_views(self):
        self.buildings_list = (
            self.data.select(pl.col("楼号").cast(pl.String))
            .unique()
            .sort("楼号")["楼号"]
            .to_list()
        )
        self.bonus_list = (
            self.data.filter(pl.col("是否奖励楼栋") == "Y")
            .select(pl.col("楼号").cast(pl.String))
            .unique()
            .sort("楼号")["楼号"]
            .to_list()
        )
        self.facility_list = (
            self.data.filter(pl.col("类型") == "配套")
            .select(pl.col("楼号").cast(pl.String))
            .unique()
            .sort("楼号")["楼号"]
            .to_list()
        )
        self.facilities = self.data.filter(
            pl.col("楼号").is_in([str(i) for i in self.facility_list])
        )

    def _merge_data(self):
        """
        根据特定规则交叉合并不同类型的数据
        :return: 合并后的 DataFrame
        """
        self.data = self._build_analysis_data(self.bonus_factor)
        self._refresh_building_views()
        self._refresh_diagnostics()

    def _refresh_diagnostics(self):
        """重算输入数据诊断。只做诊断，不影响任何指标结果。"""
        self.diagnostics = build_diagnostics(self.buildings, self.attributes, self.key)

    def _trans_ArchitectDataFrame(self):

        self.data = self.ArchitectDataFrame(self.data)
        self.info = self.ArchitectDataFrame(self.info)
        self.facilities = self.ArchitectDataFrame(self.facilities)

        if (
            not isinstance(self.data, pl.DataFrame)
            or not isinstance(self.info, pl.DataFrame)
            or not isinstance(self.facilities, pl.DataFrame)
        ):
            raise ValueError("data or info must be a polars.DataFrame.")
        logger.debug("Data converted to ArchitectDataFrame.")

# ____________________________________________________________________________________________________________________
    # 为交互更新奖励系数、奖励楼号列表和配套楼号列表提供方法

    def update_factor(self, new_factor: float):
        if new_factor <= 0:
            raise ValueError("Factor must be a positive number.")
        self.bonus_factor = new_factor
        logger.debug("Factor updated to: %s", self.bonus_factor)

    def update_ratio(self, new_ratio: float):
        if new_ratio <= 0:
            raise ValueError("Ratio must be a positive number.")
        self.ratio = new_ratio
        logger.debug("Ratio updated to: %s", self.ratio)

    def update_bonus_list(self, new_bonus_list: list):
        # if not isinstance(new_bonus_list, list):
        #     raise ValueError("Bonus list must be a list.")
        self.new_bonus_list = list(new_bonus_list)
        self.new_bonus_list = [str(i) for i in self.new_bonus_list]
        logger.debug("Bonus list updated to: %s", self.new_bonus_list)

    def update_facility_list(self, new_facility_list: list):
        # if not isinstance(new_facility_list, list):
        #     raise ValueError("Facility list must be a list.")
        self.new_facility_list = list(new_facility_list)
        self.new_facility_list = [str(i) for i in self.new_facility_list]
        logger.debug("Facility list updated to: %s", self.new_facility_list)

    def update_data(self):
        """
        更新数据
        step1. 重置数据
        step2. 根据新的奖励系数和奖励楼号列表更新数据
        step3. 更新配套楼号列表
        """
        if self.new_bonus_list is not None:
            self.reset_data = self._build_analysis_data(1.0)
            self.data = self._build_analysis_data(
                self.bonus_factor, bonus_list=self.new_bonus_list
            )
            self._refresh_building_views()
            self._refresh_diagnostics()

        if self.new_facility_list is not None:
            self.facility_list = [str(i) for i in self.new_facility_list]
            self.facilities = self.data.filter(
                pl.col("楼号").is_in(self.facility_list)
            )

        self.data = self.ArchitectDataFrame(self.data)
        self.facilities = self.ArchitectDataFrame(self.facilities)

# ——————————————————————————————————————————————————————————————————————————————————————
    def calculate_metrics(self):
        """
        根据城市的规则计算指标。不同城市可以有不同的规则。
        委派给 CityRules 处理
        """
        if self.data is None:
            raise ValueError(
                "No data loaded. Please load data first using load_data()."
            )

        city_key_map = {
            "sh": "sh",
            "上海": "sh",
            "cs": "cs",
            "长沙": "cs",
            "hf": "hf",
            "合肥": "hf",
            "gy": "gy",
            "贵阳": "gy",
        }
        city_key = city_key_map.get(self.city)
        if city_key is None:
            raise ValueError(f"Unsupported city: {self.city}")

        rules = CityRules(
            self.data,
            self.hp_data,
            self.info,
            self.facilities,
            self.bonus_factor,
            self.ratio,
        )
        helper = MetricsHelper(self.data, self.hp_data, self.info)

        method_map = {
            "sh": rules.calculate_sh_metrics,
            "cs": rules.calculate_cs_metrics,
            "hf": rules.calculate_hf_metrics,
            "gy": rules.calculate_gy_metrics,
        }
        metric_pool = method_map[city_key](helper)
        self.dicts = self._apply_output_config(city_key, metric_pool)

    def metrics_output(self):
        """
        输出指标
        """

        df_output = (
            pl.DataFrame(
                {"指标名称": list(self.dicts.keys()), "数值": list(self.dicts.values())}
            )
            .with_columns(pl.col("指标名称").cast(pl.Categorical()))
            .with_columns(pl.col("数值").cast(pl.Float64).round(2))
            .filter(pl.col("指标名称").is_not_null())
        )
        info_out = self.info.with_columns(pl.col("数值").cast(pl.Float64).round(2))

        # 合并输出结果
        outputs = (
            pl.concat([info_out, df_output])
            .filter(pl.col("数值") != 0)
            .unique(subset=["指标名称"], keep="first")
            .sort("数值", descending=True)
        )
        # 实例化 ArchitectDataFrame 类，用df.get()方法获取数据
        self.output = outputs
        self.output = self.ArchitectDataFrame(self.output)

    def _safe_affordable_households(self) -> float:
        guarantee_columns = [
            col_name for col_name in self.hp_data.columns if col_name.startswith("保障")
        ]
        if not guarantee_columns:
            return 0
        return (
            self.hp_data.select(guarantee_columns)
            .sum_horizontal(ignore_nulls=True)
            .sum()
        )

    def _total_households(self) -> float:
        return self.hp_data.sum().select(pl.sum_horizontal(pl.all()))[0, 0]

    def _build_household_metrics(self) -> dict:
        total_households = self._total_households()
        affordable_households = self._safe_affordable_households()
        return {
            "总户数": total_households,
            "保障房户数": affordable_households,
            "商品房户数": total_households - affordable_households,
        }

    def _build_parking_metrics(self, garage_area: float) -> dict:
        civil_defense_area = self.info.get("人防地下室面积")
        underground_parking_count = self.info.get("地下机动车数量")
        civil_defense_parking = civil_defense_area / 40
        ground_parking_count = self.info.get("地面停车")
        return {
            "地库面积": garage_area,
            "人防地下室面积": civil_defense_area,
            "非人防地下室面积": garage_area - civil_defense_area,
            "地下机动车数量": underground_parking_count,
            "人防车位数": civil_defense_parking,
            "非人防车位数": underground_parking_count - civil_defense_parking,
            "地面停车": ground_parking_count,
            "机动车总数": ground_parking_count + underground_parking_count,
        }

    def _build_site_metrics(self) -> dict:
        land_area = self.info.get("用地面积")
        green_area = self.info.get("绿地面积")
        facility_base_area = self.info.get("配套基底面积")
        commercial_base_area = self.info.get("商业基底面积")
        residential_base_area = self.data.query_area(floor="1f", atype="physical")
        building_base_area = (
            residential_base_area + facility_base_area + commercial_base_area
        )
        return {
            "用地面积": land_area,
            "绿地面积": green_area,
            "配套基底面积": facility_base_area,
            "商业基底面积": commercial_base_area,
            "住宅基底面积": residential_base_area,
            "建筑基底面积": building_base_area,
            "建筑密度": building_base_area / land_area,
            "绿地率": green_area / land_area,
        }

    def _build_basic_area_metrics(self) -> dict:
        return {
            "S_计容面积": self.data.query_area(atype="jr"),
            "销售面积": self.data.query_area(atype="sale"),
            "物理面积": self.data.query_area(atype="physical"),
            "赠送面积": self.data.query_area(atype="free"),
        }

    def _build_underground_metrics(self, garage_area: float) -> dict:
        civil_defense_area = self.info.get("人防地下室面积")
        underground_parking_count = self.info.get("地下机动车数量")
        return {
            "地库面积": garage_area,
            "人防地下室面积": civil_defense_area,
            "地下机动车数量": underground_parking_count,
            "人防车位数": civil_defense_area / 40,
            "非人防地下室面积": garage_area - civil_defense_area,
        }

    def _compose_metrics(self, *metric_groups: dict) -> dict:
        composed_metrics = {}
        for metrics in metric_groups:
            composed_metrics.update(metrics)
        return composed_metrics

    def _apply_output_config(self, city_key: str, metric_pool: dict) -> dict:
        configured_fields = self.OUTPUT_FIELD_CONFIG.get(city_key)
        if configured_fields is None:
            return metric_pool

        missing_fields = [
            field_name for field_name in configured_fields if field_name not in metric_pool
        ]
        if missing_fields:
            logger.warning(
                "Missing configured output fields for city %s: %s",
                city_key,
                ", ".join(missing_fields),
            )

        return {
            field_name: metric_pool[field_name]
            for field_name in configured_fields
            if field_name in metric_pool
        }

    def compare(self, other):
        """比较两个 DesignMetric 实例的结果"""
        if not isinstance(other, DesignMetrics):
            raise ValueError("比较对象必须是 DesignMetric 实例")
        if self.city != other.city:
            logger.warning("无法比较不同城市的结果: %s vs %s", self.city, other.city)
        else:
            # 获取各自的 metrics
            metrics_self = self.dicts
            metrics_other = other.dicts

            # 将对比结果放入 DataFrame
            comparison_df = (
                pl.DataFrame(
                    {
                        "Metric": list(metrics_self.keys()),
                        "Project_A": list(metrics_self.values()),
                        "Project_B": list(metrics_other.values()),
                        "Difference": [
                            round(metrics_self[key] - metrics_other[key], 2)
                            for key in metrics_self
                        ],
                    }
                )
                .with_columns(Percent=pl.col("Difference") / pl.col("Project_A") * 100)
                .with_columns(pl.col("Project_A").round(2))
                .with_columns(pl.col("Project_B").round(2))
                .with_columns(pl.col("Percent").round(2))
            )
            return comparison_df

# 城市计算规则:___________________________________________________________________________
    def _calculate_city_sh_metrics(self):
        """
        上海的计算规则
        """
        # 查询建筑面积
        basic_area_metrics = self._build_basic_area_metrics()
        household_metrics = self._build_household_metrics()
        S_计容面积 = basic_area_metrics["S_计容面积"]
        用地面积 = self.info.get("用地面积")
        不计容配套面积 = self.info.get("不计容配套面积")
        计容面积 = S_计容面积 - 不计容配套面积
        配套总面积 = self.facilities.query_area(atype="jr") - 不计容配套面积
        奖励后计容面积 = round(
            (
                self.data.query_area(bonus="n", atype="jr")
                + self.data.query_area(bonus="y", atype="jr") * self.bonus_factor
                - 不计容配套面积
            ),
            2,
        )
        奖励前计容面积 = 计容面积
        奖励面积 = round(奖励后计容面积 - 奖励前计容面积, 2)
        屋顶机房面积 = self.data.query_area(key="屋顶机房")
        s_不奖励楼栋计容面积 = self.data.query_area(bonus="N", atype="jr")
        s_奖励楼栋计容面积 = self.data.query_area(bonus="Y", atype="jr")
        保障房不计容半阳台 = self.data.query_area(btype="BZF", key="不计容阳台") * 0.5
        地上总建筑面积 = (
            奖励后计容面积 + 屋顶机房面积 + 保障房不计容半阳台 + 不计容配套面积
        )
        保障房屋顶机房 = self.data.query_area(btype="BZF", key="屋顶机房") * 0.5
        住宅总建筑面积 = 奖励后计容面积 - 配套总面积 + 屋顶机房面积 + 保障房不计容半阳台
        容积率 = round(计容面积 / 用地面积, 3)
        电缆夹层面积 = self.info.get("电缆夹层面积")
        地库面积 = self.data.query_area(key="地库主体") + 电缆夹层面积
        underground_metrics = self._build_underground_metrics(地库面积)
        建筑基底面积 = self.data.query_area(floor="1f", atype="physical")
        住宅基底面积 = self.data.query_area(
            floor="1f", atype="physical"
        ) - self.facilities.query_area(floor="1f", atype="physical")
        总建筑面积 = 地上总建筑面积 + 地库面积
        地上电梯厅 = self.data.query_area(key="电梯厅")
        地库大堂面积 = self.data.query_area(key="地库大堂")
        地上连廊面积 = self.data.query_area(key="连廊")
        首层大堂 = self.data.query_area(key="首层大堂")
        地上楼梯间 = self.data.query_area(key="楼梯间")
        S_建筑工程面积 = S_计容面积 + 奖励面积 + 屋顶机房面积
        S_计一半面积 = self.data.filter(pl.col("计容") == 0.5).query_area(atype="jr")
        地下储藏间 = self.data.query_area(key="储藏间", atype="free")
        套内面积 = self.data.query_area(key="套内")
        保障房计容面积 = self.data.query_area(btype="BZF", atype="jr")
        联排计容面积 = self.data.query_area(btype="LP", atype="jr")
        洋房计容面积 = self.data.query_area(btype="YF", atype="jr")
        联排奖励面积 = self.data.query_area(btype="LP", atype="jr") * (
            self.bonus_factor - 1
        )
        洋房屋顶机房面积 = self.data.query_area(btype="YF", key="屋顶机房")
        门卫面积 = self.info.get("门卫")  # 补充信息中增加门卫面积

        差值 = 用地面积 * self.ratio - 计容面积

        metric_pool = self._compose_metrics(
            {"差值": 差值},
            basic_area_metrics,
            {
                "用地面积": 用地面积,
                "不计容配套面积": 不计容配套面积,
                "计容面积": 计容面积,
                "配套总面积": 配套总面积,
                "奖励后计容面积": 奖励后计容面积,
                "奖励前计容面积": 奖励前计容面积,
                "奖励面积": 奖励面积,
                "屋顶机房面积": 屋顶机房面积,
                "s_不奖励楼栋计容面积": s_不奖励楼栋计容面积,
                "s_奖励楼栋计容面积": s_奖励楼栋计容面积,
                "保障房不计容半阳台": 保障房不计容半阳台,
                "地上总建筑面积": 地上总建筑面积,
                "保障房屋顶机房": 保障房屋顶机房,
                "住宅总建筑面积": 住宅总建筑面积,
                "容积率": 容积率,
                "电缆夹层面积": 电缆夹层面积,
            },
            underground_metrics,
            {
                "建筑基底面积": 建筑基底面积,
                "住宅基底面积": 住宅基底面积,
                "总建筑面积": 总建筑面积,
                "地上电梯厅": 地上电梯厅,
                "地库大堂面积": 地库大堂面积,
                "地上连廊面积": 地上连廊面积,
                "首层大堂": 首层大堂,
                "地上楼梯间": 地上楼梯间,
                "S_建筑工程面积": S_建筑工程面积,
                "S_计一半面积": S_计一半面积,
                "地下储藏间": 地下储藏间,
                "套内面积": 套内面积,
                "保障房计容面积": 保障房计容面积,
                "联排计容面积": 联排计容面积,
                "洋房计容面积": 洋房计容面积,
                "联排奖励面积": 联排奖励面积,
                "洋房屋顶机房面积": 洋房屋顶机房面积,
                "门卫": 门卫面积,
            },
            household_metrics,
        )
        self.dicts = self._apply_output_config("sh", metric_pool)
# ——————————————————————————————————————————————————————————————————————————————————————
    def _calculate_city_cs_metrics(self):
        """
        长沙的计算规则
        """
        # 查询建筑面积
        计容住宅面积 = self.data.query_area(atype="jr")
        屋顶机房面积 = self.data.query_area(key="屋顶机房")
        地下楼梯间面积 = self.data.query_area(key="地下楼梯间")
        架空面积 = self.data.query_area(key="架空层")

        不计容住宅面积 = 架空面积 + 屋顶机房面积 + 地下楼梯间面积

        商业面积 = self.info.get("商业面积")
        计容配套面积 = self.info.get("计容配套面积")  # 补充信息,自定义列表无效
        不计容配套面积 = self.info.get("不计容配套面积")  # 补充信息,自定义列表无效
        骑楼面积 = self.info.get("骑楼面积")
        销售面积 = self.data.query_area(atype="sale")
        住宅物理面积 = self.data.query_area(atype="physical")
        赠送面积 = self.data.query_area(atype="free")

        地库面积 = self.info.get("地库面积")
        parking_metrics = self._build_parking_metrics(地库面积)
        site_metrics = self._build_site_metrics()
        household_metrics = self._build_household_metrics()
        用地面积 = site_metrics["用地面积"]

        配套总面积 = 计容配套面积 + 不计容配套面积
        计容总面积 = 计容住宅面积 + 计容配套面积 + 商业面积
        地上建筑面积 = (
            计容住宅面积
            + 不计容住宅面积
            + 商业面积
            + 计容配套面积
            + 不计容配套面积
            + 骑楼面积
        )
        总建筑面积 = (
            计容住宅面积
            + 不计容住宅面积
            + 商业面积
            + 计容配套面积
            + 不计容配套面积
            + 骑楼面积
            + 地库面积
        )

        容积率 = round(计容总面积 / 用地面积, 2)

        差值 = 用地面积 * self.ratio - 计容总面积

        metric_pool = self._compose_metrics(
            {
                "差值": 差值,
                "计容住宅面积": 计容住宅面积,
                "屋顶机房面积": 屋顶机房面积,
                "地下楼梯间面积": 地下楼梯间面积,
                "架空面积": 架空面积,
                "不计容住宅面积": 不计容住宅面积,
                "商业面积": 商业面积,
                "计容配套面积": 计容配套面积,
                "不计容配套面积": 不计容配套面积,
                "地库面积": 地库面积,
                "骑楼面积": 骑楼面积,
                "销售面积": 销售面积,
                "住宅物理面积": 住宅物理面积,
                "赠送面积": 赠送面积,
            },
            parking_metrics,
            site_metrics,
            {
                "容积率": 容积率,
                "配套总面积": 配套总面积,
                "计容总面积": 计容总面积,
                "地上建筑面积": 地上建筑面积,
                "总建筑面积": 总建筑面积,
            },
            household_metrics,
        )
        self.dicts = self._apply_output_config("cs", metric_pool)
# ——————————————————————————————————————————————————————————————————————————————————————
    def _calculate_city_hf_metrics(self):
        """
        合肥的计算规则
        """
        # 查询建筑面积
        地上住宅面积 = self.data.query_area(atype="jr")
        架空面积 = self.data.query_area(key="架空层")
        保障房面积 = self.data.query_area(btype="BZF", atype="jr")

        住宅计容面积 = (地上住宅面积-保障房面积) * 0.97 - 架空面积 * 0.03 + 保障房面积
        
        住宅装配式奖励面积 = (地上住宅面积-保障房面积+架空面积) * 0.03


        商业面积 = self.info.get("商业面积")
        计容配套面积 = self.info.get("计容配套面积")  # 补充信息,自定义列表无效
        不计容配套面积 = self.info.get("不计容配套面积")  # 补充信息,自定义列表无效
        销售面积 = self.data.query_area(atype="sale")
        住宅物理面积 = self.data.query_area(atype="physical")
        赠送面积 = self.data.query_area(atype="free")

        地库面积 = self.info.get("地库面积")
        parking_metrics = self._build_parking_metrics(地库面积)
        site_metrics = self._build_site_metrics()
        household_metrics = self._build_household_metrics()
        用地面积 = site_metrics["用地面积"]

        配套总面积 = 计容配套面积 + 不计容配套面积
        计容总面积 = 住宅计容面积 + 计容配套面积 + 商业面积
        地上建筑面积 = (地上住宅面积 + 配套总面积 + 商业面积 + 架空面积)
        总建筑面积 = (地上住宅面积 + 配套总面积 + 商业面积 + 架空面积 + 地库面积)

        容积率 = round(计容总面积 / 用地面积, 2)

        差值 = 用地面积 * self.ratio - 计容总面积

        metric_pool = self._compose_metrics(
            {
                "差值": 差值,
                "住宅计容面积": 住宅计容面积,
                "架空面积": 架空面积,
                "保障房面积": 保障房面积,
                "住宅装配式奖励面积": 住宅装配式奖励面积,
                "商业面积": 商业面积,
                "计容配套面积": 计容配套面积,
                "不计容配套面积": 不计容配套面积,
                "地库面积": 地库面积,
                "销售面积": 销售面积,
                "住宅物理面积": 住宅物理面积,
                "赠送面积": 赠送面积,
            },
            parking_metrics,
            site_metrics,
            {
                "容积率": 容积率,
                "配套总面积": 配套总面积,
                "计容总面积": 计容总面积,
                "地上建筑面积": 地上建筑面积,
                "总建筑面积": 总建筑面积,
            },
            household_metrics,
        )
        self.dicts = self._apply_output_config("hf", metric_pool)
# ——————————————————————————————————————————————————————————————————————————————————————
    def _calculate_city_gy_metrics(self):
        """
        贵阳的计算规则
        """
        # 查询建筑面积
        basic_area_metrics = self._build_basic_area_metrics()
        S_计容面积 = basic_area_metrics["S_计容面积"]
        用地面积 = self.info.get("用地面积")
        计容面积 = S_计容面积
        配套总面积 = self.facilities.query_area(atype="jr")
        屋顶机房面积 = self.data.query_area(key="屋顶机房")
        架空层面积 = self.data.query_area(key="架空层")
        商办面积 = self.data.query_area(key="商办")
        地上总建筑面积 = (计容面积 + 架空层面积)
        住宅总建筑面积 = (地上总建筑面积  - 商办面积)
        容积率 = round(计容面积 / 用地面积, 3)
        非人防地下室面积 = self.info.get("地下停车场面积")
        地库面积 = 非人防地下室面积 + self.info.get("人防地下室面积")
        underground_metrics = self._build_underground_metrics(地库面积)
        建筑基底面积 = self.data.query_area(floor="1f", atype="physical")
        总建筑面积 = 地上总建筑面积 + 地库面积
        总户数 = self._total_households()
        差值 = 用地面积 * self.ratio - 计容面积

        metric_pool = self._compose_metrics(
            {"差值": 差值},
            basic_area_metrics,
            {
                "用地面积": 用地面积,
                "计容面积": 计容面积,
                "配套总面积": 配套总面积,
                "屋顶机房面积": 屋顶机房面积,
                "地上总建筑面积": 地上总建筑面积,
                "住宅总建筑面积": 住宅总建筑面积,
                "商办面积": 商办面积,
                "容积率": 容积率,
            },
            underground_metrics,
            {
                "建筑基底面积": 建筑基底面积,
                "总建筑面积": 总建筑面积,
                "非人防地下室面积": 非人防地下室面积,
                "总户数": 总户数,
                "架空层": 架空层面积,
            },
        )
        self.dicts = self._apply_output_config("gy", metric_pool)
#_______________________________________________________________________________________
