"""非淤积点战斗奖励二次寻路测试（工具与调试分组）。

战斗结束后如果角色恰好处在场景边缘，第三人称相机被墙体碰撞挤近，视域随之变小，
``battle_end`` 特征会落到画面之外。正式流程（``BattleTask._find_end``）旋转一圈未
命中时会执行同一套「中键 + 按住 D」脱离后重试；本任务把这段动作拆成可观察的单步，
每一步前后各补一次检测，命中后继续走完后续的「对准 + 前进」：

    中键重置视野 -> 按住 D 若干秒（全程逐帧检测 battle_end）-> 对准终点 -> 前进到领取点

终点可能只在动作切换的瞬间入画，因此在每个动作的前后各补一次检测；
只有「按住方向键」这一段是逐帧连续检测，其余都是动作停稳后的单帧确认：

    中键前（1 帧）-> 中键后（停稳后 1 帧）-> 按住期间（逐帧）-> 松开后（1 帧）

主要功能：
    1. 按配置重复「中键 + 按住方向键 + 持续检测」，任一检测点命中即停并记录是哪一处保住的；
    2. 每次尝试单独记日志，未命中不打断循环，避免单次误判中断观察；
    3. 命中后复用正式流程的对中与导航，走到领取点面前确认「领取」提示出现；
    4. 走到位就结束，**不点击领取**，避免测试消耗真实奖励。

依赖：
    ``NavigationMixin`` 提供的 ``align_ocr_or_find_target_to_center`` /
    ``navigate_until_target`` 与 ``NavigationDetectionScope``。检测框、置信度、容差和
    超时是复制自 ``BattleTask._find_end`` 的常量（``ESCAPE_*`` 与两个 score box），
    调整正式参数时需要同步这里，测出来的结论才能直接迁移。
"""

from ok import TaskDisabledException
from qfluentwidgets import FluentIcon

from src.tasks.mixin.navigation_detection_scope import NavigationDetectionScope, get_navigation_detection_scope
from src.tasks.navigation.mixin.navigation_mixin import NavigationMixin


