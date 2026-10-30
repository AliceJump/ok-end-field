from src.data.FeatureList import FeatureList as fL  # 复用现有按钮模板
from src.icons import Icons  # 使用演武图标
from src.tasks.mixin.battle_mixin import BattleMixin  # 复用自动战斗
from src.tasks.mixin.common import Common  # 复用主界面恢复
from src.tasks.mixin.search_mixin import SearchMixin  # 复用影拓的小步搜索


def arena_cards(frame):  # 通过五张牌的白色牌头识别已翻开的牌数
    height, width = frame.shape[:2]  # 按当前分辨率缩放
    count = 0  # 累计连续翻开的牌
    for index in range(5):  # 手牌最多五张
        left = 0.14 + index * 0.144  # 从实测截图估计各牌头的空白区域
        patch = frame[
            int(height * 0.25) : int(height * 0.29), int(width * left) : int(width * (left + 0.025))
        ]  # 避开数字和花色
        white = (patch.min(axis=2) > 180) & (
            patch.max(axis=2).astype(int) - patch.min(axis=2) < 25
        )  # 区分白色正面和青色牌背
        digit_left = 0.089 + index * 0.144  # 实测牌头数字区域
        digit = frame[
            int(height * 0.238) : int(height * 0.276), int(width * digit_left) : int(width * (digit_left + 0.022))
        ]  # 排除说明弹窗的大片白底
        if (
            not patch.size or white.mean() < 0.7 or not digit.size or (digit.max(axis=2) < 110).mean() < 0.12
        ):  # 同时需要白牌头和深色数字
            break  # 牌按从左到右顺序翻开
        count += 1  # 记录完整翻开的牌
    return count  # 返回已确认的抽牌次数


def arena_over_ten(frame):  # 使用超过十点时出现的红色提示条
    height, width = frame.shape[:2]  # 读取当前画面尺寸
    patch = frame[
        int(height * 0.80) : int(height * 0.825), int(width * 0.68) : int(width * 0.82)
    ]  # 实测提示条区域，不依赖滚动刻度
    return bool(
        patch.size and ((patch[:, :, 2] > 200) & (patch[:, :, 1] < 120) & (patch[:, :, 0] < 130)).mean() > 0.2
    )  # 红色面积达到阈值即停止抽牌


