"""打包配置的静态校验：清单、版本信息、资源与 spec 引用。"""

from __future__ import annotations

import struct
import unittest
from pathlib import Path

from core import __version__
from core.common import resource_path

ROOT = Path(__file__).resolve().parents[1]
PACKAGING = ROOT / "packaging"


class PackagingTest(unittest.TestCase):
    def test_manifest_uses_as_invoker(self) -> None:
        text = (PACKAGING / "app.manifest").read_text(encoding="utf-8")
        self.assertIn('level="asInvoker"', text)
        self.assertIn("<requestedExecutionLevel", text)
        self.assertNotIn('level="requireAdministrator"', text)

    def test_manifest_version_matches_core_version(self) -> None:
        text = (PACKAGING / "app.manifest").read_text(encoding="utf-8")
        numbers = [int(part) for part in __version__.split(".")]
        numbers += [0] * (4 - len(numbers))
        expected = ".".join(str(number) for number in numbers)
        self.assertIn(f'version="{expected}"', text)

    def test_version_info_matches_core_version(self) -> None:
        text = (PACKAGING / "version_info.txt").read_text(encoding="utf-8")
        self.assertIn(f"'ProductVersion', '{__version__}'", text)
        self.assertIn(f"'FileVersion', '{__version__}'", text)
        numbers = [int(part) for part in __version__.split(".")]
        numbers += [0] * (4 - len(numbers))
        pattern = r"filevers=\(\s*" + r",\s*".join(str(n) for n in numbers) + r"\s*\)"
        self.assertRegex(text, pattern)

    def test_spec_bundles_runtime_assets(self) -> None:
        text = (PACKAGING / "campusstream_helper.spec").read_text(encoding="utf-8")
        for needle in (
            "device_presets.json",
            "icon.ico",
            "check.svg",
            "app.manifest",
            "version_info.txt",
            "console=False",
        ):
            self.assertIn(needle, text)

    def test_icon_is_valid_ico(self) -> None:
        path = ROOT / "assets" / "icon.ico"
        self.assertTrue(path.is_file())
        data = path.read_bytes()
        reserved, kind, count = struct.unpack("<HHH", data[:6])
        self.assertEqual((reserved, kind), (0, 1))
        self.assertGreaterEqual(count, 1)
        sizes = set()
        for index in range(count):
            entry = data[6 + index * 16 : 6 + (index + 1) * 16]
            width = entry[0] or 256
            sizes.add(width)
        self.assertIn(256, sizes)

    def test_resource_path_points_to_presets(self) -> None:
        path = resource_path("assets/device_presets.json")
        self.assertTrue(path.is_file(), path)


if __name__ == "__main__":
    unittest.main()
