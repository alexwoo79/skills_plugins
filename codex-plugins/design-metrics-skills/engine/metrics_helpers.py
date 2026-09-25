"""
通用指标计算助手
提供各个城市规则都会用到的通用指标计算方法
"""

import logging

logger = logging.getLogger(__name__)


class MetricsHelper:
    """通用指标计算helpers"""

    def __init__(self, data, hp_data, info):
        """
        初始化 helper
        :param data: ArchitectDataFrame，建筑分析数据
        :param hp_data: 户配汇总
        :param info: 补充信息
        """
        self.data = data
        self.hp_data = hp_data
        self.info = info

    def _safe_affordable_households(self) -> float:
        """安全获取保障房户数，列不存在则返回 0"""
        guarantee_columns = [
            col_name
            for col_name in self.hp_data.columns
            if col_name.startswith("保障")
        ]
        if not guarantee_columns:
            return 0
        return (
            self.hp_data.select(guarantee_columns)
            .sum_horizontal(ignore_nulls=True)
            .sum()
        )

    def _total_households(self) -> float:
        """总户数"""
        return self.hp_data.sum().select(__import__('polars').sum_horizontal(__import__('polars').all()))[0, 0]

    def build_household_metrics(self) -> dict:
        """建立户数相关指标"""
        total_households = self._total_households()
        affordable_households = self._safe_affordable_households()
        return {
            "总户数": total_households,
            "保障房户数": affordable_households,
            "商品房户数": total_households - affordable_households,
        }

    def build_parking_metrics(self, garage_area: float) -> dict:
        """建立车位相关指标（长沙、合肥通用）"""
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

    def build_site_metrics(self) -> dict:
        """建立场地相关指标（长沙、合肥通用）"""
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

    def build_basic_area_metrics(self) -> dict:
        """建立基础面积指标（上海、贵阳通用）"""
        return {
            "S_计容面积": self.data.query_area(atype="jr"),
            "销售面积": self.data.query_area(atype="sale"),
            "物理面积": self.data.query_area(atype="physical"),
            "赠送面积": self.data.query_area(atype="free"),
        }

    def build_underground_metrics(self, garage_area: float) -> dict:
        """建立地下指标（上海、贵阳通用）"""
        civil_defense_area = self.info.get("人防地下室面积")
        underground_parking_count = self.info.get("地下机动车数量")
        return {
            "地库面积": garage_area,
            "人防地下室面积": civil_defense_area,
            "地下机动车数量": underground_parking_count,
            "人防车位数": civil_defense_area / 40,
            "非人防地下室面积": garage_area - civil_defense_area,
        }

    @staticmethod
    def compose_metrics(*metric_groups: dict) -> dict:
        """组合多个指标字典"""
        composed_metrics = {}
        for metrics in metric_groups:
            composed_metrics.update(metrics)
        return composed_metrics
