"""一键还原与卸载核心测试：顺序、Dry-Run、MSI、PnP 与目录清理。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.common import CommandResult, OperationResult
from core.uninstaller import (
    UninstallOptions,
    UninstallState,
    UninstallStep,
    run_uninstall,
)
from tests.fakes import FakeRunner

SUNSHINE_CODE = "{11111111-2222-3333-4444-555555555555}"
VDD_INSTANCE = r"ROOT\SYSTEM\0001"


class ScriptedRunner(FakeRunner):
    """按脚本类型返回可控的注册表、服务、PnP 和命令结果。"""

    def __init__(
        self,
        *,
        registry: dict | None = None,
        service_output: str = "STOPPED",
        cleanup_output: str = "MISSING",
        devices: list[dict] | None = None,
        mutate_results: dict[tuple[str, ...], CommandResult] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.registry = registry or {"apps": []}
        self.service_output = service_output
        self.cleanup_output = cleanup_output
        self.devices = devices or []
        self.mutate_results = mutate_results or {}

    def query_powershell(self, script: str, *, timeout_s: float | None = None) -> CommandResult:
        self.powershell_scripts.append(script)
        if "Uninstall" in script:
            payload = json.dumps(self.registry)
        elif "Get-PnpDevice -Class Display" in script:
            payload = json.dumps(self.devices)
        else:
            payload = "{}"
        return CommandResult(("pwsh",), 0, payload)

    def mutate_powershell(self, script: str, *, timeout_s: float | None = None) -> CommandResult:
        self.powershell_mutations.append(script)
        if self.dry_run:
            return CommandResult(("pwsh",), 0, simulated=True)
        if "sc.exe delete" in script:
            return CommandResult(("pwsh",), 0, self.cleanup_output)
        if "Stop-Service" in script:
            return CommandResult(("pwsh",), 0, self.service_output)
        return CommandResult(("pwsh",), 0, "OK")

    def mutate(self, argv, *, timeout_s: float | None = None) -> CommandResult:
        argv_tuple = tuple(str(item) for item in argv)
        self.commands.append(argv_tuple)
        if self.dry_run:
            return CommandResult(argv_tuple, 0, simulated=True)
        result = self.mutate_results.get(argv_tuple)
        if result is not None:
            return result
        return CommandResult(argv_tuple, 0, "OK")


def only(**overrides) -> UninstallOptions:
    values = {
        "restore_display": False,
        "restore_power": False,
        "remove_firewall": False,
        "uninstall_sunshine": False,
        "uninstall_vdd": False,
        "delete_vdd_directory": False,
        "clean_cache": False,
    }
    values.update(overrides)
    return UninstallOptions(**values)


class UninstallOptionsTest(unittest.TestCase):
    def test_default_selection_is_all(self) -> None:
        options = UninstallOptions()
        self.assertTrue(options.has_selection)
        self.assertEqual(
            options.selected_keys,
            (
                "display",
                "power",
                "firewall",
                "sunshine",
                "vdd",
                "vdd_directory",
                "cache",
            ),
        )
        self.assertEqual(options.warnings(), ())

    def test_vdd_without_display_restore_warns(self) -> None:
        options = only(
            uninstall_vdd=True,
            restore_display=False,
            uninstall_sunshine=True,
        )
        self.assertTrue(
            any("黑屏" in warning for warning in options.warnings())
        )

    def test_cache_without_power_restore_warns(self) -> None:
        options = only(clean_cache=True, restore_power=False)
        self.assertTrue(
            any("power_state.json" in warning for warning in options.warnings())
        )

    def test_vdd_without_sunshine_warns(self) -> None:
        options = only(
            uninstall_vdd=True,
            restore_display=True,
            uninstall_sunshine=False,
        )
        self.assertTrue(
            any("保留 Sunshine" in warning for warning in options.warnings())
        )


class UninstallOrderTest(unittest.TestCase):
    def _success_step(self, key: str, label: str) -> UninstallStep:
        return UninstallStep(key, label, UninstallState.SUCCESS, "ok")

    def test_fixed_safety_order(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state_dir = Path(temp)
            (state_dir / "power_state.json").write_text("{}", encoding="utf-8")
            progress: list[tuple[int, str]] = []
            steps: list[UninstallStep] = []
            options = UninstallOptions(
                state_dir=state_dir,
                vdd_install_dir=state_dir / "vdd",
            )
            with (
                patch(
                    "core.uninstaller.restore_native_display",
                    return_value=OperationResult.success("display"),
                ),
                patch(
                    "core.uninstaller._stop_sunshine_service",
                    return_value=self._success_step("sunshine_stop", "stop"),
                ),
                patch(
                    "core.uninstaller._uninstall_vdd",
                    return_value=self._success_step("vdd", "vdd"),
                ),
                patch(
                    "core.uninstaller._remove_vdd_directory",
                    return_value=self._success_step("vdd_directory", "dir"),
                ),
                patch(
                    "core.uninstaller._uninstall_sunshine",
                    return_value=self._success_step("sunshine", "sunshine"),
                ),
                patch(
                    "core.uninstaller.remove_firewall_rule",
                    return_value=OperationResult.success("firewall"),
                ),
                patch(
                    "core.uninstaller.restore_previous_profile",
                    return_value=OperationResult.success("power"),
                ),
                patch(
                    "core.uninstaller._clean_cache",
                    return_value=self._success_step("cache", "cache"),
                ),
            ):
                report = run_uninstall(
                    FakeRunner(),
                    options,
                    progress=lambda value, text: progress.append((value, text)),
                    on_step=steps.append,
                )

        self.assertEqual(
            [step.key for step in steps],
            [
                "display",
                "sunshine_stop",
                "vdd",
                "vdd_directory",
                "sunshine",
                "firewall",
                "power",
                "cache",
            ],
        )
        self.assertEqual(progress[-1], (100, "清理流程结束"))
        self.assertTrue(
            all(
                left[0] <= right[0]
                for left, right in zip(progress, progress[1:])
            )
        )
        self.assertTrue(report.ok)

    def test_display_failure_skips_vdd_and_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state_dir = Path(temp)
            options = UninstallOptions(
                state_dir=state_dir,
                vdd_install_dir=state_dir / "vdd",
            )
            with (
                patch(
                    "core.uninstaller.restore_native_display",
                    return_value=OperationResult.failure("display failed"),
                ),
                patch(
                    "core.uninstaller._stop_sunshine_service",
                    return_value=self._success_step("sunshine_stop", "stop"),
                ),
                patch("core.uninstaller._uninstall_vdd") as uninstall_vdd,
                patch("core.uninstaller._remove_vdd_directory") as remove_dir,
                patch(
                    "core.uninstaller._uninstall_sunshine",
                    return_value=self._success_step("sunshine", "sunshine"),
                ),
                patch(
                    "core.uninstaller.remove_firewall_rule",
                    return_value=OperationResult.success("firewall"),
                ),
                patch(
                    "core.uninstaller.restore_previous_profile",
                    return_value=OperationResult.success("power"),
                ),
                patch(
                    "core.uninstaller._clean_cache",
                    return_value=self._success_step("cache", "cache"),
                ),
            ):
                report = run_uninstall(FakeRunner(), options)

        uninstall_vdd.assert_not_called()
        remove_dir.assert_not_called()
        vdd_step = next(step for step in report.steps if step.key == "vdd")
        dir_step = next(step for step in report.steps if step.key == "vdd_directory")
        self.assertEqual(vdd_step.state, UninstallState.SKIPPED)
        self.assertIn("黑屏", vdd_step.message)
        self.assertEqual(dir_step.state, UninstallState.SKIPPED)
        self.assertFalse(report.ok)

    def test_stop_failure_skips_vdd(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            options = UninstallOptions(
                state_dir=Path(temp),
                vdd_install_dir=Path(temp) / "vdd",
            )
            failed_stop = UninstallStep(
                "sunshine_stop",
                "stop",
                UninstallState.FAILED,
                "stop failed",
            )
            with (
                patch(
                    "core.uninstaller.restore_native_display",
                    return_value=OperationResult.success("display"),
                ),
                patch(
                    "core.uninstaller._stop_sunshine_service",
                    return_value=failed_stop,
                ),
                patch("core.uninstaller._uninstall_vdd") as uninstall_vdd,
                patch("core.uninstaller._remove_vdd_directory"),
                patch(
                    "core.uninstaller._uninstall_sunshine",
                    return_value=self._success_step("sunshine", "sunshine"),
                ),
                patch(
                    "core.uninstaller.remove_firewall_rule",
                    return_value=OperationResult.success("firewall"),
                ),
                patch(
                    "core.uninstaller.restore_previous_profile",
                    return_value=OperationResult.success("power"),
                ),
                patch(
                    "core.uninstaller._clean_cache",
                    return_value=self._success_step("cache", "cache"),
                ),
            ):
                report = run_uninstall(FakeRunner(), options)

        uninstall_vdd.assert_not_called()
        vdd_step = next(step for step in report.steps if step.key == "vdd")
        self.assertEqual(vdd_step.state, UninstallState.SKIPPED)
        self.assertIn("SunshineService", vdd_step.message)


class UninstallVddTest(unittest.TestCase):
    def test_removes_pnp_device_and_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vdd_dir = Path(temp) / "VirtualDisplayDriver"
            vdd_dir.mkdir()
            (vdd_dir / "vdd_settings.xml").write_text("<x/>", encoding="utf-8")
            runner = ScriptedRunner(
                devices=[
                    {
                        "FriendlyName": "Virtual Display Driver",
                        "InstanceId": VDD_INSTANCE,
                        "Status": "OK",
                    }
                ]
            )
            report = run_uninstall(
                runner,
                only(
                    uninstall_vdd=True,
                    delete_vdd_directory=True,
                    vdd_install_dir=vdd_dir,
                ),
            )
            self.assertFalse(vdd_dir.exists())

        self.assertTrue(report.ok)
        self.assertIn(
            ("pnputil.exe", "/remove-device", VDD_INSTANCE),
            runner.commands,
        )

    def test_dry_run_keeps_driver_and_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vdd_dir = Path(temp) / "VirtualDisplayDriver"
            vdd_dir.mkdir()
            runner = ScriptedRunner(
                devices=[
                    {
                        "FriendlyName": "Virtual Display Driver",
                        "InstanceId": VDD_INSTANCE,
                        "Status": "OK",
                    }
                ],
                dry_run=True,
            )
            report = run_uninstall(
                runner,
                only(
                    uninstall_vdd=True,
                    delete_vdd_directory=True,
                    vdd_install_dir=vdd_dir,
                ),
            )
            self.assertTrue(vdd_dir.exists())

        self.assertEqual(report.steps[0].state, UninstallState.SIMULATED)
        self.assertEqual(report.steps[1].state, UninstallState.SIMULATED)
        self.assertIn(
            ("pnputil.exe", "/remove-device", VDD_INSTANCE),
            runner.commands,
        )

    def test_remove_failure_keeps_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vdd_dir = Path(temp) / "VirtualDisplayDriver"
            vdd_dir.mkdir()
            runner = ScriptedRunner(
                devices=[
                    {
                        "FriendlyName": "Virtual Display Driver",
                        "InstanceId": VDD_INSTANCE,
                        "Status": "OK",
                    }
                ],
                mutate_results={
                    ("pnputil.exe", "/remove-device", VDD_INSTANCE): CommandResult(
                        ("pnputil.exe",),
                        1,
                        stderr="remove failed",
                    )
                },
            )
            report = run_uninstall(
                runner,
                only(
                    uninstall_vdd=True,
                    delete_vdd_directory=True,
                    vdd_install_dir=vdd_dir,
                ),
            )
            self.assertTrue(vdd_dir.exists())

        self.assertEqual(report.steps[0].state, UninstallState.FAILED)
        self.assertEqual(report.steps[1].state, UninstallState.SKIPPED)

    def test_no_device_is_skipped(self) -> None:
        report = run_uninstall(
            ScriptedRunner(devices=[]),
            only(uninstall_vdd=True),
        )
        self.assertEqual(report.steps[0].state, UninstallState.SKIPPED)
        self.assertTrue(report.ok)

    def test_does_not_remove_other_virtual_display_adapters(self) -> None:
        gameviewer = r"ROOT\DISPLAY\0000"
        runner = ScriptedRunner(
            devices=[
                {
                    "FriendlyName": "GameViewer Virtual Display Adapter",
                    "InstanceId": gameviewer,
                    "Status": "OK",
                },
                {
                    "FriendlyName": "Virtual Display Driver",
                    "InstanceId": VDD_INSTANCE,
                    "Status": "OK",
                },
            ]
        )
        report = run_uninstall(runner, only(uninstall_vdd=True))

        self.assertIn(
            ("pnputil.exe", "/remove-device", VDD_INSTANCE),
            runner.commands,
        )
        self.assertNotIn(
            ("pnputil.exe", "/remove-device", gameviewer),
            runner.commands,
        )
        self.assertEqual(
            [step.state for step in report.steps],
            [UninstallState.SUCCESS],
        )

    def test_refuses_non_vdd_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            other = Path(temp) / "OtherFolder"
            other.mkdir()
            (other / "keep.txt").write_text("keep", encoding="utf-8")
            report = run_uninstall(
                ScriptedRunner(
                    devices=[
                        {
                            "FriendlyName": "Virtual Display Driver",
                            "InstanceId": VDD_INSTANCE,
                            "Status": "OK",
                        }
                    ]
                ),
                only(
                    uninstall_vdd=True,
                    delete_vdd_directory=True,
                    vdd_install_dir=other,
                ),
            )
            self.assertTrue((other / "keep.txt").exists())

        directory_step = next(
            step for step in report.steps if step.key == "vdd_directory"
        )
        self.assertEqual(directory_step.state, UninstallState.FAILED)
        self.assertIn("标志文件", directory_step.message)


class UninstallSunshineTest(unittest.TestCase):
    def _registry(self) -> dict:
        return {
            "apps": [
                {
                    "DisplayName": "Sunshine",
                    "DisplayVersion": "2026.914.233613",
                    "PSChildName": SUNSHINE_CODE,
                }
            ]
        }

    def test_uses_msi_product_code(self) -> None:
        runner = ScriptedRunner(registry=self._registry())
        report = run_uninstall(
            runner,
            only(uninstall_sunshine=True),
        )

        self.assertIn(
            ("msiexec", "/x", SUNSHINE_CODE, "/passive", "/norestart"),
            runner.commands,
        )
        self.assertTrue(
            any("Stop-Service" in script for script in runner.powershell_mutations)
        )
        self.assertTrue(report.ok)

    def test_reboot_required_is_success(self) -> None:
        runner = ScriptedRunner(
            registry=self._registry(),
            mutate_results={
                ("msiexec", "/x", SUNSHINE_CODE, "/passive", "/norestart"):
                    CommandResult(("msiexec",), 3010)
            },
        )
        report = run_uninstall(runner, only(uninstall_sunshine=True))
        step = report.steps[-1]
        self.assertEqual(step.state, UninstallState.SUCCESS)
        self.assertIn("重启", step.message)

    def test_msi_not_installed_is_skipped(self) -> None:
        runner = ScriptedRunner(
            registry=self._registry(),
            mutate_results={
                ("msiexec", "/x", SUNSHINE_CODE, "/passive", "/norestart"):
                    CommandResult(("msiexec",), 1605)
            },
        )
        report = run_uninstall(runner, only(uninstall_sunshine=True))
        step = report.steps[-1]
        self.assertEqual(step.state, UninstallState.SKIPPED)
        self.assertIn("1605", " ".join(step.details))

    def test_msi_not_installed_but_cleanup_failure_is_warning(self) -> None:
        runner = ScriptedRunner(
            registry=self._registry(),
            mutate_results={
                ("msiexec", "/x", SUNSHINE_CODE, "/passive", "/norestart"):
                    CommandResult(("msiexec",), 1605)
            },
        )
        with patch(
            "core.uninstaller._cleanup_sunshine_service",
            return_value=(
                UninstallState.FAILED,
                "cleanup failed",
                ("Access denied",),
            ),
        ):
            report = run_uninstall(runner, only(uninstall_sunshine=True))
        step = report.steps[-1]
        self.assertEqual(step.state, UninstallState.WARNING)
        self.assertIn("残留清理失败", step.message)

    def test_missing_product_code_cleans_stale_service(self) -> None:
        runner = ScriptedRunner(registry={"apps": []}, cleanup_output="REMOVED")
        report = run_uninstall(runner, only(uninstall_sunshine=True))
        step = report.steps[-1]
        self.assertEqual(step.state, UninstallState.WARNING)
        self.assertIn("ProductCode", step.message)

    def test_missing_product_code_and_service_is_skipped(self) -> None:
        runner = ScriptedRunner(registry={"apps": []}, cleanup_output="MISSING")
        report = run_uninstall(runner, only(uninstall_sunshine=True))
        step = report.steps[-1]
        self.assertEqual(step.state, UninstallState.SKIPPED)

    def test_ignores_unrelated_sunshine_named_app(self) -> None:
        runner = ScriptedRunner(
            registry={
                "apps": [
                    {
                        "DisplayName": "Sunshine Coast Tours",
                        "Publisher": "Another Company",
                        "PSChildName": SUNSHINE_CODE,
                    }
                ]
            },
            cleanup_output="MISSING",
        )
        report = run_uninstall(runner, only(uninstall_sunshine=True))

        self.assertFalse(
            any(command[0] == "msiexec" for command in runner.commands)
        )
        step = report.steps[-1]
        self.assertEqual(step.state, UninstallState.SKIPPED)

    def test_accepts_lizardbyte_publisher_variant(self) -> None:
        runner = ScriptedRunner(
            registry={
                "apps": [
                    {
                        "DisplayName": "Sunshine Beta",
                        "Publisher": "LizardByte",
                        "PSChildName": SUNSHINE_CODE,
                    }
                ]
            },
        )
        run_uninstall(runner, only(uninstall_sunshine=True))
        self.assertIn(
            ("msiexec", "/x", SUNSHINE_CODE, "/passive", "/norestart"),
            runner.commands,
        )


class UninstallPowerAndCacheTest(unittest.TestCase):
    def test_power_restore_deletes_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state_dir = Path(temp)
            state_path = state_dir / "power_state.json"
            state_path.write_text(
                json.dumps(
                    {
                        "ac_sleep_seconds": 900,
                        "ac_display_off_seconds": 600,
                    }
                ),
                encoding="utf-8",
            )
            report = run_uninstall(
                FakeRunner(),
                only(restore_power=True, state_dir=state_dir),
            )
            self.assertFalse(state_path.exists())

        self.assertEqual(report.steps[0].state, UninstallState.SUCCESS)

    def test_power_missing_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            report = run_uninstall(
                FakeRunner(),
                only(restore_power=True, state_dir=Path(temp)),
            )
        self.assertEqual(report.steps[0].state, UninstallState.SKIPPED)

    def test_power_failure_preserves_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state_dir = Path(temp)
            (state_dir / "power_state.json").write_text(
                "not-json",
                encoding="utf-8",
            )
            (state_dir / "crash.log").write_text("log", encoding="utf-8")
            report = run_uninstall(
                FakeRunner(),
                only(restore_power=True, clean_cache=True, state_dir=state_dir),
            )
            self.assertTrue((state_dir / "power_state.json").exists())
            self.assertTrue((state_dir / "crash.log").exists())

        power = next(step for step in report.steps if step.key == "power")
        cache = next(step for step in report.steps if step.key == "cache")
        self.assertEqual(power.state, UninstallState.FAILED)
        self.assertEqual(cache.state, UninstallState.SKIPPED)
        self.assertIn("已保留", cache.message)

    def test_cache_cleanup_removes_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state_dir = Path(temp) / "CampusStreamHelper"
            (state_dir / "downloads").mkdir(parents=True)
            (state_dir / "downloads" / "a.msi").write_bytes(b"x")
            (state_dir / "crash.log").write_text("log", encoding="utf-8")
            report = run_uninstall(
                FakeRunner(),
                only(clean_cache=True, state_dir=state_dir),
            )
            self.assertFalse(state_dir.exists())

        self.assertEqual(report.steps[0].state, UninstallState.SUCCESS)


    def test_cache_dry_run_keeps_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state_dir = Path(temp) / "CampusStreamHelper"
            state_dir.mkdir()
            (state_dir / "crash.log").write_text("log", encoding="utf-8")
            report = run_uninstall(
                FakeRunner(dry_run=True),
                only(clean_cache=True, state_dir=state_dir),
            )
            self.assertTrue(state_dir.exists())

        self.assertEqual(report.steps[0].state, UninstallState.SIMULATED)

    def test_refuses_root_directory(self) -> None:
        report = run_uninstall(
            FakeRunner(),
            only(clean_cache=True, state_dir=Path("C:/")),
        )
        self.assertEqual(report.steps[0].state, UninstallState.FAILED)
        self.assertIn("不安全", report.steps[0].message)

    def test_refuses_non_cache_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            other = Path(temp) / "OtherFolder"
            other.mkdir()
            (other / "keep.txt").write_text("keep", encoding="utf-8")
            report = run_uninstall(
                FakeRunner(),
                only(clean_cache=True, state_dir=other),
            )
            self.assertTrue((other / "keep.txt").exists())

        self.assertEqual(report.steps[0].state, UninstallState.FAILED)
        self.assertIn("CampusStreamHelper", report.steps[0].message)


if __name__ == "__main__":
    unittest.main()
