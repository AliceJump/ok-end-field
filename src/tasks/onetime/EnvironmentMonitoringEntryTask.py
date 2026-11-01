import re  # 将观察点名称作为字面文本匹配

from qfluentwidgets import FluentIcon  # 复用调试任务图标

from src.data.FeatureList import FeatureList as fL  # 复用任务定位和地图模板
from src.tasks.navigation.mixin.map_mixin import MapMixin  # 复用传送及到达确认


class EnvironmentMonitoringEntryTask(MapMixin):  # 环境监测的传送前流程
    def __init__(self, *args, **kwargs):  # 构造阶段不操作游戏
        super().__init__(*args, **kwargs)  # 初始化项目任务
        self.name = "环境监测：前往观察点"  # 明确当前只完成前半流程
        self.description = "接取或继续一个环境监测委托，传送到观察点后停止，暂不移动或拍照。"  # 保留后半流程供后续实现
        self.group_name = "工具与调试"  # 暂不接入一键日常
        self.group_icon = FluentIcon.DEVELOPER_TOOLS  # 复用工具分组
        self.icon = FluentIcon.SEARCH  # 使用查找图标
        self.default_config = {"观察点名称": ""}  # 可选精确名称，避免误接其他委托
        self.config_description = {
            "观察点名称": "留空时选择第一个可接取或可前往的委托；填写时只选择名称完全匹配的观察点。"
        }  # 说明选择规则
        self.selected_observation = None  # 每次执行重新建立交接信息

    @staticmethod  # 标题引号不是观察点身份的一部分
    def normalize_title(text):  # 兼容 OCR 的引号和空白差异
        return re.sub(r'[\s“”「」『』"«»]', "", text)  # 不模糊匹配不同观察点

    def open_monitoring(self):  # 从任意可恢复页面进入环境监测
        self.ensure_main()  # 复用主界面恢复
        if not self.to_model_area("武陵"):  # 复用地区建设区域选择
            raise RuntimeError("无法进入武陵地区建设")  # 明确入口失败
        if not self.wait_click_ocr(
            match=self.lang.environment_monitoring.entry, box=self.box.right, time_out=5
        ):  # 识别实际入口
            raise RuntimeError("未找到环境监测入口")  # 不继续点击未知页面
        if not self.wait_ocr(
            match=self.lang.environment_monitoring.page, box=self.box.top_left, time_out=5
        ):  # 验证环境监测页
            raise RuntimeError("未进入环境监测终端列表")  # 不把点击成功视为进入成功
        if not self.wait_ui_stable():  # 等待卡片入场完成
            raise RuntimeError("环境监测页面未稳定")  # 超时不扫描过渡画面

    def _terminal_selected(self, matcher):  # 验证终端标签的白色选中背景
        left = (
            0.27 if matcher.pattern == self.lang.environment_monitoring.marker_stone.pattern else 0.06
        )  # 实测两标签的空白区
        height, width = self.frame.shape[:2]  # 使用当前帧尺寸
        patch = self.frame[
            int(height * 0.145) : int(height * 0.16), int(width * left) : int(width * (left + 0.07))
        ]  # 避开文字与等级数字
        return bool(patch.size and (patch.min(axis=2) > 185).mean() > 0.7)  # 只有当前选中标签呈白底

    def select_terminal(self, matcher):  # 两个终端共用切换逻辑
        for _ in range(2):  # 有界重试被动画吞掉的点击
            if not self.wait_click_ocr(match=matcher, box=self.box.top, time_out=4):  # 根据本地化名称选择终端
                raise RuntimeError("未找到环境监测终端")  # 终端缺失不假装无任务
            if (
                self.wait_until(lambda: self._terminal_selected(matcher), time_out=3, raise_if_not_found=False)
                and self.wait_ui_stable()
            ):  # 验证实际选中并等待对应卡片稳定
                return  # 选中与稳定均确认后才扫描
        raise RuntimeError("环境监测终端切换未完成")  # 不将旧终端卡片当成新终端

    def available_observations(self):  # 从当前终端的三张卡片读取可执行委托
        observations = []  # 只记录有操作按钮的卡片
        for index in range(3):  # 当前界面实测固定三列
            left = 0.049 + index * 0.304  # 根据 1920x1080 实测截图估计卡片左边界
            right = min(left + 0.293, 0.96)  # 根据实测截图估计卡片右边界
            title_box = self.box_of_screen(left, 0.285, right, 0.335)  # 仅标题起点范围，排除下面的拍摄说明
            action_box = self.box_of_screen(left, 0.85, right, 0.925)  # 仅本卡片底部操作区
            actions = self.ocr(
                match=[self.lang.environment_monitoring.accept, self.lang.environment_monitoring.go], box=action_box
            )  # 已收录卡片无此按钮
            if not actions:  # 跳过无可执行动作的卡片
                continue  # 不接取已完成观察点
            titles = [
                item
                for item in self.ocr()
                if title_box.x <= item.x < title_box.x + title_box.width
                and title_box.y <= item.y < title_box.y + title_box.height
            ]  # 整帧识别后按卡片定位，避免紧裁剪漏掉引号后的小字
            if len(titles) != 1:  # 不猜测截断或多行标题
                continue  # 保守跳过无法确认身份的卡片
            observations.append({"name": titles[0].name, "action_box": action_box})  # 保留动态操作区域
        return observations  # 返回当前终端候选

    def enter_observation(self, observation):  # 接取或继续指定卡片
        area = observation["action_box"]  # 始终局限于目标卡片
        accept = self.ocr(match=self.lang.environment_monitoring.accept, box=area)  # 判断是否还需接取
        if accept:  # 仅未接取时点击一次
            self.click(accept[0])  # 不循环重复接取
        if not self.wait_click_ocr(
            match=self.lang.environment_monitoring.go, box=area, time_out=5, recheck_time=0.3
        ):  # 等待接取后变为前往任务
            raise RuntimeError("未找到目标委托的前往任务按钮")  # 停止而不是点其他卡片
        title = self.normalize_title(observation["name"])  # 去掉装饰性引号
        if not self.wait_ocr(
            match=re.compile(re.escape(title)), box=self.box_of_screen(0.32, 0.075, 0.97, 0.16), time_out=5
        ):  # 实测任务详情标题从屏幕约三分之一处开始
            raise RuntimeError("任务详情与所选观察点不一致")  # 防止传送至错误任务
        if not self.wait_ui_stable():  # 追踪按钮入场稳定后再操作
            raise RuntimeError("环境监测任务详情未稳定")  # 禁止点击过渡画面

    def transfer_observation(self):  # 正确处理未追踪与已追踪两条入口
        track = self.ocr(match=self.lang.environment_monitoring.track, box=self.box.bottom_right)  # 只匹配开始追踪
        if track:  # 首次追踪会自动打开任务地图
            self.click(track[0])  # 使用识别位置
        else:  # 已追踪时需要点击地图定位按钮
            if not self.ocr(
                match=self.lang.environment_monitoring.tracked, box=self.box.bottom_right
            ):  # 不把识别失败当成已追踪
                raise RuntimeError("无法确认环境监测任务追踪状态")  # 保留错误现场
            if not self.wait_click_feature(
                fL.one_task_to_map, box=self.box.bottom_right, time_out=4, raise_if_not_found=False
            ):  # 复用任务地图定位图标
                raise RuntimeError("未找到已追踪任务的地图定位按钮")  # 不绕过地图状态确认
        if not self.wait_feature(
            fL.in_map, box=self.box_of_screen(0.027, 0.531, 0.051, 0.896), time_out=10, raise_if_not_found=False
        ):  # 验证地图已打开
            raise RuntimeError("环境监测任务地图未打开")  # 禁止在任务面板直接点传送
        if not self.wait_ui_stable():  # 等待地图上的按钮稳定
            raise RuntimeError("环境监测任务地图未稳定")  # 禁止点击过渡画面
        if not self.to_near_transfer_point(need_track=False):  # 复用前往传送和传送两步
            raise RuntimeError("环境监测快捷传送失败")  # 失败不进入后半流程
        if not self._wait_for_transfer_arrival():  # 等待大世界稳定出现
            raise RuntimeError("环境监测传送后未确认到达")  # 点击成功不等于到达

    def prepare_observation(self, target_name=""):  # 可供后半流程复用的入口
        self.selected_observation = None  # 清除上轮结果
        self.open_monitoring()  # 每次从明确入口开始
        desired = self.normalize_title(target_name)  # 配置名称按同一规则比较
        terminals = [
            self.lang.environment_monitoring.outskirts,
            self.lang.environment_monitoring.marker_stone,
        ]  # 固定终端遍历顺序
        for terminal in terminals:  # 两个终端分别识别
            self.select_terminal(terminal)  # 选择终端并等待稳定
            for observation in self.available_observations():  # 只考虑可操作卡片
                if desired and self.normalize_title(observation["name"]) != desired:  # 指定名称时不退回其他任务
                    continue  # 保留其他委托
                self.enter_observation(observation)  # 接取或继续所选观察点
                self.transfer_observation()  # 快捷传送并验证到达
                self.selected_observation = {
                    "name": observation["name"],
                    "terminal": terminal.pattern,
                }  # 交给后半流程的上下文
                self.log_info(f"[environment-monitoring] arrived: {self.selected_observation}")  # 记录实际到达任务
                return self.selected_observation  # 一次只处理一个观察点
        raise RuntimeError("未找到符合条件的可接取或可前往观察点")  # 不将无匹配伪报为成功

    def run(self):  # GUI 与 MCP 共用生产流程
        return self.prepare_observation(str(self.config.get("观察点名称", "")).strip())  # 到达后停止，保留拍照任务