class BattleRewardRescanTestTask(NavigationMixin):
    """非淤积点战斗奖励二次寻路（工具与调试分组）。"""

    END_FEATURE_NAME = "battle_end"  # 协议空间/危境预演战斗终点的 YOLO 标签
    ATTEMPTS = 5  # 实测 5 轮足够覆盖绝大多数边缘卡死的站位
    HOLD_SECONDS = 3.0  # 每轮按住方向键的时长；短了位移不够，长了容易走出交互区
    MOVE_KEY = "d"  # 实测有效：中键复位后向右平移，能把相机从墙里拉出来重新看到终点
    MIN_SCORE = 0.7  # 与正式流程一致的 YOLO 置信度
    POLL_INTERVAL = 0.05  # 检测间隔下限，实际节奏由截图+推理耗时决定
    RESET_DELAY = 0.3  # 中键后等视角复位完成的缓冲
    ALIGN_TOLERANCE = 100  # 与正式流程一致的横向对中容差（像素，1080p 基准）
    WALK_TIME_OUT = 60  # 与正式流程一致的前进超时

    # 命中时刻标签：每一处都是防漏检点，命中时记录是哪一处保住的
    PHASE_BEFORE_MIDDLE = "中键前"
    PHASE_AFTER_MIDDLE = "中键后"
    PHASE_HOLD = "按住期间"
    PHASE_AFTER_RELEASE = "松开后"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "非淤积点战斗奖励二次寻路"
        self.group_name = "工具与调试"
        self.group_icon = FluentIcon.DEVELOPER_TOOLS
        self.icon = FluentIcon.SYNC
        self.description = "中键 + 按住 D 反复脱离卡死视角，看到终点后走到领取点面前，不点击领取"
        self.visible = True
        self.default_config = {
            "尝试次数": self.ATTEMPTS,
            "按住时长(秒)": self.HOLD_SECONDS,
            "移动方向": self.MOVE_KEY,
            "最低置信度": self.MIN_SCORE,
            "检测间隔(秒)": self.POLL_INTERVAL,
            "中键后等待(秒)": self.RESET_DELAY,
            "前进超时(秒)": self.WALK_TIME_OUT,
        }
        self.config_description = {
            "尝试次数": "「中键 + 按住方向键」重复的轮数，命中后立即结束，不再继续消耗轮次。",
            "按住时长(秒)": "每轮按住方向键的持续时间，期间逐帧检测 battle_end，命中即松开。",
            "移动方向": "每轮按住的方向键，默认 d（向右平移）；贴墙站位可试 a/w/s。",
            "最低置信度": "battle_end 的 YOLO 置信度阈值，与正式流程默认值保持一致便于对比。",
            "检测间隔(秒)": "两次检测之间的最小间隔；0 表示截图与推理结束后立刻进入下一次检测。",
            "中键后等待(秒)": "按下中键后等待视角回正的时间，再开始按住方向键，避免两者互相干扰。",
            "前进超时(秒)": "看到终点后对准并前进到领取点的时限；超时说明这一段本身也有问题。",
        }
        self.config_type = {
            "尝试次数": {"min": 1, "max": 20},
            "移动方向": {"options": ["d", "a", "w", "s"]},
            "前进超时(秒)": {"min": 10, "max": 300},
        }

    def _search_box(self):
        """沿用正式流程的检测框：只覆盖画面中间 61% 宽、上部 86% 高。

        正式代码（``BattleTask._find_end`` 非淤积点分支）刻意裁掉两侧与底部，避免
        HUD 图标误检；这里保持同一区域，否则测出来的命中位置无法与线上行为对照。
        """
        return self.box_of_screen(
            (1920 - 1550) / 1920,
            0,
            1550 / 1920,
            (1080 - 150) / 1080,
        )

    def _claim_box(self):
        """「领取」交互提示出现的窄框，与正式流程一致的 0.679–0.714 / 0.620–0.769。"""
        return self.box_of_screen(0.679, 0.620, 0.714, 0.769)

    def _read_options(self):
        """读取并校正运行时参数，非法值回退到类常量而不是抛错。"""
        attempts = max(1, int(self.config.get("尝试次数", self.ATTEMPTS)))
        hold_seconds = max(0.1, float(self.config.get("按住时长(秒)", self.HOLD_SECONDS)))
        move_key = str(self.config.get("移动方向", self.MOVE_KEY)).strip().lower() or self.MOVE_KEY
        if move_key not in ("w", "a", "s", "d"):
            self.log_info(f"[reward-rescan] 未知移动方向 {move_key!r}，回退到 {self.MOVE_KEY}")
            move_key = self.MOVE_KEY
        min_score = min(1.0, max(0.0, float(self.config.get("最低置信度", self.MIN_SCORE))))
        poll_interval = max(0.0, float(self.config.get("检测间隔(秒)", self.POLL_INTERVAL)))
        reset_delay = max(0.0, float(self.config.get("中键后等待(秒)", self.RESET_DELAY)))
        walk_time_out = max(5, int(self.config.get("前进超时(秒)", self.WALK_TIME_OUT)))
        return attempts, hold_seconds, move_key, min_score, poll_interval, reset_delay, walk_time_out

    def _detect_once(self, search_box, min_score):
        """单帧检测，命中返回首个检测框，否则返回 None。"""
        boxes = self.yolo_detect(name=self.END_FEATURE_NAME, box=search_box, conf=min_score)
        return boxes[0] if boxes else None

    def _walk_to_reward(self, search_box, walk_time_out):
        """对准终点后前进到领取点面前，返回 ``(是否到达, 耗时)``。

        两段都复用正式流程：先只对齐 X 轴把终点放到画面中央，再以「领取」提示为到达条件
        边走边对中。区别只有一个——到位后不点击，避免测试真的把奖励领掉。
        """
        started = self.active_time()
        claim_pattern = self.lang.daily_battle_mixin.k_39d12e73_1
        try:
            # 与 _find_end 一致地启用检测作用域：近期观测可阻止中键重置、也避免对中时乱转。
            with NavigationDetectionScope(self):
                observations = get_navigation_detection_scope(self)
                if observations is not None:
                    observations.track("yolo", self.END_FEATURE_NAME)
                    observations.track("ocr", claim_pattern)
                aligned = self.align_ocr_or_find_target_to_center(
                    self.END_FEATURE_NAME,
                    ocr=False,
                    use_yolo=True,
                    box=search_box,
                    only_x=True,
                    threshold=self.MIN_SCORE,
                    tolerance=self.ALIGN_TOLERANCE,
                    raise_if_fail=False,
                )
                if not aligned:
                    self.log_info("[reward-rescan] 终点未能在容差内对中，跳过前进")
                    return False, self.active_time() - started
                arrived = self.navigate_until_target(
                    target=claim_pattern,
                    nav=self.END_FEATURE_NAME,
                    nav_is_yolo=True,
                    target_is_ocr=True,
                    time_out=walk_time_out,
                    box=self._claim_box(),
                    max_run_time=1,
                )
        except TaskDisabledException:
            raise
        except Exception as error:
            self.log_info(f"[reward-rescan] 前进到领取点异常: {error}")
            return False, self.active_time() - started
        return bool(arrived), self.active_time() - started

    def _claim_prompt_visible(self):
        """到达后再确认一次「领取」提示，判断停下后交互是否仍然存在。"""
        return bool(
            self.wait_ocr(
                match=self.lang.daily_battle_mixin.k_39d12e73_1,
                box=self.box.bottom_right,
                time_out=1,
                raise_if_not_found=False,
            )
        )

    def _log_hit(self, attempt, phase, elapsed, box, key_label):
        """统一记录命中详情：哪一轮、哪个时刻、耗时和检测框位置。"""
        center = (int(box.x + box.width / 2), int(box.y + box.height / 2))
        timing = f"按住 {key_label} {elapsed:.2f}s 后" if phase == self.PHASE_HOLD else phase
        self.log_info(
            f"[reward-rescan] 第 {attempt} 轮命中（{timing}）：看到 {self.END_FEATURE_NAME}，"
            f"检测框中心 {center} 尺寸 {int(box.width)}x{int(box.height)}"
        )

    def run(self):
        attempts, hold_seconds, move_key, min_score, poll_interval, reset_delay, walk_time_out = self._read_options()
        search_box = self._search_box()
        key_label = move_key.upper()

        def check_marker():
            """单帧检测终点；供 SearchMixin.hold_and_detect 之类的无参回调使用。"""
            return self._detect_once(search_box, min_score)

        self.log_info(
            f"[reward-rescan] 开始：尝试 {attempts} 轮，每轮 中键 -> 按住 {key_label} {hold_seconds:g}s，"
            f"在「{self.PHASE_BEFORE_MIDDLE}/"
            f"{self.PHASE_AFTER_MIDDLE}/"
            f"{self.PHASE_HOLD}/{self.PHASE_AFTER_RELEASE}」四处检测 "
            f"{self.END_FEATURE_NAME}(conf>={min_score:g})"
        )

        hit_attempt = 0
        hit_phase = ""
        hit_elapsed = 0.0
        hit_box = None
        try:
            for attempt in range(1, attempts + 1):
                self.info_set("测试进度", f"{attempt}/{attempts}")
                observed = []

                # ① 中键前：沿用上一轮松开后的画面，先确认是不是本来就还在视野里
                found = check_marker()
                if found:
                    hit_attempt, hit_phase, hit_box = attempt, self.PHASE_BEFORE_MIDDLE, found
                    self._log_hit(attempt, hit_phase, 0.0, found, key_label)
                    break
                observed.append(f"{self.PHASE_BEFORE_MIDDLE}=无")

                # ② 中键后：等视角回正停稳后再检测一次，不在复位过程中边转边判
                self.click(key="middle", after_sleep=reset_delay)
                found = check_marker()
                if found:
                    hit_attempt, hit_phase, hit_box = attempt, self.PHASE_AFTER_MIDDLE, found
                    self._log_hit(attempt, hit_phase, 0.0, found, key_label)
                    break
                observed.append(f"{self.PHASE_AFTER_MIDDLE}=无")

                # ③ 按住期间：逐帧检测，命中即松开
                hold_start = self.active_time()
                found = self.hold_and_detect(move_key, check_marker, hold_seconds, poll_interval)
                elapsed = self.active_time() - hold_start
                if found:
                    hit_attempt, hit_phase, hit_elapsed, hit_box = attempt, self.PHASE_HOLD, elapsed, found
                    self._log_hit(attempt, hit_phase, elapsed, found, key_label)
                    break
                observed.append(f"{self.PHASE_HOLD}=无")

                # ④ 松开后：位移刚停、相机还在回位，再补一帧，防止终点只在松手那一下露头
                found = check_marker()
                if found:
                    hit_attempt, hit_phase, hit_box = attempt, self.PHASE_AFTER_RELEASE, found
                    self._log_hit(attempt, hit_phase, 0.0, found, key_label)
                    break
                observed.append(f"{self.PHASE_AFTER_RELEASE}=无")

                self.log_info(f"[reward-rescan] 第 {attempt} 轮全部落空：{'，'.join(observed)}")

            if not hit_box:
                self.screenshot("BattleRewardRescan_failed")
                summary = self.tr(
                    "二次寻路测试失败：{attempts} 轮均在四个时刻未检测到战斗终点，已保存现场截图。"
                ).format(attempts=attempts)
                self.log_info(summary, notify=True)
                self.mark_task_failure(summary)
                return False

            self.screenshot("BattleRewardRescan_hit")
            self.info_set("测试进度", "前进到领取点")
            arrived, walk_elapsed = self._walk_to_reward(search_box, walk_time_out)
            hit_detail = (
                f"按住 {key_label} {hit_elapsed:.2f}s 后" if hit_phase == self.PHASE_HOLD else f"「{hit_phase}」"
            )
            if not arrived:
                self.screenshot("BattleRewardRescan_walk_failed")
                summary = self.tr(
                    "二次寻路半成功：第 {attempt} 轮在{detail}看到终点，但前进 {walk:.2f}s 未到达领取点。"
                ).format(attempt=hit_attempt, detail=hit_detail, walk=walk_elapsed)
                self.log_info(summary, notify=True)
                self.mark_task_failure(summary)
                return False

            self.screenshot("BattleRewardRescan_arrived")
            prompt_visible = self._claim_prompt_visible()
            summary = self.tr(
                "二次寻路测试成功：第 {attempt} 轮在{detail}看到终点，再前进 {walk:.2f}s 到达领取点，"
                "领取提示{claim}，未点击领取。"
            ).format(
                attempt=hit_attempt,
                detail=hit_detail,
                walk=walk_elapsed,
                claim=self.tr("仍在") if prompt_visible else self.tr("已消失"),
            )
            self.log_info(summary, notify=True)
            self.info_set("测试结果", f"第 {hit_attempt} 轮 · {hit_phase}")
            return True
        finally:
            for key in ("w", "a", "s", "d"):
                self.send_key_up(key)
            self.release_yolo_detector()
