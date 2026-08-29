import time


def move_keys(self, keys, duration):
    if isinstance(keys, str):
        keys = list(keys)  # "wa" -> ["w", "a"]

    for key in keys:
        self.send_key_down(key)

    time.sleep(duration)

    for key in keys:
        self.send_key_up(key)
