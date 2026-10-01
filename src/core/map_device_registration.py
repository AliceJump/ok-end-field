"""地图设备指纹注册共用的 HTTP 请求。"""

import json
import urllib.request

DEVICEPROFILE_URL = "https://fp-it.portal101.cn/deviceprofile/v4"
MAP_PAGE_URL = "https://game.skland.com/map/endfield"


def post_device_registration(payload: dict, timeout: float) -> dict:
    """提交注册载荷，具体签发校验交给调用方。"""
    req = urllib.request.Request(
        DEVICEPROFILE_URL,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json;charset=UTF-8",
            "User-Agent": "Mozilla/5.0 ok-ef map websocket client",
            "Origin": MAP_PAGE_URL.rsplit("/", 2)[0],
            "Referer": MAP_PAGE_URL,
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))
