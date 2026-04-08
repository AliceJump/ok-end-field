import re
import time

from src.data.characters_utils import get_contact_list_with_feature_list
from src.tasks.mixin.common import LiaisonResult, build_name_patterns
from src.tasks.mixin.liaison_mixin import LiaisonMixin


class DailyLiaisonMixin(LiaisonMixin):
    _BOAT_STORE_WHITELIST_SUPPORTED_ITEMS = (
        "材料",
        "礼物",
        "食品",
        "任务物品",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.can_contact_dict = get_contact_list_with_feature_list()
        self.contact_name_patterns = {name: build_name_patterns(name) for name in self.can_contact_dict.keys()}
        #
        self.config_type["优先送礼对象"] = {"type": "drop_down", "options": list(self.can_contact_dict.keys())}
        self.default_config.update({
            "帮助": "https://cnb.cool/ok-oldking/ok-ef-update/-/blob/main/docs/日常任务.md",
            "⭐送礼": True,
            "⭐帝江号一键存放": True,
            "启用一键存放白名单检查": False,
            "一键存放白名单": "",
            "一键存放启用_材料": True,
            "一键存放启用_礼物": True,
            "一键存放启用_食品": True,
            "一键存放启用_任务物品": False,
            "送礼任务最多尝试次数": 2,
            "优先送礼对象": list(self.can_contact_dict.keys())[0],
        })
        self.config_description.update({
            "⭐送礼": (
                "是否通过「帝江号/干员联络台/赠送礼物」提升员好感度。\n"
                "如果途中偶遇干员，则直接交互完成送礼。\n"
                "任务开始时候，角色不能位于「帝江号/剑桥」传送点附近。"
            ),
            "⭐帝江号一键存放": (
                "是否在「帝江号」打开背包并点击「一键存放」。\n"
                "OCR 仅匹配「存放」，以避免「一」字识别失败。"
            ),
            "启用一键存放白名单检查": (
                "开启后，执行「一键存放」前会检查白名单条目是否在设置里启用。\n"
                "若白名单可用项为空，则本轮直接跳过一键存放。"
            ),
            "一键存放白名单": (
                "使用英文逗号分隔白名单条目，例如：材料,礼物。\n"
                "支持条目：材料,礼物,食品,任务物品。"
            ),
            "一键存放启用_材料": "是否启用白名单条目「材料」。",
            "一键存放启用_礼物": "是否启用白名单条目「礼物」。",
            "一键存放启用_食品": "是否启用白名单条目「食品」。",
            "一键存放启用_任务物品": "是否启用白名单条目「任务物品」。",
        })

    def execute_gift_to_liaison(self):
        """传送至帝江号后执行联络与送礼链路。"""
        self.log_info("传送至帝江号指定点")
        if not self.transfer_to_home_point():
            self.log_info("传送失败，无法开始送礼任务")
            return False
        wait_bridge_disappear_count = 0
        while self.ocr(match="舰桥", box=self.box.left):
            wait_bridge_disappear_count += 1
            if wait_bridge_disappear_count >= 120:
                self.log_info("等待 '舰桥' 文案消失次数超限，送礼任务中断")
                return False
            self.next_frame()
            self.sleep(0.5)
        self.log_info("舰桥提示已经消失，等待信赖弹窗并消失")
        start_time = time.time()
        if self.wait_ocr(match=re.compile("信赖"), box=self.box.left, time_out=5):
            while self.ocr(match=re.compile("信赖"), box=self.box.left):
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
            self.log_info(f"聊天界面处理 (第 {retry+1}/{max_retry} 次)")

            if self.collect_and_give_gifts():
                return True

            retry += 1
            if retry >= max_retry:
                self.log_info("多次收礼失败，停止重试")
                return False

            result = self.navigate_to_operator_liaison_station()
        if result:
            self.log_info("成功到达干员联络台，开始干员联络任务")
            if self.perform_operator_liaison():
                self.log_info("干员联络完成，开始收取或赠送礼物")
                return self.collect_and_give_gifts()
            else:
                self.log_info("干员联络任务失败")
                return False

        else:
            self.log_info("前往联络站失败")
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

        self.log_info("送礼任务最终失败")
        return False

    def boat_one_key_store(self):
        """在帝江号执行背包一键存放。"""
        self.info_set("current_task", "boat_one_key_store")
        if self.config.get("启用一键存放白名单检查", False):
            enabled_items = self._resolve_boat_store_whitelist_enabled_items()
            if not enabled_items:
                self.log_info("一键存放白名单检查开启且无可用项，本轮跳过")
                return True
        if not self.transfer_to_home_point(should_check_out_boat=True):
            self.log_info("传送到帝江号失败，无法执行一键存放")
            return False
        self.press_key("b", after_sleep=1)
        store_btn = self.wait_ocr(
            box=self.box_of_screen(0.64, 0.705, 0.69, 0.735, name="onekey_store_area"),
            match=re.compile(r"存放"),
            time_out=5,
        )
        if not store_btn:
            self.log_info("未找到“存放”按钮")
            return False
        self.click(store_btn[0], move_back=True, after_sleep=0.5)
        return True

    def _resolve_boat_store_whitelist_enabled_items(self):
        raw_text = str(self.config.get("一键存放白名单", "")).strip()
        raw_items = [s.strip() for s in raw_text.split(",") if s.strip()]
        if not raw_items:
            self.log_info("一键存放白名单为空，视为无可用项")
            return []
        seen = set()
        items = []
        for item in raw_items:
            if item in seen:
                continue
            seen.add(item)
            items.append(item)
        enabled_items = []
        unsupported_items = []
        disabled_items = []
        for item in items:
            if item not in self._BOAT_STORE_WHITELIST_SUPPORTED_ITEMS:
                unsupported_items.append(item)
                continue
            config_key = f"一键存放启用_{item}"
            if self.config.get(config_key, False):
                enabled_items.append(item)
            else:
                disabled_items.append(item)
        self.log_info(f"一键存放白名单原始输入: {raw_text}")
        if unsupported_items:
            self.log_info(f"一键存放白名单无效项(已过滤): {','.join(unsupported_items)}")
        if disabled_items:
            self.log_info(f"一键存放白名单未启用项(已过滤): {','.join(disabled_items)}")
        self.log_info(
            f"一键存放白名单启用项: {','.join(enabled_items) if enabled_items else '无'}"
        )
        # TODO: 后续可升级为可视化多选配置，减少纯文本白名单维护成本。
        return enabled_items
