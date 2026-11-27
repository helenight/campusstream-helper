"""VDD 预设加载与 XML 注入 / 回滚测试（全部在临时目录中完成）。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from core.common import CommandRunner
from core.vdd import (
    DevicePreset,
    MonitorSpec,
    VddStatus,
    apply_resolutions,
    detect_vdd,
    load_device_presets,
    reset_managed_resolutions,
)
from tests.fakes import FakeRunner

BASE_XML = """<?xml version="1.0" encoding="utf-8"?>
<vdd_settings>
  <!-- keep this comment -->
  <monitors>
    <count>1</count>
  </monitors>
  <gpu>
    <friendlyname>Test GPU</friendlyname>
  </gpu>
  <resolutions>
    <resolution>
      <width>1920</width>
      <height>1080</height>
      <refresh_rate>60</refresh_rate>
    </resolution>
  </resolutions>
  <options>
    <debug_mode>false</debug_mode>
  </options>
</vdd_settings>
"""

LEGACY_XML = """<?xml version="1.0" encoding="utf-8"?>
<vdd_settings>
  <monitors>
    <monitor>
      <width>1920</width>
      <height>1080</height>
      <refresh_rate>60</refresh_rate>
      <dpi>100</dpi>
    </monitor>
  </monitors>
</vdd_settings>
"""

NO_MONITORS_XML = """<?xml version="1.0" encoding="utf-8"?>
<vdd_settings>
  <resolutions>
    <resolution>
      <width>1920</width>
      <height>1080</height>
      <refresh_rate>60</refresh_rate>
    </resolution>
  </resolutions>
</vdd_settings>
"""

NO_RESOLUTIONS_XML = """<?xml version="1.0" encoding="utf-8"?>
<vdd_settings>
  <monitors>
    <count>1</count>
  </monitors>
  <gpu>
    <friendlyname>Test GPU</friendlyname>
  </gpu>
