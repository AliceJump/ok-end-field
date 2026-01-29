import re

from qfluentwidgets import FluentIcon

from src.tasks.BaseEfTask import BaseEfTask


class BuySellTask(BaseEfTask):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "倒货绿票"
        self.description = "满足照设置利润则倒货"
        self.icon = FluentIcon.SYNC
        self.default_config.update({
            '囤货价格折扣百分比': 20,
            '出售价格利润百分比': 300,
        })

    def run(self):
        self.buy()
        return
        self.ensure_main()
        self.send_key('y')
        self.wait_click_feature('buy_sell', settle_time=0.5)
        self.wait_feature('q', settle_time=0.5)
        self.send_key('q', after_sleep=1)

    def buy(self):
        buy_box = self.box_of_screen(0.06, 0.71, 0.97, 0.92)
        prices = self.ocr(box=buy_box, match=re.compile('%'))
        up_and_downs = self.find_feature(['price_up', 'price_down'], box=buy_box)
        highest_margin = 0
        highest_box = None
        self.log_info(f'ocr prices {prices}')
        self.log_info(f'up_and_downs {up_and_downs}')
        for price in prices:
            price_int = parse_rounded_int(price.name)
            if price_int is not None:
                closest = price.find_closest_box("all", up_and_downs)
                self.log_info(f'price_{price_int} find_closest_box {closest}')
                if closest and closest.closest_distance(price) < self.width_of_screen(0.02):
                    self.log_info(f'found closest box {price_int} {closest}')
                    if closest.name == 'price_down' and price_int > highest_margin:
                        highest_margin = price_int
                        highest_box = price
        if highest_margin > self.config.get('囤货价格折扣百分比'):
            self.log_info(f'highest margin {highest_margin} > {self.config.get("囤货价格折扣百分比")}')
            self.click(highest_box, after_sleep=1)
            self.click(0.53, 0.68, after_sleep=0.5)
            self.click(0.83, 0.79, after_sleep=1)


def parse_rounded_int(text):
    """
    Parses the first number found in a string and returns it
    as a rounded integer.
    """
    # Regex explanation:
    # \d+       : Matches one or more digits
    # (?: ... )?: Non-capturing group for the optional decimal part
    # \.        : Matches a literal dot
    # \d+       : Matches digits after the dot
    pattern = r"(\d+(?:\.\d+)?)"

    match = re.search(pattern, text)

    if match:
        # Extract the string part that is the number (e.g., "104.56")
        number_str = match.group(1)
        # Convert to float, round to nearest whole number, convert to int
        return int(round(float(number_str)))
    else:
        # Return None or raise an error if no number is found
        return None
