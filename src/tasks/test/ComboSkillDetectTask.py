import math  # 校验扫描间隔
import time  # 分别测量识别耗时和刷新间隔

from ok import Box  # 复用框架调试框
from qfluentwidgets import FluentIcon  # 使用工具任务图标

from src.tasks.mixin.battle_mixin import BattleMixin  # 复用正式连携观测入口


class ComboSkillDetectTask(BattleMixin):  # 只观测连携，不调用战斗调度或输入
    def __init__(self, *args, **kwargs):  # 注册独立测试入口
        super().__init__(*args, **kwargs)  # 初始化框架任务
        self.name = "连携技检测"  # 在工具分组中显示
        self.group_name = "工具与调试"  # 沿用现有调试分组
        self.description = "仅观察连携提示与四个固定头像槽，显示角色、队伍位置、匹配分数和检测耗时；不释放技能。"  # 说明只读范围
        self.icon = FluentIcon.SEARCH  # 使用识别任务图标
        self.visible = self.debug  # 与编队判定测试保持一致
        self.default_config.update({"扫描间隔(秒)": 0.1})  # 只暴露扫描频率
        self.config_description.update({"扫描间隔(秒)": "每轮检测后的等待秒数，点击停止结束检测。"})  # 说明停止方式

    def run(self):  # 用户手动观察游戏并操作连携
        raw = self.config.get("扫描间隔(秒)", 0.1)  # 使用账号合并后的配置
        try:  # 容忍空值或无效的旧输入
            interval = float(raw)  # 转换为秒
        except (TypeError, ValueError):  # 无效配置使用默认频率
            interval = 0.1  # 与界面默认一致
        interval = min(5.0, max(0.1, interval)) if math.isfinite(interval) else 0.1  # 避免无界轮询或非法等待
        detector = getattr(self, "_combo_skill_detector", None)  # 获取可能存在的上次实例
        if detector is not None:  # 每次手动启动视为新的观察会话
            detector.reset(clear_order=True)  # 不沿用上场战斗的顺序提示
        team, size, last_team_scan = (), None, float("-inf")  # 编队只按周期或尺寸变化刷新
        last_signature, last_log = None, float("-inf")  # 状态变化立即记录，稳定时限频
        self.log_info("连携检测启动：仅观察，不按键；请手动触发连携并查看任务信息。")  # 不开启自动战斗
        try:  # 停止或异常后清理叠加框和会话状态
            while True:  # 持续观测直至用户点击停止
                frame = self.frame  # 每轮使用一个缓存帧，sleep负责下一轮刷新
                if frame is None:  # 无画面时不能保留旧识别结果
                    self.clear_box()  # 清除旧位置标注
                    self.info_set(self.tr("连携提示"), self.tr("无画面"))  # 明确是未知，不是假定不存在
                    self.info_set(self.tr("当前编队"), self.tr("未知"))  # 清除旧候选显示
                    self.info_set(self.tr("检测耗时"), "-")  # 不显示上一次耗时
                    for position in range(1, 5):  # 清除四个旧槽位
                        self.info_set(self.tr("连携槽位 {position}").format(position=position), self.tr("未知"))  # 保留原位置
                    self.link_skill_observation = None  # 不保留旧队列观察
                    detector = getattr(self, "_combo_skill_detector", None)  # 兼容尚无头像检测的首轮
                    if detector is not None:  # 画面恢复后重新学习
                        detector.reset(clear_order=True)  # 不把消失前队列当当前事实
                    team, size, last_team_scan = (), None, float("-inf")  # 恢复画面时重新识别编队
                else:  # 仅在有效帧上运行正式观测
                    now, current_size = time.monotonic(), frame.shape[:2]  # 记录当前布局
                    team_ms = 0.0  # 区分本轮是否进行了编队识别
                    if current_size != size or now - last_team_scan >= 2.0:  # 复用编队识别及管理员性别线索
                        before_team = time.perf_counter()  # 单独测量编队开销
                        team = tuple(self.detect_team(frame))  # 同一帧识别四名候选，不搜索全角色
                        team_ms = (time.perf_counter() - before_team) * 1000  # 不混入头像匹配耗时
                        size, last_team_scan = current_size, now  # 与性别线索有效期保持一致
                    before_ready = time.perf_counter()  # 提示框检测起点
                    ready = self.is_link_skill_ready()  # 复用默认小框，不扩大扫描区域
                    before_heads = time.perf_counter()  # 单独测量头像识别
                    observation = self.observe_link_skills(team=team, ready=ready, frame=frame)  # 四槽独立，未知不截断后项
                    after_heads = time.perf_counter()  # 不把截图或绘制耗时当成头像匹配
                    timing = self.tr("提示 {ready:.2f}ms / 头像 {heads:.2f}ms / 编队 {team:.2f}ms").format(  # 固定翻译模板
                        ready=(before_heads - before_ready) * 1000, heads=(after_heads - before_heads) * 1000, team=team_ms,  # 展示分项时间
                    )  # 结束耗时格式化
                    self.info_set(self.tr("当前编队"), " | ".join(team))  # 显示候选来源
                    self.info_set(self.tr("连携提示"), self.tr("存在") if ready else self.tr("未检测到"))  # 只报告当前提示
                    self.info_set(self.tr("检测耗时"), timing)  # 缓存命中也显示本轮真实耗时
                    known, unknown, queue = [], [], []  # 保留四个原始位置
                    slots = {slot.position: slot for slot in observation.slots}  # 不压缩未知槽位
                    for position in range(1, 5):  # 包括用户批准外推的第四槽
                        slot = slots.get(position)  # 提示未出现时不借用旧头像
                        text = self.tr("未知")  # 未识别不等于没有角色
                        if slot is not None and ready:  # 提示消失时清除旧头像和框
                            name = slot.character or self.tr("未知")  # 不把后项提升为队首
                            score = "-" if slot.score is None else f"{slot.score:.3f}"  # 保留空分数
                            margin = "-" if slot.margin is None else f"{slot.margin:.3f}"  # 展示歧义余量
                            text = f"{name} / team={slot.team_slot or '-'} / score={score} / margin={margin} / {slot.match_scope}"  # 用技术字段辅助实测
                            x, y, x2, y2 = slot.box  # 使用正式识别器输出的小框
                            box = Box(x, y, x2 - x, y2 - y, name=f"{position}:{name}")  # 不扩大HUD区域
                            (known if slot.character else unknown).append(box)  # 绿框已识别，红框未知
                        self.info_set(self.tr("连携槽位 {position}").format(position=position), text)  # 每轮覆盖旧槽位
                        queue.append(f"{position}:{slot.character if slot and slot.character else '?'}")  # 日志保留未知位置
                    self.draw_boxes("combo_known", known, color="green", debug=True)  # 绘制已识别头像
                    self.draw_boxes("combo_unknown", unknown, color="red", debug=True)  # 绘制未识别固定槽
                    signature = (team, ready, tuple(queue), observation.status)  # 分数轻微波动不刷屏
                    if signature != last_signature or now - last_log >= 1.0:  # 变化立即输出，稳定时每秒一次
                        self.log_info(self.tr("连携检测：{queue}；{timing}").format(queue=" | ".join(queue), timing=timing))  # 固定模板防翻译池污染
                        last_signature, last_log = signature, now  # 更新日志节流状态
                self.sleep(interval)  # 唯一刷新边界，同时响应框架停止和暂停
        finally:  # 用户停止后不留下旧队列或顺序
            self.clear_box()  # 清除实时检测框
            self.link_skill_observation = None  # 清除本任务最后一帧观察
            detector = getattr(self, "_combo_skill_detector", None)  # 兼容首轮尚未创建识别器
            if detector is not None:  # 新运行不能继承旧顺序
                detector.reset(clear_order=True)  # 不保留跨会话记忆
