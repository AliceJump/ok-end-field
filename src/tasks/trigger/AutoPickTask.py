from ok import Logger, TriggerTask

from src.core.BaseEfTask import BaseEfTask
from src.icons import Icons
from src.tasks.trigger.auto_pick_rules import (
    BLACK_LIST,
    CFG_SKIP_PRODUCIBLE,
    WHITE_LIST,
    should_skip_pick,
)

logger = Logger.get_logger(__name__)


class AutoPickTask(BaseEfTask, TriggerTask):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "自动拾取"
        self.description = "大世界探索时自动拾取"
        self.icon = Icons.Collect

        self.default_config = {
            '_enabled': True,
            CFG_SKIP_PRODUCIBLE: True,
        }
        self.config_description = {
            CFG_SKIP_PRODUCIBLE: (
                '不拾取可由种植田或采种/种植流水线量产的作物：琼叶参、金石稻、芽针、锦草、'
                '苦叶椒、砂叶、灰芦麦、柑实、荞花、酮化灌木。'
            ),
        }
        self.last_box_name = None
        self.last_pick_time = 0
        self.white_list = set(WHITE_LIST)
        self.black_list = set(BLACK_LIST)

    def run(self):
        self.check_resolution()
        if self.in_combat_world():
            while button_f := self.find_f():
                text_zone = button_f.copy(x_offset=button_f.width * 6, width_offset=button_f.width * 12,
                                          y_offset=-button_f.height, height_offset=button_f.height * 12)
                texts = self.wait_ocr(box=text_zone)
                if not texts:
                    self.log_error('pick can not ocr texts')
                    return

                if should_skip_pick(texts[0].name, self.black_list, self.config.get(CFG_SKIP_PRODUCIBLE, True)):
                    return

                if any(text in texts[0].name for text in self.white_list):
                    if self.debug:
                        self.screenshot('pick')
                    self.log_debug('pick white_list {}'.format(texts[0].name))
                    self.pick()
                    self.sleep(0.2)
                    return
                start = self.active_time()
                icon_zone = button_f.copy(x_offset=button_f.width * 3.3, width_offset=button_f.width * 0.8,
                                          y_offset=-button_f.height * 0.2, height_offset=button_f.height * 0.85,
                                          name='choice')
                white_percent = 0
                while self.active_time() - start < 0.3:
                    white_percent = self.calculate_color_percentage(white_color, icon_zone)
                    if white_percent > 0.1:
                        break
                    self.sleep(0.01)
                text_count = len(texts)
                self.log_debug(f'pick_up text_count {text_count} / {white_percent}')
                if white_percent < 0.1:
                    if self.debug:
                        self.screenshot('pick')
                        self.screenshot('pick_wg', frame=icon_zone.crop_frame(self.frame))
                    self.log_info('pick because not gray/white icon {} {}'.format(texts, white_percent))
                    self.pick(text_count)
                    return
                self.sleep(0.2)

    def pick(self, count=1):
        for _ in range(count):
            self.press_key('f', after_sleep=0.1)


white_color = {
    'r': (230, 255),
    'g': (230, 255),
    'b': (230, 255)
}
