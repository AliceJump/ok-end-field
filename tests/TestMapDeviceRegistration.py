import json
import unittest
from unittest.mock import MagicMock, patch

from src.core import map_device_fingerprint, map_device_id
from src.core.map_device_registration import DEVICEPROFILE_URL, MAP_PAGE_URL


class TestMapDeviceRegistration(unittest.TestCase):
    def response(self, result):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(result).encode("utf-8")
        return response

    @patch("src.core.map_device_registration.urllib.request.urlopen")
    def test_replay_and_synthetic_registration_send_same_request(self, urlopen):
        payload = {"data": "example", "appId": "default"}
        urlopen.return_value = self.response({"code": 1100, "detail": {"deviceId": "x" * 32}})
        with patch.object(map_device_fingerprint, "build_registration_payload", return_value=(payload, "uid")):
            self.assertEqual(map_device_id._post_registration_payload(payload, 7), "B" + "x" * 32)
            self.assertEqual(map_device_fingerprint.mint_device_id_synthetic(7), "B" + "x" * 32)

        self.assertEqual(urlopen.call_count, 2)
        for call in urlopen.call_args_list:
            req = call.args[0]
            self.assertEqual(req.full_url, DEVICEPROFILE_URL)
            self.assertEqual(req.get_method(), "POST")
            self.assertEqual(json.loads(req.data), payload)
            self.assertEqual(req.get_header("Content-type"), "application/json;charset=UTF-8")
            self.assertEqual(req.get_header("Origin"), "https://game.skland.com")
            self.assertEqual(req.get_header("Referer"), MAP_PAGE_URL)
            self.assertEqual(call.kwargs["timeout"], 7)

    @patch("src.core.map_device_registration.urllib.request.urlopen")
    def test_callers_keep_their_response_validation_rules(self, urlopen):
        urlopen.return_value = self.response({"code": 1902, "detail": {"deviceId": "x" * 32}})
        self.assertEqual(map_device_id._post_registration_payload({}, 7), "B" + "x" * 32)
        with self.assertRaisesRegex(RuntimeError, "1902"):
            map_device_fingerprint.mint_device_id_synthetic(7)

    @patch("src.core.map_device_registration.urllib.request.urlopen")
    def test_both_callers_reject_short_device_ids(self, urlopen):
        urlopen.return_value = self.response({"code": 1100, "detail": {"deviceId": "short"}})
        for mint in (
            lambda: map_device_id._post_registration_payload({}, 7),
            map_device_fingerprint.mint_device_id_synthetic,
        ):
            with self.assertRaisesRegex(RuntimeError, "deviceId"):
                mint()


if __name__ == "__main__":
    unittest.main()
