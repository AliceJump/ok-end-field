"""Confirmed kitchen submissions and table orders for optional spare portions."""

from collections import Counter


class RestaurantInventory:
    # The supported counter has 8 slots. Reserve one for new demand, so spare
    # dishes cannot fill every slot and block an unrelated dish from finishing.
    SPARE_BUDGET = 7

    def __init__(self, dishes=(), *, fresh=False):
        self.dishes = tuple(dishes)
        self.portions = Counter()
        self.tables = {}
        self.certain = fresh

    def submitted(self, item, *, confirmed):
        if confirmed:
            self.portions[item] += 1
        else:
            # A completed head can hide a queue increment. Do not retry spare
            # production from an unconfirmed cache; demand markers remain usable.
            self.certain = False

    def table(self, number, items=(), *, dining=False):
        previous = self.tables.get(number)
        if dining:
            if previous:
                for item in previous:
                    if item in self.dishes:
                        self.portions[item] = max(0, self.portions[item] - 1)
                del self.tables[number]
            return
        if not items:
            return
        items = tuple(items)
        if previous and previous != items:
            # Missing a dining frame must not deduct an invented delivery.
            self.certain = False
        elif previous is None:
            self.tables[number] = items

    def spare(self):
        if self.certain and self.dishes and sum(self.portions.values()) < self.SPARE_BUDGET:
            item = min(self.dishes, key=lambda dish: self.portions[dish])
            return item if self.portions[item] < 2 else None
        return None
