"""SetupWorker 测试：执行顺序与现有 output_name 兜底。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.common import OperationResult
from core.network import NetworkReport
from core.plan import SetupPlan, SetupSummary
from core.sunshine import SunshineStatus
from core.vdd import MonitorSpec, VddStatus
from ui.workers import SetupWorker

VDD_ID = "{22222222-2222-2222-2222-222222222222}"
EXISTING_ID = "{44444444-4444-4444-4444-444444444444}"


class SetupWorkerOrderTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.config = self.root / "sunshine.conf"
        self.config.write_text("address_family = ipv4\n", encoding="utf-8")
        self.settings = self.root / "vdd_settings.xml"
        self.settings.write_text("<resolutions />", encoding="utf-8")

    def _plan(self) -> SetupPlan:
        return SetupPlan(
            platforms=("ipados",),
            monitors=(MonitorSpec(2360, 1640, 60, "iPad Air 11"),),
            enable_firewall=False,
            apply_power_profile=False,
            reload_driver=True,
            dry_run=False,
        )

    def _statuses(self) -> tuple[SunshineStatus, VddStatus, NetworkReport]:
        sunshine = SunshineStatus(
            installed=True,
            install_dir=self.root,
            executable=self.root / "sunshine.exe",
            config_path=self.config,
            service_name="SunshineService",
            service_state="running",
            version="test",
            detail="test",
        )
        vdd = VddStatus(
            installed=True,
            install_dir=self.root,
            settings_path=self.settings,
            driver_version="test",
            schema="resolutions",
            configured_monitor_count=1,
            configured_resolution_count=1,
            detail="test",
        )
        network = NetworkReport(
            adapters=(),
            scenario="ethernet_ready",
            preferred_ipv4="192.168.1.10",
            preferred_ipv6=None,
            firewall_rule_present=True,
            warnings=(),
            generated_at="2026-11-26T00:00:00+08:00",
        )
        return sunshine, vdd, network

    def _run(
        self,
        *,
        found_id: str | None,
        existing_id: str | None = None,
    ) -> tuple[list, list, list, SetupSummary]:
        sunshine, vdd, network = self._statuses()
        if existing_id is not None:
            self.config.write_text(
                f"output_name = {existing_id}\naddress_family = ipv4\n",
                encoding="utf-8",
            )

        calls: list[str] = []
        sleeps: list[float] = []
        captured_plans: list = []
        summaries: list = []
        restart_count = 0

        def apply_resolutions(*_args, **_kwargs):
            calls.append("apply_resolutions")
            return OperationResult.success("vdd ok")

        def reload_driver(*_args, **_kwargs):
            calls.append("reload_driver")
            return OperationResult.success("reload ok")

        def snapshot_log(*_args, **_kwargs):
            calls.append("snapshot_log")
            return {}

        def restart_service(*_args, **_kwargs):
            nonlocal restart_count
            restart_count += 1
            name = "restart_initial" if restart_count == 1 else "restart_final"
            calls.append(name)
            return OperationResult.success(name)

        def wait_log(*_args, **_kwargs):
            calls.append("wait_log")
            self.assertEqual(restart_count, 1)
            return found_id

        def find_id(*_args, **_kwargs):
            calls.append("find_id")
            self.assertEqual(restart_count, 1)
            return found_id

        def apply_sunshine_config(_runner, _status, plan):
            calls.append("apply_config")
            captured_plans.append(plan)
            return OperationResult.success("config ok")

        worker = SetupWorker(self._plan(), sleep=lambda seconds: sleeps.append(seconds))
        worker.log.connect(lambda _message: None)
        worker.progress.connect(lambda _value, _text: None)
        worker.finished.connect(summaries.append)
        worker.failed.connect(lambda message: self.fail(message))

        with (
            patch("ui.workers.detect_sunshine", return_value=sunshine),
            patch("ui.workers.detect_vdd", return_value=vdd),
            patch("ui.workers.collect_network_report", return_value=network),
            patch("ui.workers.apply_resolutions", side_effect=apply_resolutions),
            patch("ui.workers.reload_driver", side_effect=reload_driver),
            patch("ui.workers.restart_service", side_effect=restart_service),
            patch("ui.workers.snapshot_log_sizes", side_effect=snapshot_log),
            patch(
                "ui.workers.wait_for_virtual_display_id",
                side_effect=wait_log,
            ),
            patch("ui.workers.find_virtual_display_id", side_effect=find_id),
            patch("ui.workers.apply_config", side_effect=apply_sunshine_config),
        ):
            worker.run()

        self.assertEqual(len(summaries), 1)
        self.assertTrue(
            summaries[0].success,
            summaries[0].failed_outcomes,
        )
        return calls, sleeps, captured_plans, summaries[0]

    def test_execution_order_restarts_before_extracting_id(self) -> None:
        calls, sleeps, plans, summary = self._run(found_id=VDD_ID)
        outcomes = summary.outcomes

        self.assertEqual(
            calls,
            [
                "apply_resolutions",
                "reload_driver",
                "snapshot_log",
                "restart_initial",
                "wait_log",
                "apply_config",
                "restart_final",
            ],
        )
        self.assertEqual(len(sleeps), 10)  # 重载后固定等待 2.0s，每次 0.2s
        self.assertEqual(plans[0].output_name, VDD_ID)
        self.assertEqual(plans[0].resolution_mode, "manual")
        self.assertEqual(plans[0].manual_resolution, (2360, 1640))
        self.assertEqual(
            summary.pairing_url,
            "https://localhost:47990/pin",
        )
        by_key = {outcome.key: outcome for outcome in outcomes}
        self.assertTrue(by_key["service_initial"].ok)
        self.assertTrue(by_key["device_id"].ok)
        self.assertTrue(by_key["sunshine_config"].ok)
        self.assertTrue(by_key["service"].ok)

    def test_falls_back_to_existing_output_name(self) -> None:
        _calls, _sleeps, plans, summary = self._run(
            found_id=None,
            existing_id=EXISTING_ID,
        )

        self.assertEqual(plans[0].output_name, EXISTING_ID)
        by_key = {outcome.key: outcome for outcome in summary.outcomes}
        self.assertTrue(by_key["device_id"].ok)
        self.assertIn("沿用现有 output_name", by_key["device_id"].message)


if __name__ == "__main__":
    unittest.main()
