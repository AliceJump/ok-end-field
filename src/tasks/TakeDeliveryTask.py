import re
import time
from qfluentwidgets import FluentIcon

from ok import TriggerTask, Logger
from src.tasks.BaseEfTask import BaseEfTask

logger = Logger.get_logger(__name__)


class TakeDeliveryTask(BaseEfTask, TriggerTask):
    """
    TakeDeliveryTask

    功能：自动接取高价值调度任务。
    逻辑：同时识别“报酬金额”与“调度券类型（图标）”，满足条件则接取，否则刷新。

    配置说明：
    - `target_tickets`: 目标券种，列表。可选值：`ticket_valley`, `ticket_wuling`。
    - `min_reward`: 最低报酬金额（万）。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "运送委托接取"
        self.description = "自动抢单"
        self.icon = FluentIcon.ACCEPT
        self.default_config = {
            '接取谷地券': False,
            '接取谷地券最低金额(万)': 5.0,
            '接取武陵券': True,
            '接取武陵券最低金额(万)': 5.0
        }

    def process_ocr_results(self, full_texts, filter_min, reward_pattern):
        """
        处理 OCR 结果，提取奖励、接取按钮和刷新按钮。
        方便单元测试逻辑。
        """
        rewards = []
        accept_btns = []
        refresh_btn = None

        for t in full_texts:
            name = t.name.strip()
            if ("刷新" in name or "秒后可刷新" in name) and t.y > self.height * 0.8:
                refresh_btn = t
            elif "接取运送委托" in name:
                accept_btns.append(t)
            else:
                match = reward_pattern.search(name)
                if match:
                    try:
                        val = float(match.group(1))
                        if val >= filter_min and val <= 100:
                            rewards.append((t, val))  # 保存 OCR对象 和 提取出的数值
                        elif val > 100:
                            self.log_debug(f"金额异常过大({val}万)，可能是OCR误识别，已过滤")
                    except:
                        pass
        return rewards, accept_btns, refresh_btn

    def detect_ticket_type(self, reward_obj, ticket_types, y_ceiling):
        """
        根据报酬文本的位置，推算图标区域。
        新增 y_ceiling 参数：限制搜索框的顶部边界，防止覆盖到上一行。
        """
        # 保持较大的搜索范围设置，以适应高分辨率
        search_hw_ratio = 3.6
        search_h_ratio = 2.4
        min_box_size = 110 # 保持这个较大的值以兼容 2K/4K

        search_width = max(reward_obj.height * search_hw_ratio, min_box_size)
        search_height = max(reward_obj.height * search_h_ratio, min_box_size)

        x_offset_val = (reward_obj.width / 2) - (search_width / 2)

        # 原始计算的顶部 Y 坐标
        target_y = reward_obj.y - search_height

        # 【关键修复】如果计算出的顶部超过了天花板（上一行），就强制压回
        if target_y < y_ceiling:
            # 调整高度，使顶部刚好顶着天花板，而不是伸过去
            # 此时 height 变小，可能导致崩溃，所以下面要加 try-catch
            search_height = reward_obj.y - y_ceiling
            target_y = y_ceiling

        target_real_height = search_height + reward_obj.height * 0.5
        y_offset_val = target_y - reward_obj.y # 重新计算相对位移

        icon_search_box = reward_obj.copy(
            x_offset = x_offset_val,
            y_offset = y_offset_val,
            width_offset = search_width - reward_obj.width,
            height_offset = target_real_height - reward_obj.height
        )

        # 边界检查
        if icon_search_box.y < 0:
            icon_search_box.height += icon_search_box.y
            icon_search_box.y = 0
        if icon_search_box.x < 0:
            icon_search_box.width += icon_search_box.x
            icon_search_box.x = 0

        # 【防崩溃】如果盒子被压得太扁（小于模板），find_feature 会抛出异常
        try:
            found_ticket = self.find_feature(ticket_types, box=icon_search_box)
            if found_ticket:
                return found_ticket[0] if isinstance(found_ticket, list) else found_ticket
        except Exception as e:
            # 捕获 "image size < template size" 错误，视为未找到
            self.log_debug(f"图标搜索区域过小(可能被截断)，跳过: {e}")
            return None

        return None

    def run(self):
        # 前置：按Y，点击“仓储节点”，点击“运送委托列表”
        self.log_info("前置操作：按Y，点击‘仓储节点’，点击‘运送委托列表’")
        self.send_key('y', down_time=0.05, after_sleep=0.5)
        storage_box = self.wait_ocr(match="仓储节点", time_out=5)
        if storage_box:
            self.click(storage_box[0], move_back=True, after_sleep=0.5)
        else:
            self.log_error("未找到‘仓储节点’按钮，任务中止。")
            return

        delivery_box = self.wait_ocr(match="运送委托列表", time_out=5)
        if delivery_box:
            self.click(delivery_box[0], move_back=True, after_sleep=0.5)
            # 点击后滚动到底部（多次大幅度向下滚动）
            cx = int(self.width * 0.5)
            cy = int(self.height * 0.5)
            for _ in range(6):
                self.scroll(cx, cy, -8)
                self.sleep(0.2)
        else:
            self.log_error("未找到‘运送委托列表’按钮，任务中止。")
            return

        reward_regex = r"(\d+\.?\d*)万"
        reward_pattern = re.compile(reward_regex, re.I)

        # 读取券种配置
        enable_valley = self.config.get('接取谷地券', False)
        enable_wuling = self.config.get('接取武陵券', True)
        valley_min = float(self.config.get('接取谷地券最低金额(万)', 5.0))
        wuling_min = float(self.config.get('接取武陵券最低金额(万)', 5.0))

        ticket_types = []
        if enable_valley:
            ticket_types.append('ticket_valley')
        if enable_wuling:
            ticket_types.append('ticket_wuling')

        if not ticket_types:
            self.log_info("警告: 未启用任何券种，任务退出")
            return

        active_mins = []
        if enable_valley:
            active_mins.append(valley_min)
        if enable_wuling:
            active_mins.append(wuling_min)
        filter_min = min(active_mins)

        # 滚动控制状态
        scroll_step = 0             # 当前滚动计数 (0, 1) -> 2次后刷新
        scroll_direction = -1       # -1: 向下(wheel负值), 1: 向上(wheel正值)

        while True:
            if not self.enabled:
                break

            try:

                full_texts = self.ocr(box=self.box_of_screen(0.05, 0.15, 0.95, 0.95))
                rewards, accept_btns, refresh_btn = self.process_ocr_results(full_texts, filter_min, reward_pattern)

                # 【新增步骤】按 Y 坐标排序，确保从上往下处理，才能正确计算行间距
                rewards.sort(key=lambda x: x[0].y)

                target_btn = None
                matched_msg = ""

                # 初始化第一行的天花板（比如列表顶部的 Y 坐标，这里设为 OCR 区域的顶部大概位置）
                # 这里的 0.15 * height 是我们在 box_of_screen 里设置的顶部
                current_ceiling = self.height * 0.15

                # 2. 遍历满足金额条件的所有报酬行
                for reward_obj, val in rewards:
                    # 更新当前行的天花板：
                    # 对于当前行，允许的最高位置是 "current_ceiling"
                    # 我们给一点点余量 (比如 +5 像素)，避免紧贴着上一行
                    safe_ceiling = current_ceiling + 5

                    # 寻找该行对应的接取按钮
                    r_cy = reward_obj.y + reward_obj.height / 2
                    my_btn = None
                    for btn in accept_btns:
                        if abs(r_cy - (btn.y + btn.height / 2)) < btn.height * 0.8:
                            my_btn = btn
                            break

                    # 无论是否找到按钮，处理完这一行后，更新下一行的天花板
                    # 下一行的天花板 = 当前行文字的底部 Y 坐标
                    current_ceiling = reward_obj.y + reward_obj.height

                    if not my_btn:
                        continue

                    # 传入计算好的 safe_ceiling
                    ticket_result = self.detect_ticket_type(reward_obj, ticket_types, safe_ceiling)

                    if ticket_result:
                        # 根据具体的图标类型判断对应的金额阈值
                        is_qualified = False
                        if ticket_result.name == 'ticket_valley' and enable_valley and val >= valley_min:
                            is_qualified = True
                        elif ticket_result.name == 'ticket_wuling' and enable_wuling and val >= wuling_min:
                            is_qualified = True

                        if is_qualified:
                            target_btn = my_btn
                            matched_msg = f"金额={val}万, 类型={ticket_result.name}"
                            self.log_info(f"匹配成功: {matched_msg}")
                            break
                        else:
                            self.log_debug(f"类型匹配({ticket_result.name})但金额({val}万)不达标")
                    else:
                        self.log_debug(f"金额符合({val}万)但未找到券种图标")

                # 4. 执行操作
                if target_btn:
                    # 匹配成功后，增加日志并点击
                    self.log_info(f"准备接取任务：{matched_msg}")
                    self.click(target_btn, after_sleep=2)
                    return True
                else:
                    self.log_info("未找到符合条件(金额+类型)的委托")

                    # 1. 更新刷新按钮位置记忆
                    if refresh_btn:
                        last_refresh_box = refresh_btn
                    else:
                        last_refresh_box = getattr(self, 'last_known_refresh_btn', None)

                    # 2. 检查是否需要滚动 (每轮刷新之间最多滚动1次)
                    if scroll_step < 1:
                        scroll_step += 1
                        direction_str = "向下" if scroll_direction == -1 else "向上"
                        self.log_info(f"执行第 {scroll_step}/1 次{direction_str}滚动查漏...")

                        # 滚动操作：在屏幕列表中间位置滚动
                        cx = int(self.width * 0.5)
                        cy = int(self.height * 0.5)
                        self.scroll(cx, cy, scroll_direction * 3)
                        self.sleep(1.0) # 等待滚动动画
                        continue    # 滚动后重新OCR

                    # 3. 滚动次数已满，准备刷新
                    self.log_info("已完成当前列表扫描，准备检测刷新")

                    # 4. 尝试执行盲点刷新（直接用 Box 对象）
                    if last_refresh_box:
                        last_click = getattr(self, 'last_refresh_time', 0)
                        elapsed = time.time() - last_click

                        if elapsed < 5.2:
                            # CD未好
                            self.log_debug(f"刷新CD中 ({elapsed:.1f}/5.2s)，等待...")
                            self.sleep(5.2 - elapsed)

                        # CD已好（或睡醒），执行点击
                        self.log_info(f"执行刷新 (坐标: {int(last_refresh_box.x)}, {int(last_refresh_box.y)})")
                        self.click(last_refresh_box, move_back=True)
                        self.last_refresh_time = time.time()

                        # 刷新成功后，滚动状态反转
                        scroll_direction *= -1
                        scroll_step = 0             # 重置计数

                        self.sleep(1.0) # 等待刷新内容加载
                    else:
                        self.log_info("警告: 尚未定位到刷新按钮位置，无法刷新，重试...")
                        time.sleep(1.0)
                        continue
            except Exception as e:
                self.log_info(f"TakeDeliveryTask error: {e}")
                if "SetCursorPos" in str(e) or "拒绝访问" in str(e):
                    self.log_info("警告: 检测到权限不足或光标控制失败，请尝试【以管理员身份运行】程序！")
                time.sleep(2)
                continue
