"""Sunshine 配置合并与 device_id 提取测试。"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.common import CommandRunner
from core.sunshine import (
    SunshineConfigPlan,
    SunshineStatus,
    apply_config,
    build_config_plan,
    configured_output_name,
    default_resolution_mode,
    detect_sunshine,
    find_virtual_display_id,
    pairing_port,
    pairing_url,
    read_config,
    snapshot_log_sizes,
    wait_for_virtual_display_id,
)
from tests.fakes import FakeRunner

PHYSICAL_ID = "{11111111-1111-1111-1111-111111111111}"
VDD_ID = "{22222222-2222-2222-2222-222222222222}"
VDD_ID_2 = "{33333333-3333-3333-3333-333333333333}"


def device(
    device_id: str,
    display_name: str,
    friendly_name: str,
    *,
    primary: bool | None = None,
    manufacturer: str = "TMX",
) -> dict:
    info = None
    if primary is not None:
        info = {
            "primary": primary,
            "resolution": {"width": 2560, "height": 1600},
        }
    return {
        "device_id": device_id,
        "display_name": display_name,
        "edid": {"manufacturer_id": manufacturer},
        "friendly_name": friendly_name,
        "info": info,
    }


def display_block(devices: list[dict], prefix: str = "") -> str:
    return (
        f"{prefix}[2026-11-26 13:18:46.498]: Info: Currently available devices:\n"
        + json.dumps(devices, ensure_ascii=False)
        + "\n"
    )


def log_status(root: Path, *, config: Path | None = None) -> SunshineStatus:
    return SunshineStatus(
        installed=True,
        install_dir=root,
        executable=root / "sunshine.exe",
        config_path=config,
        service_name="SunshineService",
        service_state="running",
        version=None,
        detail="test",
    )

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
                resolution_mode="auto",
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

    def test_single_device_writes_manual_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / "sunshine.conf"
            config.write_text(CONFIG_TEXT, encoding="utf-8")
            plan = build_config_plan(
                address_family="ipv4",
                output_name=VDD_ID,
                resolution_mode="manual",
                manual_resolution=(1920, 1200),
            )
            result = apply_config(CommandRunner(), _status(config), plan)
            self.assertTrue(result.ok)

            text = config.read_text(encoding="utf-8")
            self.assertIn("dd_resolution_option = manual", text)
            self.assertIn("dd_manual_resolution = 1920x1200", text)
            self.assertIn("dd_refresh_rate_option = disabled", text)

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

    def test_existing_output_name_is_preserved_when_not_replacing(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / "sunshine.conf"
            config.write_text(
                f"output_name = {PHYSICAL_ID}\naddress_family = ipv4\n",
                encoding="utf-8",
            )
            result = apply_config(
                CommandRunner(),
                _status(config),
                SunshineConfigPlan(
                    output_name=None,
                    resolution_mode="manual",
                ),
            )
            self.assertTrue(result.ok)
            text = config.read_text(encoding="utf-8")
            self.assertIn(f"output_name = {PHYSICAL_ID}", text)


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

    def test_json_log_prefers_vdd_by_friendly_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            log = root / "sunshine.log"
            log.write_text(
                display_block(
                    [
                        device(
                            PHYSICAL_ID,
                            r"\\.\DISPLAY1",
                            "TL160ADMP11-0",
                            primary=True,
                        ),
                        device(
                            VDD_ID,
                            "",
                            "VDD by MTT",
                            primary=False,
                            manufacturer="MTT",
                        ),
                    ]
                ),
                encoding="utf-8",
            )
            logs: list[str] = []
            found = find_virtual_display_id(
                CommandRunner(log_sink=logs.append),
                log_status(root),
                log_path=log,
            )
            self.assertEqual(found, VDD_ID)
            self.assertIn("原始显示器列表", "\n".join(logs))
            self.assertIn("VDD by MTT", "\n".join(logs))

    def test_json_log_uses_newest_block(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            log = root / "sunshine.log"
            log.write_text(
                display_block(
                    [
                        device(
                            PHYSICAL_ID,
                            r"\\.\DISPLAY1",
                            "Laptop",
                            primary=True,
                        ),
                        device(VDD_ID, "", "VDD by MTT", primary=False),
                    ]
                )
                + "[2026-11-26 13:20:00.000]: Info: restart\n"
                + display_block(
                    [
                        device(
                            PHYSICAL_ID,
                            r"\\.\DISPLAY1",
                            "Laptop",
                            primary=True,
                        ),
                        device(VDD_ID_2, "", "VDD by MTT", primary=False),
                    ]
                ),
                encoding="utf-8",
            )
            found = find_virtual_display_id(
                CommandRunner(),
                log_status(root),
                log_path=log,
            )
            self.assertEqual(found, VDD_ID_2)

    def test_latest_startup_discards_old_device_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            log = root / "sunshine.log"
            log.write_text(
                "[2026-11-26 13:00:00.000]: Info: Sunshine version: old\n"
                + display_block(
                    [device(VDD_ID, "", "VDD by MTT", primary=False)]
                )
                + "[2026-11-26 13:10:00.000]: Info: Sunshine version: new\n"
                + display_block(
                    [device(VDD_ID_2, "", "VDD by MTT", primary=False)]
                ),
                encoding="utf-8",
            )
            found = find_virtual_display_id(
                CommandRunner(),
                log_status(root),
                log_path=log,
            )
            self.assertEqual(found, VDD_ID_2)

    def test_nested_info_and_braces_in_strings_do_not_truncate_device(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            log = root / "sunshine.log"
            virtual = device(VDD_ID, "", "VDD by MTT", primary=False)
            virtual["info"] = {
                "primary": False,
                "resolution": {"width": 1920, "height": 1200},
                "note": "nested } brace and \" quote",
            }
            log.write_text(
                display_block(
                    [
                        device(
                            PHYSICAL_ID,
                            r"\\.\DISPLAY1",
                            "Laptop",
                            primary=True,
                        ),
                        virtual,
                    ]
                ),
                encoding="utf-8",
            )
            found = find_virtual_display_id(
                CommandRunner(),
                log_status(root),
                log_path=log,
            )
            self.assertEqual(found, VDD_ID)

    def test_json_log_accepts_keyword_variants(self) -> None:
        variants = (
            ("VDD", "Other"),
            ("MTT Virtual", "Other"),
            ("Virtual Display", "Other"),
            ("IddSampleDriver", "Other"),
            ("Indirect Display", "Other"),
        )
        for friendly, manufacturer in variants:
            with self.subTest(friendly=friendly), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                log = root / "sunshine.log"
                log.write_text(
                    display_block(
                        [
                            device(
                                PHYSICAL_ID,
                                r"\\.\DISPLAY1",
                                "Laptop",
                                primary=True,
                            ),
                            device(
                                VDD_ID,
                                r"\\.\DISPLAY9",
                                friendly,
                                primary=False,
                                manufacturer=manufacturer,
                            ),
                        ]
                    ),
                    encoding="utf-8",
                )
                found = find_virtual_display_id(
                    CommandRunner(),
                    log_status(root),
                    log_path=log,
                )
                self.assertEqual(found, VDD_ID)

    def test_friendly_name_match_beats_other_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            log = root / "sunshine.log"
            log.write_text(
                display_block(
                    [
                        device(
                            PHYSICAL_ID,
                            "Virtual Panel",
                            "Laptop",
                            primary=True,
                        ),
                        device(
                            VDD_ID,
                            "",
                            "Indirect Display",
                            primary=False,
                        ),
                    ]
                ),
                encoding="utf-8",
            )
            found = find_virtual_display_id(
                CommandRunner(),
                log_status(root),
                log_path=log,
            )
            # 物理屏的 display_name 含 Virtual，但 friendly_name 不含关键词；
            # 应优先选择 friendly_name 命中 Indirect 的虚拟屏。
            self.assertEqual(found, VDD_ID)

    def test_json_log_falls_back_to_non_primary_display(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            log = root / "sunshine.log"
            log.write_text(
                display_block(
                    [
                        device(
                            PHYSICAL_ID,
                            r"\\.\DISPLAY1",
                            "Laptop",
                            primary=True,
                        ),
                        device(
                            VDD_ID,
                            r"\\.\DISPLAY2",
                            "Secondary Panel",
                            primary=False,
                        ),
                    ]
                ),
                encoding="utf-8",
            )
            found = find_virtual_display_id(
                CommandRunner(),
                log_status(root),
                log_path=log,
            )
            self.assertEqual(found, VDD_ID)

    def test_scan_does_not_require_currently_available_devices_title(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            log = root / "sunshine.log"
            log.write_text(
                "some unrelated startup line\n"
                + json.dumps(
                    [
                        device(
                            PHYSICAL_ID,
                            r"\\.\DISPLAY1",
                            "Laptop",
                            primary=True,
                        ),
                        device(VDD_ID_2, "", "VDD by MTT", primary=False),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            found = find_virtual_display_id(
                CommandRunner(),
                log_status(root),
                log_path=log,
            )
            self.assertEqual(found, VDD_ID_2)

    def test_wait_for_log_polls_only_new_content(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            log = root / "sunshine.log"
            log.write_text(
                json.dumps(
                    [device(VDD_ID, "", "VDD by MTT", primary=False)]
                )
                + "\n",
                encoding="utf-8",
            )
            status = log_status(root)
            offsets = snapshot_log_sizes(status, log_path=log)
            sleeps: list[float] = []

            def sleep(seconds: float) -> None:
                sleeps.append(seconds)
                with log.open("a", encoding="utf-8") as handle:
                    if len(sleeps) == 1:
                        handle.write(
                            json.dumps(
                                [
                                    device(
                                        PHYSICAL_ID,
                                        r"\\.\DISPLAY1",
                                        "Laptop",
                                        primary=True,
                                    )
                                ]
                            )
                            + "\n"
                        )
                    else:
                        handle.write(
                            json.dumps(
                                [
                                    device(
                                        VDD_ID_2,
                                        "",
                                        "VDD by MTT",
                                        primary=False,
                                    )
                                ]
                            )
                            + "\n"
                        )

            logs: list[str] = []
            found = wait_for_virtual_display_id(
                CommandRunner(log_sink=logs.append),
                status,
                offsets=offsets,
                timeout_s=2.0,
                interval_s=0.5,
                sleep=sleep,
                log_path=log,
            )
            self.assertEqual(found, VDD_ID_2)
            self.assertEqual(len(sleeps), 2)
            self.assertTrue(any("轮询" in line for line in logs))

    def test_timeout_reports_pnp_and_last_log_lines(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            log = root / "sunshine.log"
            log.write_text(
                json.dumps(
                    [
                        device(
                            PHYSICAL_ID,
                            r"\\.\DISPLAY1",
                            "Laptop",
                            primary=True,
                        )
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            runner = FakeRunner(
                powershell_response="Status  FriendlyName  InstanceId\nOK      Laptop      DISPLAY\\LAPTOP"
            )
            self.assertIsNone(
                wait_for_virtual_display_id(
                    runner,
                    log_status(root),
                    offsets=snapshot_log_sizes(log_status(root), log_path=log),
                    timeout_s=0.01,
                    interval_s=0.01,
                    sleep=lambda _seconds: None,
                    log_path=log,
                )
            )
            self.assertTrue(
                any("Get-PnpDevice -Class Display" in log for log in runner.logs)
            )
            self.assertTrue(any("最后 1 行" in log for log in runner.logs))

    def test_single_physical_display_is_not_selected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            log = root / "sunshine.log"
            log.write_text(
                display_block(
                    [
                        device(
                            PHYSICAL_ID,
                            r"\\.\DISPLAY1",
                            "Laptop",
                            primary=True,
                        )
                    ]
                ),
                encoding="utf-8",
            )
            found = find_virtual_display_id(
                CommandRunner(),
                log_status(root),
                log_path=log,
            )
            self.assertIsNone(found)

    def test_json_log_normalizes_missing_braces(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            log = root / "sunshine.log"
            log.write_text(
                display_block(
                    [
                        device(
                            VDD_ID[1:-1],
                            "",
                            "VDD by MTT",
                            primary=False,
                        )
                    ]
                ),
                encoding="utf-8",
            )
            found = find_virtual_display_id(
                CommandRunner(),
                log_status(root),
                log_path=log,
            )
            self.assertEqual(found, VDD_ID)

    def test_truncated_newest_block_uses_previous_complete_block(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            log = root / "sunshine.log"
            log.write_text(
                display_block([device(VDD_ID, "", "VDD by MTT", primary=False)])
                + "[2026-11-26 13:20:00.000]: Info: Currently available devices:\n"
                + '[{"device_id": "' + VDD_ID_2 + '", "friendly_name": "VDD',
                encoding="utf-8",
            )
            found = find_virtual_display_id(
                CommandRunner(),
                log_status(root),
                log_path=log,
            )
            self.assertEqual(found, VDD_ID)

    def test_localappdata_lizardbyte_log_is_discovered(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            log = root / "LizardByte" / "Sunshine" / "sunshine.log"
            log.parent.mkdir(parents=True)
            log.write_text(
                display_block([device(VDD_ID, "", "VDD by MTT", primary=False)]),
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {"LOCALAPPDATA": str(root), "ProgramFiles": str(root / "pf")},
                clear=False,
            ), patch("core.sunshine.DEFAULT_INSTALL_DIR", root / "default"):
                found = find_virtual_display_id(
                    CommandRunner(),
                    log_status(root / "install"),
                )
            self.assertEqual(found, VDD_ID)

    def test_configured_output_name_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = root / "sunshine.conf"
            config.write_text(
                f"output_name = {PHYSICAL_ID}\n",
                encoding="utf-8",
            )
            status = log_status(root, config=config)
            self.assertEqual(
                configured_output_name(CommandRunner(), status),
                PHYSICAL_ID,
            )
            config.write_text("output_name = not-a-guid\n", encoding="utf-8")
            self.assertIsNone(configured_output_name(CommandRunner(), status))

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

    def test_pairing_url_uses_localhost_and_pin(self) -> None:
        self.assertEqual(pairing_url(), "https://localhost:47990/pin")
        self.assertEqual(pairing_url(port=49000), "https://localhost:49000/pin")

    def test_pairing_url_uses_custom_config_port_plus_one(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / "sunshine.conf"
            config.write_text("port = 50000\n", encoding="utf-8")
            status = _status(config)
            self.assertEqual(pairing_port(status), 50001)
            self.assertEqual(
                pairing_url(status),
                "https://localhost:50001/pin",
            )

    def test_default_resolution_mode(self) -> None:
        self.assertEqual(default_resolution_mode(1), "manual")
        self.assertEqual(default_resolution_mode(2), "auto")


if __name__ == "__main__":
    unittest.main()
