from src.data.FeatureList import FeatureList as fL  # 复用地图传送点模板


class EnvironmentMonitoringRoute:  # 共用传送后移动与状态检查
    photo_direction = "right"  # 默认向右取景

    def __init__(self, task):  # 复用所属任务的输入与生命周期
        self.task = task  # 不创建独立执行器

    def transfer(self):  # 默认使用委托的快捷传送
        self.task.open_observation_map()  # 共用任务追踪与地图检查
        if not self.task.to_near_transfer_point(need_track=False):  # 使用快捷传送
            raise RuntimeError("环境监测快捷传送失败")  # 不在传送失败后继续
        self.confirm_arrival()  # 验证传送完成

    def transfer_from_map_region(self, region, region_name):  # 按指定地图区域选择传送点
        task = self.task  # 复用所属任务的地图操作
        task.open_observation_map()  # 共用任务追踪与地图检查
        if not task.clear_icon_in_map():  # 清空地图标记筛选
            raise RuntimeError("清空地图标记失败")  # 避免标记遮挡传送点
        if not task.zoom_map_out():  # 缩小地图并等待稳定
            raise RuntimeError("地图缩放后未稳定")  # 不在过渡画面识别
        if not task.wait_click_feature(
            fL.transfer_point,
            box=task.box_of_screen(*region),
            threshold=0.7,
            time_out=5,
            raise_if_not_found=False,
        ):  # 在指定区域查找传送点
            raise RuntimeError(f"未找到{region_name}传送点")  # 不盲目传送
        if not task._wait_click_transfer_go():  # 点击所选传送点的传送按钮
            raise RuntimeError("未找到传送按钮")  # 不把选中当成传送
        self.confirm_arrival()  # 验证传送完成

    def confirm_arrival(self):  # 共用传送到达检查
        if not self.task._wait_for_transfer_arrival():  # 等待大世界稳定出现
            raise RuntimeError("环境监测传送后未确认到达")  # 不在异常界面继续

    def hold_route_key(self, key, duration, sprint=False):  # 执行可中止的移动操作
        task = self.task  # 使用所属任务输入
        task._require_input_focus()  # 每段移动前确认焦点
        actual_key = task._resolve_config_key(key, "common")  # 遵循用户移动键绑定
        task.send_key_down(actual_key)  # 开始保持移动方向
        try:  # 中止、冲刺输入或等待异常都必须松键
            if sprint:  # 仅需要冲刺的路线启动加速
                task.press_key("shift", after_sleep=0.05)  # 沿用网格导航的冲刺方式
            task.sleep(duration)  # 使用可响应任务停止的等待
        finally:  # 配对清理输入
            task.send_key_up(actual_key)  # 不留下按住的移动键

    def run_route(self, name, steps, sprint=False):  # 共用起步、分段移动和结束检查
        task = self.task  # 使用所属任务的大世界状态
        if not task.in_world():  # 必须从传送后的大世界起步
            raise RuntimeError(f"{name}路线未处于大世界")  # 不在菜单内发送移动
        for key, duration in steps:  # 各点位仅提供已确认的方向与时长
            self.hold_route_key(key, duration, sprint=sprint)  # 每段移动结束即松键
        if not task.wait_until(task.in_world, time_out=2, settle_time=0.5, raise_if_not_found=False):  # 等待大世界稳定
            raise RuntimeError(f"{name}路线结束后未恢复大世界")  # 不在异常状态进入相机


class PoolLotusRoute(EnvironmentMonitoringRoute):  # 小池荷传送后短路线
    def run(self):  # 向前一步，再向右走到竹子旁
        self.run_route("小池荷", (("w", 0.5), ("d", 3)))  # 保留已验证的移动时长


class MysteriousGraffitiRoute(EnvironmentMonitoringRoute):  # 谜之生物涂鸦传送后路线
    photo_direction = "down"  # 向下取景

    def run(self):  # 传送后前进
        self.run_route("谜之生物的涂鸦", (("w", 2),), sprint=True)  # 向前冲刺两秒


class GlowingLankybeastRoute(EnvironmentMonitoringRoute):  # 吐虫长股兽传送后短路线
    def run(self):  # 传送后向后、向右移动
        self.run_route("吐虫长股兽", (("s", 1), ("d", 3)), sprint=True)  # 已验证的冲刺路线


class CisternOriginiumSlugsRoute(EnvironmentMonitoringRoute):  # 普通蓄水源石虫传送后短路线
    photo_direction = "left"  # 向左取景

    def run(self):  # 保持传送后的朝向，向后冲刺到取景位置
        self.run_route("蓄水源石虫", (("s", 7),), sprint=True)  # 后退冲刺到取景位置


