import re
import time
import threading

from PySide6.QtCore import QTimer
from qfluentwidgets import FluentIcon

from src.data.characters_utils import get_contact_list_with_feature_list
from src.data.characters import all_list
from src.tasks.mixin.common import LiaisonResult, build_name_patterns


class DailyLiaisonFeature:
    HELP_LINK = "https://cnb.cool/ok-oldking/ok-ef-update/-/blob/main/docs/日常任务.md"

    def __init__(self, task):
        self._task = task

        self.can_contact_dict = get_contact_list_with_feature_list(self._task.lang)
        self.contact_name_patterns = {name: build_name_patterns(name) for name in self.can_contact_dict.keys()}
        #
        self._task.config_type["优先送礼对象"] = {"type": "drop_down", "options": all_list}
        self._task.config_type["帮助"] = {
            "type": "button",
            "text": "打开帮助",
            "icon": FluentIcon.LINK,
            "callback": self.open_help_link,
        }
        self._task.default_config.update({
            "⭐送礼": True,
            "⭐帝江号一键存放": False,
            "送礼任务最多尝试次数": 2,
            "优先送礼对象": all_list[0],
        })
        self._task.config_description.update({
            "⭐送礼": (
                "是否通过「帝江号/干员联络台/赠送礼物」提升员好感度。\n"
                "如果途中偶遇干员，则直接交互完成送礼。\n"
                "任务开始时候，角色不能位于「帝江号/剑桥」传送点附近。"
            ),
            "⭐帝江号一键存放": (
                "是否在「帝江号」打开背包并点击「一键存放」。\n"
                "确认不会自动存可用道具导致治疗药被存入后再开启"
            ),
            "帮助": "打开日常任务使用说明网页。",
        })
        self._task.default_config_group.update({
            "⭐送礼": ["送礼任务最多尝试次数", "优先送礼对象"],
        })

    def __getattr__(self, name):
        return getattr(self._task, name)

    def open_help_link(self, *_):
        """打开帮助链接，使用独立的内嵌 WebView 对话框。"""
        from src.gui.WebViewDialog import WebViewDialog

        def _show_dialog():
            try:
                dialog = WebViewDialog("日常任务帮助", self.HELP_LINK, None)
                dialog.show()
                self._help_dialog = dialog
                self.log_info("已打开帮助 WebView 对话框")
            except Exception as e:
                self.log_error(f"打开帮助对话框失败: {e}")
                # 如果 WebView 失败，回退到打开浏览器
                import webbrowser
                webbrowser.open(self.HELP_LINK)

        # 确保在 GUI 线程中执行
        if threading.current_thread() is threading.main_thread():
            _show_dialog()
        else:
            QTimer.singleShot(0, _show_dialog)

    def execute_gift_to_liaison(self):
        """传送至帝江号后执行联络与送礼链路。"""
        self.log_info("传送至帝江号指定点")
        if not self.transfer_to_home_point():
            self.mark_task_failure("传送失败，无法开始送礼任务")
            return False
        wait_bridge_disappear_count = 0
        while self.ocr(match=self.lang.daily_liaison_mixin.k_27d2b829, box=self.box.left):
            wait_bridge_disappear_count += 1
            if wait_bridge_disappear_count >= 120:
                self.log_info("等待 '舰桥' 文案消失次数超限，送礼任务中断")
                return False
            self.next_frame()
            self.sleep(0.5)
        self.log_info("舰桥提示已经消失，等待信赖弹窗并消失")
        start_time = time.time()
        if self.wait_ocr(match=self.lang.daily_liaison_mixin.k_933056f0, box=self.box.left, time_out=5):
            while self.ocr(match=self.lang.daily_liaison_mixin.k_933056f0, box=self.box.left):
                if time.time() - start_time > 10:
                    self.log_info("等待 '信赖' 弹窗超时，进行下一步")
                self.next_frame()
                self.sleep(0.5)
        self.log_info("前往中央环厅")
        if not self.navigate_to_main_hall():
            self.log_info("未到达中央环厅，送礼任务中断")
            return False

        self.log_info("前往干员联络站")
        max_retry = 3
        retry = 0
        result = self.navigate_to_operator_liaison_station()
        while result == LiaisonResult.FIND_CHAT_ICON:
            self.log_info(f"聊天界面处理 (第 {retry + 1}/{max_retry} 次)")

            if self.collect_and_give_gifts():
                return True

            retry += 1
            if retry >= max_retry:
                self.mark_task_failure("多次收礼失败，停止重试")
                return False

            result = self.navigate_to_operator_liaison_station()
        if result:
            self.log_info("成功到达干员联络台，开始干员联络任务")
            if self.perform_operator_liaison():
                self.log_info("干员联络完成，开始收取或赠送礼物")
                return self.collect_and_give_gifts()
            else:
                self.mark_task_failure("干员联络任务失败")
                return False

        else:
            self.mark_task_failure("前往联络站失败")
            return False

    def execute_gift_task(self):
        """送礼任务入口，支持失败重试。"""
        self.info_set("current_task", "give_gift")
        self.log_info("开始执行送礼任务")

        max_retry = self.config.get("送礼任务最多尝试次数", 1)

        for i in range(max_retry):
            self.log_info(f"送礼任务 - 第 {i + 1}/{max_retry} 次尝试")

            success = self.execute_gift_to_liaison()
            if success:
                self.log_info(f"第 {i + 1} 次送礼任务成功")
                return True

            self.log_info(f"第 {i + 1} 次送礼任务失败")

        self.mark_task_failure("送礼任务最终失败")
        return False

    def boat_one_key_store(self):
        """在帝江号执行背包一键存放。"""
        self.info_set("current_task", "boat_one_key_store")
        if not self.transfer_to_home_point(should_check_out_boat=True):
            self.mark_task_failure("传送到帝江号失败，无法执行一键存放")
            return False
        self.press_key("b", after_sleep=1)
        store_btn = self.wait_ocr(
            box=self.box_of_screen(0.64, 0.705, 0.69, 0.735, name="onekey_store_area"),
            match=self.lang.daily_liaison_mixin.k_d661f6da,
            time_out=5,
        )
        if not store_btn:
            self.log_info("未找到“存放”按钮")
            return False
        self.click(store_btn[0], after_sleep=0.5)
        return True
