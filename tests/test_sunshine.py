"""Sunshine 配置合并与 device_id 提取测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from core.common import CommandRunner
from core.sunshine import (
    SunshineConfigPlan,
    SunshineStatus,
    apply_config,
    build_config_plan,
    default_resolution_mode,
    detect_sunshine,
    find_virtual_display_id,
    pairing_url,
    read_config,
)
from tests.fakes import FakeRunner

CONFIG_TEXT = """# Sunshine config
address_family = ipv6
output_name =
other = keep
"""


def _status(config_path: Path) -> SunshineStatus:
    return SunshineStatus(
        installed=True,
        install_dir=config_path.parent.parent,
        executable=config_path.parent.parent / "sunshine.exe",
        config_path=config_path,
        service_name="SunshineService",
        service_state="running",
        version="0.23.1",
        detail="test",
    )


class SunshineConfigTest(unittest.TestCase):
    def test_read_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / "sunshine.conf"
            config.write_text(CONFIG_TEXT, encoding="utf-8")
            state = read_config(CommandRunner(), config)
            self.assertEqual(state.values["address_family"], "ipv6")
            self.assertEqual(state.values["other"], "keep")

    def test_apply_config_preserves_unknown_lines(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / "sunshine.conf"
            config.write_text(CONFIG_TEXT, encoding="utf-8")
            runner = CommandRunner()
            plan = build_config_plan(
                address_family="ipv4",
                output_name="{11111111-1111-1111-1111-111111111111}",
                resolution_mode="follow_client",
            )
            result = apply_config(runner, _status(config), plan)
            self.assertTrue(result.ok)
            self.assertIsNotNone(result.backup_path)

            text = config.read_text(encoding="utf-8")
            self.assertIn("# Sunshine config", text)
            self.assertIn("address_family = ipv4", text)
            self.assertIn("output_name = {11111111-1111-1111-1111-111111111111}", text)
            self.assertIn("other = keep", text)
            self.assertIn("dd_configuration_option = ensure_only_display", text)
            self.assertIn("dd_config_revert_on_disconnect = enabled", text)
            self.assertIn("dd_resolution_option = auto", text)
            self.assertIn("dd_refresh_rate_option = auto", text)

    def test_dry_run_does_not_modify_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / "sunshine.conf"
            config.write_text(CONFIG_TEXT, encoding="utf-8")
            runner = CommandRunner(dry_run=True)
            result = apply_config(
                runner,
                _status(config),
                SunshineConfigPlan(output_name="{22222222-2222-2222-2222-222222222222}"),
            )
            self.assertTrue(result.ok)
            self.assertTrue(result.simulated)
            self.assertIn("将写入", result.message)
            self.assertEqual(config.read_text(encoding="utf-8"), CONFIG_TEXT)

    def test_missing_config_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / "missing.conf"
            result = apply_config(CommandRunner(), _status(config), SunshineConfigPlan())
            self.assertFalse(result.ok)


class SunshineDetectionTest(unittest.TestCase):
    def test_find_virtual_display_id_prefers_virtual_line(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            log = Path(temp) / "sunshine.log"
            log.write_text(
                "Info: Detected display: \\\\.\\DISPLAY1 {11111111-1111-1111-1111-111111111111}\n"
                "Info: Virtual Display Driver output {22222222-2222-2222-2222-222222222222}\n",
                encoding="utf-8",
            )
            status = SunshineStatus(
                installed=True,
                install_dir=Path(temp),
                executable=Path(temp) / "sunshine.exe",
                config_path=None,
                service_name="SunshineService",
                service_state="running",
                version=None,
                detail="test",
            )
            found = find_virtual_display_id(CommandRunner(), status, log_path=log)
            self.assertEqual(found, "{22222222-2222-2222-2222-222222222222}")

    def test_detect_sunshine_from_registry_and_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "sunshine.exe").write_text("stub", encoding="utf-8")
            (root / "config").mkdir()
            (root / "config" / "sunshine.conf").write_text("", encoding="utf-8")
            payload = {
                "apps": [
                    {
                        "DisplayName": "Sunshine",
                        "DisplayVersion": "0.23.1",
                        "InstallLocation": str(root),
                    }
                ],
                "service": {"Name": "SunshineService", "Status": "Running"},
            }
            runner = FakeRunner(powershell_response=json.dumps(payload))
            status = detect_sunshine(runner, search_roots=[root])
            self.assertTrue(status.installed)
            self.assertEqual(status.service_state, "running")
            self.assertEqual(status.version, "0.23.1")
            self.assertEqual(status.config_path, root / "config" / "sunshine.conf")

    def test_service_status_normalization(self) -> None:
        runner = FakeRunner(
            powershell_response=json.dumps(
                {"apps": [], "service": {"Name": "SunshineService", "Status": 1}}
            )
        )
        status = detect_sunshine(runner, search_roots=[])
        self.assertEqual(status.service_state, "stopped")

    def test_pairing_url(self) -> None:
        self.assertEqual(pairing_url("192.168.1.10"), "https://192.168.1.10:47990")
        self.assertEqual(pairing_url("2001:da8::1"), "https://[2001:da8::1]:47990")

    def test_default_resolution_mode(self) -> None:
        self.assertEqual(default_resolution_mode(1), "do_not_change")
        self.assertEqual(default_resolution_mode(2), "follow_client")


if __name__ == "__main__":
    unittest.main()
