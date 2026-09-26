import threading

from PySide6.QtCore import QTimer
from qfluentwidgets import FluentIcon

from src.data.characters import all_list
from src.icons import Icons
from src.tasks.mixin.common import Common, LiaisonResult
from src.tasks.mixin.liaison_mixin import LiaisonMixin


class LiaisonGiftTask(Common, LiaisonMixin):
    """送礼子任务：干员联络台赠送礼物，日常任务经 DailyFeature 接入。"""

    HELP_LINK = "https://cnb.cool/ok-oldking/ok-ef-update/-/blob/main/docs/日常任务.md"
    CFG_PRIORITY_GIFT_TARGET = "优先送礼对象"
    CFG_GIFT_MAX_RETRY = "送礼任务最多尝试次数"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "送礼"
        self.icon = Icons.Interact
        self.group_name = "日常任务"
        self.group_icon = FluentIcon.CALENDAR
        self.description = "通过「帝江号/干员联络台/赠送礼物」提升干员好感度。"
        self.support_multi_account = True
        self.config_type[self.CFG_PRIORITY_GIFT_TARGET] = {"type": "drop_down", "options": all_list}
        self.config_type["帮助"] = {
            "type": "button",
            "text": "打开帮助",
            "icon": FluentIcon.LINK,
            "callback": self.open_help_link,
        }
        self.default_config.update(
            {
                "一次送礼个数": 2,
                self.CFG_GIFT_MAX_RETRY: 2,
                self.CFG_PRIORITY_GIFT_TARGET: all_list[0],
            }
        )
        self.config_description.update(
            {
                "一次送礼个数": "每次送礼时送给联络台干员的礼物个数。",
                "帮助": "打开日常任务使用说明网页。",
            }
        )
        # can_contact_dict / contact_name_patterns 由 LiaisonMixin.__init__ 构建，无需重复

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
        start_time = self.active_time()
        if self.wait_ocr(match=self.lang.daily_liaison_mixin.k_933056f0, box=self.box.left, time_out=5):
            while self.ocr(match=self.lang.daily_liaison_mixin.k_933056f0, box=self.box.left):
                if self.active_time() - start_time > 10:
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
            self.log_info(self.tr("聊天界面处理 (第 {idx}/{total} 次)").format(idx=retry + 1, total=max_retry))

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

        max_retry = self.config.get(self.CFG_GIFT_MAX_RETRY, 1)

        for i in range(max_retry):
            self.log_info(self.tr("送礼任务 - 第 {idx}/{total} 次尝试").format(idx=i + 1, total=max_retry))

            success = self.execute_gift_to_liaison()
            if success:
                self.log_info(self.tr("第 {idx} 次送礼任务成功").format(idx=i + 1))
                return True

            self.log_info(self.tr("第 {idx} 次送礼任务失败").format(idx=i + 1))

        self.mark_task_failure("送礼任务最终失败")
        return False

    def run(self):
        self.ensure_main(time_out=420)
        return self.execute_gift_task()
