"""Step 1 界面测试：检测进度、四态徽章、汇总与动作按钮。"""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QSettings, Qt
    from PySide6.QtWidgets import QApplication, QPushButton

    from core.detection import (
        DetectionState,
        admin_item,
        failed_item,
        gpu_item,
        network_item,
        sunshine_item,
        vdd_item,
    )
    from core.gpu import GpuInfo
    from core.network import AdapterInfo, NetworkReport
    from core.power import PowerState
    from core.sunshine import SunshineStatus
    from core.vdd import VddStatus
    from ui.steps.step_environment import EnvironmentStep
    from ui.theme import ThemeManager
    from ui.workers import EnvironmentData
except ImportError:  # PySide6 未安装时跳过
    QApplication = None  # type: ignore[assignment]
    EnvironmentStep = None  # type: ignore[assignment]


def make_sunshine(*, installed: bool = True) -> SunshineStatus:
    return SunshineStatus(
        installed=installed,
        install_dir=Path(r"C:\Program Files\Sunshine") if installed else None,
        executable=(
            Path(r"C:\Program Files\Sunshine\sunshine.exe") if installed else None
        ),
        config_path=(
            Path(r"C:\Program Files\Sunshine\config\sunshine.conf")
            if installed
            else None
        ),
        service_name="SunshineService",
        service_state="running" if installed else "not_installed",  # type: ignore[arg-type]
        version="2026.914" if installed else None,
        detail="test",
    )


def make_vdd(*, installed: bool = True) -> VddStatus:
    return VddStatus(
        installed=installed,
        install_dir=Path(r"C:\VirtualDisplayDriver") if installed else None,
        settings_path=(
            Path(r"C:\VirtualDisplayDriver\vdd_settings.xml") if installed else None
        ),
        driver_version="15.6.5.199" if installed else None,
        schema="resolutions" if installed else "unknown",
        configured_monitor_count=1 if installed else 0,
        configured_resolution_count=6 if installed else 0,
        detail="test",
    )


def make_network() -> NetworkReport:
    return NetworkReport(
        adapters=(
            AdapterInfo(
                name="以太网",
                description="Intel Ethernet",
                kind="ethernet",
                is_up=True,
                ipv4="10.3.127.190",
                global_ipv6=("2408:8207::1",),
                ipv6_binding_enabled=True,
            ),
        ),
        scenario="ethernet_ready",
        preferred_ipv4="10.3.127.190",
        preferred_ipv6=None,
        firewall_rule_present=True,
        warnings=(),
        generated_at="2026-11-26T21:00:00+08:00",
    )


def make_gpu() -> GpuInfo:
    return GpuInfo(
        name="NVIDIA RTX 4060",
        driver_version="32.0.15",
        status="OK",
        is_basic_adapter=False,
        detail="test",
    )


def make_power() -> PowerState:
    return PowerState(
        scheme_guid="{test}",
        scheme_name="平衡",
        ac_sleep_seconds=0,
        ac_display_off_seconds=600,
        dc_sleep_seconds=900,
        dc_display_off_seconds=300,
    )


def ready_items() -> tuple:
    return (
        admin_item(True, make_power()),
        gpu_item(make_gpu()),
        sunshine_item(make_sunshine()),
        vdd_item(make_vdd()),
        network_item(make_network()),
    )