class BaiQuArenaTask(Common, SearchMixin, BattleMixin):  # 百趣演武独立活动任务
    def __init__(self, *args, **kwargs):  # 保持任务构造无交互
        super().__init__(*args, **kwargs)  # 初始化项目任务基类
        self.name = "百趣演武"  # GUI 任务名称
        self.description = (
            "按配置次数完成百趣演武：每轮抽至战力超过10或满5张，每局自动完成两轮战斗。"  # 明确简单抽牌策略和执行范围
        )
        self.icon = Icons.SwordChallenge  # 复用演武图标
        self.default_config.update({"刷取次数": 1})  # 默认仅完成一局
        self.config_type["刷取次数"] = {"min": 1, "max": 999}  # GUI 使用有界整数输入
        self.config_description["刷取次数"] = (
            "每次最多包含两轮挑战，挑战中断也计为一次；中途启动会将当前局计为一次。"  # 说明结算计数方式
        )

    def _next_round_button(self):  # 第一轮结算按钮
        return self.find_one(
            fL.skip_dialog_confirm, box=self.box_of_screen(0.66, 0.78, 0.72, 0.86), use_gray_scale=True
        )  # 实测继续下轮的模板区域

    def _leave_button(self):  # 挑战完成或中断共用退出按钮
        return self.find_one(
            fL.give_gift, box=self.box_of_screen(0.84, 0.90, 0.86, 0.94), use_gray_scale=True
        )  # 实测离开演算的勾选图标

    def _activity_box(self):  # 保留 left 的左右范围，仅限制活动名称所在高度
        box = self.box.left.copy()  # 不修改公共区域
        height = self.frame.shape[0]  # 按当前分辨率缩放上下边界
        box.y = round(height * 0.15)  # 跳过顶部标题，保留活动列表
        box.height = round(height * 0.90) - box.y  # 覆盖排在下方的活动入口
        return box  # 页面识别和点击共用同一区域

    def _activity_page(self):  # 在左侧指定高度匹配各语言对应的演武关键词
        boxes = self.ocr(match=self.lang.baiqu_arena.title, box=self._activity_box())  # left 范围保留完整外语名称
        return [box for box in boxes if box.x < self.frame.shape[1] * 0.12]  # 仅接受从左侧菜单起始列开始的文字

    def _team_button(self):  # 编队确认图标使用独立区域，避免和结算按钮混淆
        return self.find_one(
            fL.give_gift, box=self.box_of_screen(0.944, 0.900, 0.969, 0.941), use_gray_scale=True
        )  # 复用现有勾选模板

    def _mechanics_close_button(self):  # 机制弹窗右上角的白色关闭图标
        return self.find_one(
            fL.close,
            box=self.box_of_screen(0.84, 0.30, 0.88, 0.35),
            use_gray_scale=True,
            frame_processor=lambda crop: 255 - crop,
        )  # 搜索区域反色后复用现有黑色关闭模板

    def _draw_page(self):  # 抽牌页有固定青色抽牌按钮
        height, width = self.frame.shape[:2]  # 使用当前缓存帧
        patch = self.frame[
            int(height * 0.74) : int(height * 0.78), int(width * 0.87) : int(width * 0.90)
        ]  # 实测按钮左侧空白区
        cyan = (patch[:, :, 0] > 200) & (patch[:, :, 1] > 150) & (patch[:, :, 2] < 150)  # 按钮的高亮青色
        if patch.size and cyan.mean() > 0.5:  # 抽牌可用时通过青色按钮识别
            return True  # 普通抽牌页
        if arena_cards(self.frame) != 5:  # 满手牌后抽牌按钮变灰，必须确认五张牌
            return False  # 未满手牌且无青色按钮不认定为抽牌页
        start = self.frame[
            int(height * 0.89) : int(height * 0.94), int(width * 0.81) : int(width * 0.86)
        ]  # 同时核对开始挑战按钮的白色背景
        return bool(start.size and (start.min(axis=2) > 230).mean() > 0.9)  # 避免其他白色牌面误判

    def _challenge_button(self):  # 开始挑战只接受抽牌页中的白色启用按钮
        if not self._draw_page():  # 限制颜色判断的页面上下文
            return None  # 其他页面不点击
        height, width = self.frame.shape[:2]  # 使用当前帧尺寸
        patch = self.frame[
            int(height * 0.89) : int(height * 0.94), int(width * 0.81) : int(width * 0.86)
        ]  # 实测按钮空白区，避开文字和装饰
        if patch.size and (patch.min(axis=2) > 230).mean() > 0.9:  # 灰色禁用按钮不能通过
            return self.box_of_screen(0.75, 0.88, 0.975, 0.95)  # 返回实测完整按钮区域以点击中心
        return None  # 按钮尚未启用时继续等待

    def _state(self):  # 识别可恢复的活动阶段，未知画面不进行点击
        if self._next_round_button():  # 限定区域的继续按钮直接识别首轮结算
            return "next"  # 等待进入第二轮
        if self._leave_button():  # 限定区域的退出图标识别最终结算
            return "final"  # 最终结算
        if self._draw_page():  # 已在抽牌阶段时保留已有手牌
            return "draw"  # 继续本轮抽牌
        if self._team_button():  # 编队确认与结算的区域不同
            return "team"  # 等待出战
        if self._mechanics_close_button():  # 入场机制弹窗使用关闭图标定位
            return "mechanics"  # 只关闭已识别的说明
        if self.find_one(fL.battle_space_left):  # 使用协议空间模板识别入场，不读取轮次文字
            return "space"  # 继续触碰光点或战斗
        if self._activity_page():  # 可以从活动中心启动或确认退出
            return "activity"  # 活动选择阶段
        return None  # 动画或未知页面保持等待

    def _wait_state(self, states, timeout=20):  # 有界等待目标页面
        def check():  # wait_until 自行刷新帧
            state = self._state()  # 每次从画面重新判断
            return state if state in states else None  # 仅接受调用者期待的阶段

        return self.wait_until(check, time_out=timeout, settle_time=0.4, raise_if_not_found=False)  # 短暂稳定后再交互

    def _click_transition(self, locate, states, point=None, timeout=15):  # 页面点击只在原按钮仍存在时重试
        for _ in range(3):  # 避免无效点击无限循环
            button = locate()  # 每次点击前重新确认来源页面
            if not button:  # 来源消失可能正在加载
                state = self._wait_state(states, timeout)  # 已离开原页面时保留完整加载等待
                if state:  # 等待实际目标而不重复点击
                    return state  # 加载完成
                break  # 不在未知页面盲点
            if point:  # 部分按钮图标靠边，使用已验证的按钮中心
                self.click_relative(*point)  # 坐标来自实测截图
            else:  # 可点击的模板直接使用定位结果
                self.click_box(button)  # 点击本帧检测到的按钮
            state = self._wait_state(states, min(3, timeout))  # 按钮仍存在时尽早重试，避免空等十五秒
            if state:  # 页面转移完成
                return state  # 返回实际目标状态
        else:  # 最后一次点击也可能刚刚开始加载
            state = self._wait_state(states, timeout)  # 无论按钮是否仍可见，都保留完整等待
            if state:  # 页面最终到达目标
                return state  # 返回目标状态
        raise RuntimeError("百趣演武页面切换失败")  # 保留当前进度并停止

    def _open_activity(self):  # 在活动中心选择百趣演武
        titles = self.wait_until(self._activity_page, time_out=10, raise_if_not_found=True)  # 复用菜单位置筛选
        self.click_box(titles)  # 点击过滤后的活动名称，避免说明正文中的同名文字
        self.wait_feature(
            fL.transfer_go,
            box=self.box_of_screen(0.90, 0.88, 1, 0.96),
            time_out=10,
            settle_time=0.5,
            raise_if_not_found=True,
        )  # 等待开启演算按钮稳定
        return self._click_transition(
            lambda: self.find_one(fL.transfer_go, box=self.box_of_screen(0.90, 0.88, 1, 0.96)),
            {"draw"},
            point=(0.90, 0.92),
        )  # 验证入口模板后点击按钮中心

    def _draw_cards(self):  # 延用简单阈值策略，不进行弃牌或手动加倍
        for _ in range(5):  # 本轮最多五次新抽牌
            if not self._draw_page():  # 所有颜色判断限定在抽牌页
                raise RuntimeError("未识别百趣演武抽牌页")  # 防止在其他页面误操作
            count = arena_cards(self.frame)  # 读取本轮已翻开的牌数，支持中途恢复
            if count >= 5 or arena_over_ten(self.frame):  # 超过十点或满手立即停抽
                break  # 开始本轮战斗
            self.click_relative(0.95, 0.76)  # 实测青色抽牌按钮中心
            if not self.wait_until(
                lambda count=count: self._draw_page() and arena_cards(self.frame) == count + 1,
                time_out=10,
                settle_time=0.8,
                raise_if_not_found=False,
            ):  # 等待新牌完整翻开，禁止超时后补点
                raise RuntimeError("抽牌后未确认牌面变化")  # 未证实抽牌成功则停止，避免多抽
            self.log_info(f"百趣演武已抽 {count + 1} 张")  # 记录本轮实际牌数
        self._click_transition(self._challenge_button, {"team"})  # 仅点击已启用的开始按钮并确认编队页

    def _enter_battle(self):  # 确认编队后等待入场
        self._click_transition(
            self._team_button,
            {"mechanics", "space"},
            point=(0.90, 0.92),
            timeout=60,
        )  # 加载期间不按 ESC

    def _fight(self):  # 按影拓流程靠近光点并复用自动战斗
        if self._mechanics_close_button():  # 模板确认机制弹窗关闭按钮
            self._click_transition(self._mechanics_close_button, {"space"})  # 关闭后验证回到副本
        if not self.wait_battle_space(time_out=60):  # 等待进入协议空间
            raise RuntimeError("百趣演武入场超时")  # 防止在加载阶段移动

        if not self.start_touch_challenge():  # 与影拓共用光点搜索、触碰和开战确认
            raise RuntimeError("未能找到触碰点或启动百趣演武挑战")  # 失败时保留当前场景
        if not self.auto_battle() or not self._wait_state({"next", "final"}, timeout=20):  # 战斗返回后仍需看到活动结算
            raise RuntimeError("未确认百趣演武战斗结算")  # 不凭战斗超时宣告成功

    def is_battle_settlement(self):  # 扩展当前任务的结算判断
        return (
            bool(self._next_round_button() or self._leave_button()) or super().is_battle_settlement()
        )  # 及时退出战斗，不等待通用十五秒兜底

    def _check_single_exit_condition(self):  # 内层战斗同样需要识别活动结算
        return (
            bool(self._next_round_button() or self._leave_button()) or super()._check_single_exit_condition()
        )  # 沿用父类连续两次判定，不等待队伍 HUD 消失

    def run(self):  # GUI 和 MCP 共用完整业务流程
        count = int(self.config.get("刷取次数", 1))  # 兼容尚未保存新配置的用户
        if not 1 <= count <= 999:  # 运行时同样验证配置范围
            raise ValueError("百趣演武刷取次数必须在 1 到 999 之间")  # 无效配置不启动挑战
        for index in range(count):  # 按结算计数，挑战完成或中断均计一次
            self._run_once()  # 页面切换或战斗流程异常仍停止任务
            self.log_info(f"百趣演武已完成 {index + 1}/{count} 局")  # 仅在确认返回活动中心后记录进度
        return True  # 所有配置局数均已完成

    def _run_once(self):  # 完成或恢复一局，结束时回到活动中心
        state = self._wait_state(
            {"activity", "draw", "team", "mechanics", "space", "next", "final"}, timeout=3
        )  # 优先恢复本局已有阶段
        if not state:  # 未进入活动时从大世界打开入口
            if not self.is_main():  # 未知界面不随意按 ESC，以免撤离副本
                raise RuntimeError("请从大世界或百趣演武活动界面启动")  # 明确可恢复的启动范围
            self.ensure_main()  # 已确认大世界后调用标准恢复
            self.press_key("f7")  # 打开活动中心
            state = self._wait_state({"activity"})  # 验证入口已打开
        for _ in range(12):  # 两轮最多十二个页面阶段，异常循环有界
            self.log_info(f"百趣演武阶段: {state}")  # 留下可复核的状态轨迹
            if state == "activity":  # 新一局入口
                self._open_activity()  # 选择活动并进入抽牌
            elif state == "draw":  # 当前轮抽牌
                self._draw_cards()  # 按超过十点策略选牌
            elif state == "team":  # 编队确认
                self._enter_battle()  # 等待副本加载
            elif state in {"mechanics", "space"}:  # 入场或恢复战斗
                self._fight()  # 触碰光点并战斗
            elif state == "next":  # 第一轮结算
                self._click_transition(self._next_round_button, {"draw"})  # 使用指定模板继续第二轮
            elif state == "final":  # 挑战完成或中断均按一次结算处理
                self._click_transition(self._leave_button, {"activity"}, timeout=60)  # 退出后留足活动页加载时间
                self.log_info("百趣演武本局结束")  # 不再区分战斗胜负
                return True  # 退出结算后计入刷取次数
            else:  # 未识别状态不能跳过
                raise RuntimeError("未识别百趣演武当前阶段")  # 停止并保留现场
            state = self._wait_state({"draw", "team", "mechanics", "space", "next", "final"})  # 每步重新观察页面
        raise RuntimeError("百趣演武页面切换次数超限")  # 防止重复页面导致无限运行
