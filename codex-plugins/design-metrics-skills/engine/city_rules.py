"""
城市计算规则集合
各城市独特的建筑指标计算逻辑
"""

import logging

logger = logging.getLogger(__name__)


class CityRules:
    """城市规则集合"""

    def __init__(self, data, hp_data, info, facilities, bonus_factor, ratio):
        """
        初始化计算上下文
        :param data: ArchitectDataFrame，建筑分析数据
        :param hp_data: 户配汇总
        :param info: 补充信息
        :param facilities: 配套建筑数据
        :param bonus_factor: 奖励系数
        :param ratio: 容积率系数
        """
        self.data = data
        self.hp_data = hp_data
        self.info = info
        self.facilities = facilities
        self.bonus_factor = bonus_factor
        self.ratio = ratio

    def calculate_sh_metrics(self, helper) -> dict:
        """上海的计算规则"""
        basic_area_metrics = helper.build_basic_area_metrics()
        household_metrics = helper.build_household_metrics()
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
        住宅总建筑面积 = (
            奖励后计容面积 - 配套总面积 + 屋顶机房面积 + 保障房不计容半阳台
        )
        容积率 = round(计容面积 / 用地面积, 3)
        电缆夹层面积 = self.info.get("电缆夹层面积")
        地库面积 = self.data.query_area(key="地库主体") + 电缆夹层面积
        underground_metrics = helper.build_underground_metrics(地库面积)
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
        import polars as pl

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
        门卫面积 = self.info.get("门卫")

        差值 = 用地面积 * self.ratio - 计容面积

        return helper.compose_metrics(
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

    def calculate_cs_metrics(self, helper) -> dict:
        """长沙的计算规则"""
        计容住宅面积 = self.data.query_area(atype="jr")
        屋顶机房面积 = self.data.query_area(key="屋顶机房")
        地下楼梯间面积 = self.data.query_area(key="地下楼梯间")
        架空面积 = self.data.query_area(key="架空层")
        不计容住宅面积 = 架空面积 + 屋顶机房面积 + 地下楼梯间面积

        商业面积 = self.info.get("商业面积")
        计容配套面积 = self.info.get("计容配套面积")
        不计容配套面积 = self.info.get("不计容配套面积")
        骑楼面积 = self.info.get("骑楼面积")
        销售面积 = self.data.query_area(atype="sale")
        住宅物理面积 = self.data.query_area(atype="physical")
        赠送面积 = self.data.query_area(atype="free")

        地库面积 = self.info.get("地库面积")
        parking_metrics = helper.build_parking_metrics(地库面积)
        site_metrics = helper.build_site_metrics()
        household_metrics = helper.build_household_metrics()
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

        return helper.compose_metrics(
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

    def calculate_hf_metrics(self, helper) -> dict:
        """合肥的计算规则"""
        import polars as pl

        地上住宅面积 = self.data.query_area(atype="jr")
        架空面积 = self.data.query_area(key="架空层")
        保障房面积 = self.data.query_area(btype="BZF", atype="jr")

        住宅计容面积 = (
            (地上住宅面积 - 保障房面积) * 0.97 - 架空面积 * 0.03 + 保障房面积
        )
        住宅装配式奖励面积 = (地上住宅面积 - 保障房面积 + 架空面积) * 0.03

        商业面积 = self.info.get("商业面积")
        计容配套面积 = self.info.get("计容配套面积")
        不计容配套面积 = self.info.get("不计容配套面积")
        销售面积 = self.data.query_area(atype="sale")
        住宅物理面积 = self.data.query_area(atype="physical")
        赠送面积 = self.data.query_area(atype="free")

        地库面积 = self.info.get("地库面积")
        parking_metrics = helper.build_parking_metrics(地库面积)
        site_metrics = helper.build_site_metrics()
        household_metrics = helper.build_household_metrics()
        用地面积 = site_metrics["用地面积"]

        配套总面积 = 计容配套面积 + 不计容配套面积
        计容总面积 = 住宅计容面积 + 计容配套面积 + 商业面积
        地上建筑面积 = 地上住宅面积 + 配套总面积 + 商业面积 + 架空面积
        总建筑面积 = 地上住宅面积 + 配套总面积 + 商业面积 + 架空面积 + 地库面积

        容积率 = round(计容总面积 / 用地面积, 2)
        差值 = 用地面积 * self.ratio - 计容总面积

        return helper.compose_metrics(
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

    def calculate_gy_metrics(self, helper) -> dict:
        """贵阳的计算规则"""
        basic_area_metrics = helper.build_basic_area_metrics()
        S_计容面积 = basic_area_metrics["S_计容面积"]
        用地面积 = self.info.get("用地面积")
        计容面积 = S_计容面积
        配套总面积 = self.facilities.query_area(atype="jr")
        屋顶机房面积 = self.data.query_area(key="屋顶机房")
        架空层面积 = self.data.query_area(key="架空层")
        商办面积 = self.data.query_area(key="商办")
        地上总建筑面积 = 计容面积 + 架空层面积
        住宅总建筑面积 = 地上总建筑面积 - 商办面积
        容积率 = round(计容面积 / 用地面积, 3)
        非人防地下室面积 = self.info.get("地下停车场面积")
        地库面积 = 非人防地下室面积 + self.info.get("人防地下室面积")
        underground_metrics = helper.build_underground_metrics(地库面积)
        建筑基底面积 = self.data.query_area(floor="1f", atype="physical")
        总建筑面积 = 地上总建筑面积 + 地库面积
        总户数 = helper._total_households()
        差值 = 用地面积 * self.ratio - 计容面积

        return helper.compose_metrics(
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