</vdd_settings>
"""


class DevicePresetTest(unittest.TestCase):
    def test_load_all_presets(self) -> None:
        presets = load_device_presets()
        self.assertEqual(len(presets), 10)
        self.assertEqual(len({preset.id for preset in presets}), 10)

    def test_preset_to_monitor(self) -> None:
        preset = DevicePreset(
            id="demo",
            platform="ipados",
            label="Demo",
            width=2360,
            height=1640,
            refresh_options=(60, 120),
            default_refresh_hz=120,
            windows_scaling_percent=150,
            aspect_ratio="4:3",
        )
        monitor = preset.to_monitor()
        self.assertEqual(monitor.mode, "2360x1640@120")
        with self.assertRaises(ValueError):
            preset.to_monitor(90)


class VddXmlTest(unittest.TestCase):
    def _write(self, directory: Path, content: str) -> Path:
        path = directory / "vdd_settings.xml"
        path.write_text(content, encoding="utf-8")
        return path

    def test_apply_and_rollback_resolutions(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            settings = self._write(root, BASE_XML)
            state_dir = root / "state"
            runner = CommandRunner()
            monitors = [
                MonitorSpec(2360, 1640, 60, "iPad Air 11"),
                MonitorSpec(2400, 1080, 120, "Android Phone"),
            ]

            result = apply_resolutions(
                runner,
                settings,
                monitors,
                monitor_count=2,
                state_dir=state_dir,
            )
            self.assertTrue(result.ok)
            self.assertFalse(result.simulated)
            self.assertIsNotNone(result.backup_path)
            self.assertTrue(result.backup_path.exists())

            text = settings.read_text(encoding="utf-8")
            self.assertIn("keep this comment", text)
            self.assertIn("Test GPU", text)
            self.assertIn("debug_mode", text)
            self.assertIn("2360", text)
            self.assertIn("2400", text)
            self.assertIn("<count>2</count>", text)

            again = apply_resolutions(
                runner,
                settings,
                monitors,
                monitor_count=2,
                state_dir=state_dir,
            )
            self.assertTrue(again.ok)
            self.assertIn("无需修改", again.message)

            rollback = reset_managed_resolutions(runner, settings, state_dir=state_dir)
            self.assertTrue(rollback.ok)
            final = settings.read_text(encoding="utf-8")
            self.assertIn("<count>1</count>", final)
            self.assertIn("1920", final)
            self.assertNotIn("2360", final)
            self.assertNotIn("2400", final)
            self.assertFalse((state_dir / "vdd_managed.json").exists())

    def test_dry_run_does_not_write(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            settings = self._write(root, BASE_XML)
            runner = CommandRunner(dry_run=True)
            result = apply_resolutions(
                runner,
                settings,
                [MonitorSpec(2360, 1640, 60)],
                monitor_count=1,
                state_dir=root / "state",
            )
            self.assertTrue(result.ok)
            self.assertTrue(result.simulated)
            self.assertIn("将写入", result.message)
            self.assertEqual(settings.read_text(encoding="utf-8"), BASE_XML)

    def test_creates_missing_monitors_and_rolls_back(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            settings = self._write(root, NO_MONITORS_XML)
            runner = CommandRunner()
            state_dir = root / "state"

            result = apply_resolutions(
                runner,
                settings,
                [MonitorSpec(2360, 1640, 60)],
                monitor_count=1,
                state_dir=state_dir,
            )
            self.assertTrue(result.ok)
            text = settings.read_text(encoding="utf-8")
            self.assertIn("<monitors>", text)
            self.assertIn("<count>1</count>", text)
            self.assertIn("2360", text)

            rollback = reset_managed_resolutions(runner, settings, state_dir=state_dir)
            self.assertTrue(rollback.ok)
            final = settings.read_text(encoding="utf-8")
            self.assertNotIn("<monitors>", final)
            self.assertNotIn("2360", final)
            self.assertIn("1920", final)

    def test_creates_missing_resolutions_and_rolls_back(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            settings = self._write(root, NO_RESOLUTIONS_XML)
            runner = CommandRunner()
            state_dir = root / "state"

            result = apply_resolutions(
                runner,
                settings,
                [MonitorSpec(2360, 1640, 60)],
                monitor_count=1,
                state_dir=state_dir,
            )
            self.assertTrue(result.ok)
            text = settings.read_text(encoding="utf-8")
            self.assertIn("<resolutions>", text)
            self.assertIn("2360", text)

            rollback = reset_managed_resolutions(runner, settings, state_dir=state_dir)
            self.assertTrue(rollback.ok)
            final = settings.read_text(encoding="utf-8")
            self.assertNotIn("<resolutions>", final)
            self.assertNotIn("2360", final)
            self.assertIn("<count>1</count>", final)

    def test_unknown_schema_is_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            settings = self._write(root, "<not_vdd><foo>1</foo></not_vdd>")
            runner = CommandRunner()
            result = apply_resolutions(
                runner,
                settings,
                [MonitorSpec(1920, 1080, 60)],
                state_dir=root / "state",
            )
            self.assertFalse(result.ok)
            self.assertEqual(settings.read_text(encoding="utf-8"), "<not_vdd><foo>1</foo></not_vdd>")

    def test_legacy_monitor_list_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            settings = self._write(root, LEGACY_XML)
            runner = CommandRunner()
            state_dir = root / "state"
            result = apply_resolutions(
                runner,
                settings,
                [MonitorSpec(2732, 2048, 60)],
                monitor_count=1,
                state_dir=state_dir,
            )
            self.assertTrue(result.ok)
            self.assertIn("2732", settings.read_text(encoding="utf-8"))

            rollback = reset_managed_resolutions(runner, settings, state_dir=state_dir)
            self.assertTrue(rollback.ok)
            final = settings.read_text(encoding="utf-8")
            self.assertNotIn("2732", final)
            self.assertIn("1920", final)


class VddDetectionTest(unittest.TestCase):
    def _detect(self, root: Path, devices: list[dict]) -> VddStatus:
        (root / "vdd_settings.xml").write_text(BASE_XML, encoding="utf-8")
        payload = {"devices": devices, "controllers": []}
        runner = FakeRunner(powershell_response=json.dumps(payload))
        return detect_vdd(runner, extra_search_roots=[root])

    def test_ok_virtual_device_marks_driver_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            status = self._detect(
                Path(temp),
                [
                    {
                        "FriendlyName": "Virtual Display Driver",
                        "Status": "OK",
                    }
                ],
            )
        self.assertTrue(status.installed)
        self.assertTrue(status.device_ready)

    def test_non_ok_virtual_device_marks_driver_not_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            status = self._detect(
                Path(temp),
                [
                    {
                        "FriendlyName": "Virtual Display Driver",
                        "Status": "Error",
                    }
                ],
            )
        self.assertTrue(status.installed)
        self.assertFalse(status.device_ready)
        self.assertIn("VDD Control", status.detail)

    def test_settings_without_pnp_device_is_not_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            status = self._detect(Path(temp), [])
        self.assertTrue(status.installed)
        self.assertFalse(status.device_ready)


if __name__ == "__main__":
    unittest.main()