@unittest.skipIf(QApplication is None, "PySide6 未安装")
class EnvironmentStepTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        settings = QSettings(
            str(Path(self._tmp.name) / "settings.ini"),
            QSettings.Format.IniFormat,
        )
        self.theme = ThemeManager(self.app, mode="light", settings=settings)
        self.step = EnvironmentStep()
        self.step.set_mode(True)

    def tearDown(self) -> None:
        self.step.deleteLater()
        self.app.processEvents()

    def _finish(self, items, **kwargs) -> None:
        data = EnvironmentData(
            network=kwargs.get("network"),
            sunshine=kwargs.get("sunshine"),
            vdd=kwargs.get("vdd"),
            gpu=kwargs.get("gpu"),
            power=kwargs.get("power"),
            items=tuple(items),
            finished_at=kwargs.get("finished_at", datetime(2026, 11, 26, 21, 35, 10)),
        )
        self.step.start_detection()
        self.step.finish(data)

    def test_start_detection_sets_indeterminate_state(self) -> None:
        self.step.start_detection()
        self.assertTrue(self.step.is_detecting)
        self.assertEqual(self.step.progress.minimum(), 0)
        self.assertEqual(self.step.progress.maximum(), 0)
        self.assertEqual(self.step.refresh_button.text(), "检测中...")
        self.assertFalse(self.step.refresh_button.isEnabled())
        self.assertIn("已完成 0/5 项", self.step.progress_text.text())

    def test_progress_counts_completed_items(self) -> None:
        self.step.start_detection()
        item = admin_item(True, make_power())
        self.step.apply_item(item)
        self.step.set_progress(1, 5)
        self.assertIn("已完成 1/5 项", self.step.progress_text.text())
        self.assertIn("已就绪", self.step._cards["admin"].badge.text())

    def test_finish_all_ready(self) -> None:
        items = ready_items()
        self._finish(items, network=make_network())
        self.assertFalse(self.step.is_detecting)
        self.assertIn("环境全部就绪", self.step.summary_title.text())
        self.assertEqual(self.step.progress.maximum(), 5)
        self.assertEqual(self.step.progress.value(), 5)
        self.assertEqual(self.step.progress.property("result"), "success")
        self.assertIn("21:35:10", self.step.timestamp_label.text())
        self.assertTrue(self.step.refresh_button.isEnabled())
        for card in self.step._cards.values():
            self.assertEqual(card.badge.state, "ready")

    def test_finish_with_warning_shows_action(self) -> None:
        items = list(ready_items())
        items[2] = sunshine_item(make_sunshine(installed=False))
        captured: list[str] = []
        self.step.download_requested.connect(captured.append)

        self._finish(items)
        self.assertIn("1 项待处理", self.step.summary_title.text())
        self.assertEqual(self.step.progress.property("result"), "warning")
        card = self.step._cards["sunshine"]
        self.assertEqual(card.badge.state, "warning")
        self.assertIn("未安装 / 需处理", card.badge.text())
        buttons = card.action_container.findChildren(QPushButton)
        self.assertEqual(len(buttons), 1)
        buttons[0].click()
        self.assertEqual(captured, ["sunshine"])

    def test_finish_with_failure_shows_reason(self) -> None:
        items = list(ready_items())
        items[1] = failed_item("gpu", "WMI 查询失败")
        self._finish(items)
        self.assertIn("1 项失败", self.step.summary_title.text())
        self.assertEqual(self.step.progress.property("result"), "failed")
        card = self.step._cards["gpu"]
        self.assertEqual(card.badge.state, "failed")
        self.assertEqual(card._rows[0]._value.text(), "WMI 查询失败")

    def test_network_action_emits_adapter_names(self) -> None:
        report = NetworkReport(
            adapters=(
                AdapterInfo(
                    name="以太网",
                    description="Intel Ethernet",
                    kind="ethernet",
                    is_up=True,
                    ipv4="10.3.127.190",
                    global_ipv6=(),
                    ipv6_binding_enabled=False,
                ),
            ),
            scenario="ethernet_ready",
            preferred_ipv4="10.3.127.190",
            preferred_ipv6=None,
            firewall_rule_present=True,
            warnings=(),
            generated_at="2026-11-26T21:00:00+08:00",
        )
        items = list(ready_items())
        items[4] = network_item(report)
        captured: list[list[str]] = []
        self.step.fix_ipv6_requested.connect(captured.append)

        self._finish(items)
        card = self.step._cards["network"]
        buttons = card.action_container.findChildren(QPushButton)
        self.assertEqual(len(buttons), 1)
        buttons[0].click()
        self.assertEqual(captured, [["以太网"]])


if __name__ == "__main__":
    unittest.main()
