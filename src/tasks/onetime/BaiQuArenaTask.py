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
            "每次包含两轮挑战，挑战失败时停止；中途启动会将当前局计为一次。"  # 说明计数与失败行为
        )

    def _text(self, matcher, region):  # OCR 仅用于页面语义核对
        return self.ocr(match=matcher, box=self.box_of_screen(*region))  # 限制识别范围

    def _next_round_button(self):  # 第一轮结算按钮
        return self.find_one(
            fL.skip_dialog_confirm, box=self.box_of_screen(0.66, 0.78, 0.72, 0.86), use_gray_scale=True
        )  # 实测继续下轮的模板区域

    def _leave_button(self):  # 第二轮结算退出按钮
        return self.find_one(
            fL.give_gift, box=self.box_of_screen(0.84, 0.90, 0.86, 0.94), use_gray_scale=True
        )  # 实测离开演算的勾选图标

    def _activity_page(self):  # 判断已返回活动中心
        return self._text(self.lang.baiqu_arena.activity_center, (0, 0, 0.2, 0.10))  # 活动中心标题不会出现在副本中

    def _draw_page(self):  # 抽牌页有固定青色抽牌按钮
        height, width = self.frame.shape[:2]  # 使用当前缓存帧
        patch = self.frame[
            int(height * 0.74) : int(height * 0.78), int(width * 0.87) : int(width * 0.90)
        ]  # 实测按钮左侧空白区
        cyan = (patch[:, :, 0] > 200) & (patch[:, :, 1] > 150) & (patch[:, :, 2] < 150)  # 按钮的高亮青色
        return bool(
            patch.size and cyan.mean() > 0.5 and self._text(self.lang.baiqu_arena.draw, (0.9, 0.70, 1, 0.81))
        )  # 颜色命中后核对抽牌文字

    def _state(self):  # 识别可恢复的活动阶段，未知画面不进行点击
        if self._next_round_button() and self._text(
            self.lang.baiqu_arena.next_round, (0.60, 0.78, 0.69, 0.85)
        ):  # 模板优先识别首轮结算
            return "next"  # 等待进入第二轮
        if self._leave_button() and self._text(
            self.lang.baiqu_arena.leave, (0.86, 0.88, 0.97, 0.96)
        ):  # 核对最终退出文本
            return "final"  # 最终结算
        if self._draw_page():  # 已在抽牌阶段时保留已有手牌
            return "draw"  # 继续本轮抽牌
        if self.find_one(fL.give_gift, box=self.box_of_screen(0.944, 0.900, 0.969, 0.941)) and self._text(
            self.lang.baiqu_arena.start, (0.80, 0.86, 1, 0.97)
        ):  # 编队确认与结算的区域不同
            return "team"  # 等待出战
        if self._text(self.lang.baiqu_arena.mechanics, (0.1, 0.15, 0.9, 0.5)):  # 入场机制弹窗
            return "mechanics"  # 只关闭已识别的说明
        if self.find_one(fL.battle_space_left) and self._text(
            self.lang.baiqu_arena.current_round, (0.04, 0.23, 0.25, 0.30)
        ):  # 协议空间标识配合活动轮次，避免恢复到其他副本
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
        raise RuntimeError("百趣演武页面切换失败")  # 保留当前进度并停止

    def _open_activity(self):  # 在活动中心选择百趣演武
        self.wait_click_ocr(
            match=self.lang.baiqu_arena.title, box=self.box.left, time_out=10, raise_if_not_found=True
        )  # 必须选中本活动，不能沿用其他活动的入口
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
        self.wait_click_ocr(
            match=self.lang.baiqu_arena.challenge, box=self.box.bottom_right, time_out=5, raise_if_not_found=True
        )  # 开始挑战按钮无对应图标，使用 OCR
        if not self._wait_state({"team"}):  # 等待编队页面
            raise RuntimeError("未进入百趣演武编队页")  # 不盲点确认

    def _enter_battle(self):  # 确认编队后等待入场
        self._click_transition(
            lambda: self.find_one(fL.give_gift, box=self.box_of_screen(0.944, 0.900, 0.969, 0.941)),
            {"mechanics", "space"},
            point=(0.90, 0.92),
            timeout=60,
        )  # 加载期间不按 ESC

    def _fight(self):  # 按影拓流程靠近光点并复用自动战斗
        if self._text(self.lang.baiqu_arena.mechanics, (0.1, 0.15, 0.9, 0.5)):  # 只处理已确认的机制弹窗
            self.click_relative(0.86, 0.326)  # 关闭按钮为实测截图估计位置
        if not self.wait_battle_space(time_out=60):  # 等待进入协议空间
            raise RuntimeError("百趣演武入场超时")  # 防止在加载阶段移动

        def challenge_state():  # 每一步移动后判断触碰提示或已开战
            self.next_frame()  # 沿用影拓的刷新方式
            if self.in_combat():  # 支持中途继续已开始的战斗
                return "combat"  # 无需再次触碰
            return self.ocr(
                match=self.lang.daily_battle_mixin.k_4cc61900, box=self.box.bottom_right
            )  # 复用触碰文字匹配

        state = self.strafe_search(
            challenge_state, keys=("w",), duration=0.25, passes=None, time_out=60
        )  # 小步向前搜索光点
        if not state:  # 未找到交互时停止
            raise RuntimeError("未找到百趣演武触碰点")  # 保留场景便于诊断
        if state != "combat":  # 交互提示出现后才触碰
            self.press_key("f")  # 启动挑战
            if not self.wait_until(self.in_combat, time_out=10, raise_if_not_found=False):  # 验证战斗开始
                raise RuntimeError("触碰后百趣演武挑战未启动")  # 不将空场景当作胜利
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
        for index in range(count):  # 按整局计数，每局包含两轮战斗
            self._run_once()  # 失败直接向上传递，禁止开始下一局
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
            elif state == "final":  # 第二轮结算
                completed = bool(self._text(self.lang.baiqu_arena.completed, (0.03, 0.15, 0.35, 0.30)))  # 核对完成标题
                if not completed and not self._text(
                    self.lang.baiqu_arena.interrupted, (0.03, 0.15, 0.35, 0.30)
                ):  # 未识别成功或失败标题则保持现场
                    raise RuntimeError("百趣演武结算结果未识别")  # 不从未知结果推断成功
                self._click_transition(self._leave_button, {"activity"})  # 成功或失败均验证返回活动中心
                if not completed:  # 战斗失败也及时离开结算
                    raise RuntimeError("百趣演武挑战中断，已返回活动中心")  # 报告失败，不自动重开
                self.log_info("百趣演武本局完成")  # 仅在退出得到验证后报告完成
                return True  # 一次任务完成一局
            else:  # 未识别状态不能跳过
                raise RuntimeError("未识别百趣演武当前阶段")  # 停止并保留现场
            state = self._wait_state({"draw", "team", "mechanics", "space", "next", "final"})  # 每步重新观察页面
        raise RuntimeError("百趣演武页面切换次数超限")  # 防止重复页面导致无限运行