class VigorousCisternSlugRoute(EnvironmentMonitoringRoute):  # 活力蓄水源石虫路线
    photo_direction = "left"  # 向左取景

    def run(self):  # 导航到记录的拍摄位置
        if not self.task.navigate_grid_to((-1068.79, -559.33), map_id="map02"):  # 使用武陵网格
            raise RuntimeError("充满活力的蓄水源石虫导航失败")  # 导航失败时停止拍照


class AncientTreeRoute(EnvironmentMonitoringRoute):  # 古树世界坐标路线
    photo_direction = "up"  # 向上取景

    def run(self):  # 导航至记录的拍摄位置
        if not self.task.navigate_grid_to((-957.16, -1636.97), map_id="map02"):  # 使用武陵网格
            raise RuntimeError("古树导航失败")  # 导航失败时停止拍照


class AnnularWaterfallRoute(EnvironmentMonitoringRoute):  # 环形瀑布传送后路线
    photo_direction = "up"  # 向上取景

    def run(self):  # 先左走一步，再按顺序冲刺
        self.run_route("环形瀑布", (("a", 0.5),))  # 一步暂按半秒估计
        self.run_route("环形瀑布", (("w", 5), ("d", 2), ("w", 1)), sprint=True)  # 按指定时间冲刺


class XiraniteNexusRoute(EnvironmentMonitoringRoute):  # 枢壤仪世界坐标路线
    def run(self):  # 导航至记录的拍摄位置
        if not self.task.navigate_grid_to((-1433.03, -503.42), map_id="map02"):  # 使用武陵导航网格
            raise RuntimeError("枢壤仪导航失败")  # 不在错误位置打开相机


class MiniShellbeastRoute(EnvironmentMonitoringRoute):  # 小壳兽世界坐标路线
    photo_direction = "left"  # 向左取景

    def run(self):  # 导航至记录的拍摄位置
        if not self.task.navigate_grid_to((-1454.48, -545.85), map_id="map02"):  # 使用武陵导航网格
            raise RuntimeError("小壳兽导航失败")  # 不在错误位置打开相机


class TianshiPillarRoute(EnvironmentMonitoringRoute):  # 天师桩世界坐标路线
    photo_direction = "right"  # 向右取景

    def run(self):  # 导航至记录的拍摄位置
        if not self.task.navigate_grid_to((-1166.03, -1711.93), map_id="map02"):  # 使用武陵导航网格
            raise RuntimeError("天师桩导航失败")  # 不在错误位置打开相机


class EternalSunsetRoute(EnvironmentMonitoringRoute):  # 栖霞驻影世界坐标路线
    photo_direction = "up"  # 向上取景

    def transfer(self):  # 栖霞驻影使用左上方传送点
        self.transfer_from_map_region((0.05, 0.02, 0.5, 0.5), "左上方")  # 复用地图传送流程

    def run(self):  # 导航至已实测的取景位置
        if not self.task.navigate_grid_to((-908.95, -1179.31), map_id="map02"):  # 使用武陵导航网格
            raise RuntimeError("栖霞驻影导航失败")  # 不在错误位置打开相机


class StationaryObservationRoute(EnvironmentMonitoringRoute):  # 传送落点即可取景
    def run(self):  # 原地等待传送后大世界稳定
        self.run_route("原地取景", ())  # 原地等待大世界稳定


class AutumnGoldRoute(EnvironmentMonitoringRoute):
    photo_direction = "right"

    def run(self):
        if not self.task.navigate_grid_to((-1628.62, -1267.79), map_id="map02"):
            raise RuntimeError("金秋时导航失败")


class WaterTemperatureControllerRoute(EnvironmentMonitoringRoute):
    photo_direction = "left"

    def run(self):
        if not self.task.navigate_grid_to((-1595.80, -1081.94), map_id="map02"):
            raise RuntimeError("净水温控装置导航失败")


class HangingVinesRoute(EnvironmentMonitoringRoute):  # 垂藤的地图传送入口
    def transfer(self):  # 垂藤从地图右下半区寻找传送点
        self.transfer_from_map_region((0.5, 0.5, 1.0, 1.0), "右下方")  # 复用清标记、缩图和传送确认

    photo_direction = "right"  # 向右搜索垂藤

    def run(self):  # 导航至切片还原的拍摄终点
        if not self.task.navigate_grid_to((-1359.5, -1127.5), map_id="map02"):  # 使用武陵网格
            raise RuntimeError("垂藤导航失败")  # 导航失败时不进入相机


class SerenityGardenTianshiPillarRoute(EnvironmentMonitoringRoute):
    photo_direction = "up"

    def run(self):
        if not self.task.navigate_grid_to((-1670.55, -1299.16), map_id="map02"):
            raise RuntimeError("安思园的天师桩导航失败")
