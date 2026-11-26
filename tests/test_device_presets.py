"""校验 ``assets/device_presets.json`` 的数据质量。

运行方式（无需第三方依赖）::

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any

PRESETS_PATH = Path(__file__).resolve().parents[1] / "assets" / "device_presets.json"
VALID_PLATFORMS = {"ipados", "android_tablet", "android_phone", "ios"}


class DevicePresetsTest(unittest.TestCase):
    """确保社区新增机型时不会破坏预设文件的结构。"""

    data: dict[str, Any]
    devices: list[dict[str, Any]]

    @classmethod
    def setUpClass(cls) -> None:
        cls.data = json.loads(PRESETS_PATH.read_text(encoding="utf-8"))
        cls.devices = cls.data["devices"]

    def test_schema_version_is_supported(self) -> None:
        self.assertEqual(self.data["schema_version"], 1)

    def test_device_ids_are_unique(self) -> None:
        ids = [device["id"] for device in self.devices]
        self.assertEqual(len(ids), len(set(ids)), "设备 id 必须全局唯一")

    def test_platforms_are_valid(self) -> None:
        declared = {platform["id"] for platform in self.data["platforms"]}
        self.assertEqual(declared, VALID_PLATFORMS)
        for device in self.devices:
            with self.subTest(device=device["id"]):
                self.assertIn(device["platform"], declared)

    def test_resolutions_and_refresh_rates(self) -> None:
        for device in self.devices:
            with self.subTest(device=device["id"]):
                self.assertGreater(device["width"], 0)
                self.assertGreater(device["height"], 0)
                self.assertTrue(device["refresh_options"])
                self.assertIn(
                    device["default_refresh_hz"],
                    device["refresh_options"],
                    "默认刷新率必须出现在 refresh_options 中",
                )
                self.assertTrue(
                    100 <= device["windows_scaling_percent"] <= 300,
                    "Windows 缩放比例应在 100~300 之间",
                )

    def test_custom_device_ranges(self) -> None:
        custom = self.data["custom_device"]
        width_min, width_max = custom["width_range"]
        height_min, height_max = custom["height_range"]
        self.assertLess(width_min, width_max)
        self.assertLess(height_min, height_max)
        self.assertIn(custom["default_refresh_hz"], custom["refresh_options"])


if __name__ == "__main__":
    unittest.main()
