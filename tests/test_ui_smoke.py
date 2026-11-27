"""离屏 UI 冒烟测试：不弹窗、不启动真实后台任务。"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from ui.workers import EnvironmentData
except ImportError:  # 系统 Python 未安装 PySide6 时跳过
    Qt = None
    QApplication = None
    EnvironmentData = None  # type: ignore[assignment]

from core.network import NetworkReport
from core.plan import SetupPlan, SetupSummary, StepOutcome
from core.sunshine import SunshineStatus
from core.vdd import MonitorSpec, VddStatus


def _environment() -> EnvironmentData:
    network = NetworkReport(
        adapters=(),
        scenario="ethernet_ready",
        preferred_ipv4="192.168.1.10",
        preferred_ipv6=None,
        firewall_rule_present=True,
        warnings=(),
        generated_at="2026-11-26T00:00:00+08:00",
    )
    sunshine = SunshineStatus(
        installed=True,
        install_dir=Path(r"C:\Program Files\Sunshine"),
        executable=Path(r"C:\Program Files\Sunshine\sunshine.exe"),
        config_path=Path(r"C:\Program Files\Sunshine\config\sunshine.conf"),
        service_name="SunshineService",
        service_state="running",
        version="test",
        detail="test",
    )
    vdd = VddStatus(
        installed=True,
        install_dir=Path(r"C:\VirtualDisplayDriver"),
        settings_path=Path(r"C:\VirtualDisplayDriver\vdd_settings.xml"),
        driver_version=None,
        schema="resolutions",
        configured_monitor_count=1,
        configured_resolution_count=2,
        detail="test",
    )
    return EnvironmentData(network=network, sunshine=sunshine, vdd=vdd, power=None)


def _plan() -> SetupPlan:
    return SetupPlan(
        platforms=("ipados",),
        monitors=(
            MonitorSpec(2360, 1640, 60, "iPad Air 11", "preset", 150),
        ),
        dry_run=True,
    )


@unittest.skipIf(QApplication is None, "PySide6 未安装")
class UiSmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        from PySide6.QtCore import QSettings

        from ui.theme import ThemeManager
        from ui.wizard import WizardWindow

        self._settings_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._settings_dir.cleanup)
        settings = QSettings(
            str(Path(self._settings_dir.name) / "settings.ini"),
            QSettings.Format.IniFormat,
        )
        self.theme = ThemeManager(self.app, mode="light", settings=settings)
        self.window = WizardWindow(
            force_dry_run=True,
            auto_detect=False,
            theme=self.theme,
        )

    def tearDown(self) -> None:
        self.window.close()
        self.app.processEvents()

    def test_window_structure(self) -> None:
        self.assertEqual(self.window.step_count, 4)
        self.assertEqual(self.window.current_step, 0)
        self.assertEqual(self.window.stack.count(), 4)
        self.assertIsNotNone(self.window.theme_switcher)
        self.assertEqual(
            self.window.uninstall_button.text(),
            "一键还原与卸载",
        )
        self.assertTrue(self.window.uninstall_button.isEnabled())

    def test_theme_switch_updates_stylesheet(self) -> None:
        from ui.theme import DARK_QSS, LIGHT_QSS

        self.assertEqual(self.app.styleSheet(), LIGHT_QSS)
        self.window.theme_switcher._actions["dark"].trigger()
        self.assertEqual(self.theme.mode, "dark")
        self.assertEqual(self.app.styleSheet(), DARK_QSS)
        self.assertIn("深色", self.window.theme_switcher.text())

    def test_detection_disables_navigation(self) -> None:
        self.window._detecting = True
        self.window._update_nav()
        self.assertFalse(self.window.next_button.isEnabled())
        self.assertIn("检测中", self.window.next_button.text())

        self.window._detecting = False
        self.window._update_nav()
        self.assertTrue(self.window.next_button.isEnabled())

    def test_device_selection_builds_plan(self) -> None:
        devices = self.window.devices_step
        item, preset = devices._items[0]
        item.setCheckState(Qt.CheckState.Checked)
        monitors = devices.selected_monitors()
        self.assertEqual(len(monitors), 1)
        self.assertEqual(monitors[0].width, preset.width)
        plan = devices.build_plan(dry_run=True)
        self.assertTrue(plan.dry_run)
        self.assertEqual(plan.platforms, ("ipados",))
        self.assertEqual(len(plan.monitors), 1)

    def test_dry_run_toggle_updates_pending_plan(self) -> None:
        self.window.env_data = _environment()
        self.window._go_to(1)
        devices = self.window.devices_step
        item, _preset = devices._items[0]
        item.setCheckState(Qt.CheckState.Checked)
        self.window.pending_plan = devices.build_plan(dry_run=True)
        self.window._go_to(2)
        self.assertEqual(self.window.execute_step.start_button.text(), "开始模拟运行")

        self.window.dry_run_check.setChecked(False)

        self.assertFalse(self.window.pending_plan.dry_run)
        self.assertFalse(self.window.execute_step._plan.dry_run)
        self.assertEqual(self.window.execute_step.start_button.text(), "开始配置")

    def test_download_action_opens_package_dialog(self) -> None:
        with patch("ui.wizard.is_admin", return_value=True), patch(
            "ui.wizard.PackageInstallDialog"
        ) as dialog_class:
            self.window.environment_step.download_requested.emit("sunshine")

        dialog_class.assert_called_once()
        args, kwargs = dialog_class.call_args
        self.assertEqual(args[1], "sunshine")
        self.assertTrue(kwargs["auto_start"])
        self.assertIs(self.window._package_dialog, None)

    def test_navigation_to_guide(self) -> None:
        self.window.env_data = _environment()
        self.window._go_to(1)
        self.assertEqual(self.window.current_step, 1)

        devices = self.window.devices_step
        item, _preset = devices._items[0]
        item.setCheckState(Qt.CheckState.Checked)
        plan = devices.build_plan(dry_run=True)
        self.window.pending_plan = plan
        self.window._go_to(2)
        self.assertEqual(self.window.current_step, 2)
        self.window.execute_step.set_plan(plan)
        self.assertIn("模拟", self.window.execute_step.start_button.text())

        summary = SetupSummary(
            plan=plan,
            outcomes=(
                StepOutcome("vdd", "虚拟显示器分辨率", True, "ok", simulated=True),
                StepOutcome("device_id", "提取虚拟屏 ID", True, "{test}", simulated=True),
                StepOutcome("sunshine_config", "写入 sunshine.conf", True, "ok", simulated=True),
                StepOutcome("service", "重启服务", True, "ok", simulated=True),
            ),
            network=_environment().network,
            sunshine=_environment().sunshine,
            vdd=_environment().vdd,
            device_id="{test}",
            recommended_ip="192.168.1.10",
            pairing_url="https://localhost:47990/pin",
            rollback_available=False,
        )
        self.window.summary = summary
        self.window._go_to(3)
        self.assertEqual(self.window.current_step, 3)
        self.assertEqual(self.window.guide_step.ip_label.text(), "192.168.1.10")
        self.assertEqual(
            self.window.guide_step.pairing_label.text(),
            "https://localhost:47990/pin",
        )
        platform_text = self.window.guide_step.platform_tips.toPlainText()
        self.assertIn("Settings", platform_text)
        self.assertIn("Smoothest Video", platform_text)
        self.assertIn("Optimize Game Settings", platform_text)
        self.assertIn("台前调度", platform_text)
        self.assertTrue(self.window.guide_step.artemis_button.isHidden())
        general_text = self.window.guide_step.general_tips.toPlainText()
        self.assertIn("优化游戏设置", general_text)
        self.assertIn("150%", general_text)

    def test_local_pairing_page_is_available_without_lan_ip(self) -> None:
        summary = SetupSummary(
            plan=_plan(),
            outcomes=(),
            network=_environment().network,
            sunshine=_environment().sunshine,
            vdd=_environment().vdd,
            device_id=None,
            recommended_ip=None,
            pairing_url="https://localhost:47990/pin",
            rollback_available=False,
        )
        self.window.guide_step.set_summary(summary)
        self.assertEqual(
            self.window.guide_step.pairing_label.text(),
            "https://localhost:47990/pin",
        )
        self.assertTrue(self.window.guide_step.pair_button.isEnabled())
        self.assertFalse(self.window.guide_step.copy_button.isEnabled())

    def test_android_guide_uses_chinese_menu_names(self) -> None:
        plan = SetupPlan(
            platforms=("android_tablet",),
            monitors=(
                MonitorSpec(1920, 1200, 60, "Android 平板", "preset", 150),
            ),
            resolution_mode="manual",
            dry_run=True,
        )
        summary = SetupSummary(
            plan=plan,
            outcomes=(),
            network=_environment().network,
            sunshine=_environment().sunshine,
            vdd=_environment().vdd,
            device_id="{test}",
            recommended_ip="192.168.1.10",
            pairing_url="https://localhost:47990/pin",
            rollback_available=False,
        )
        self.window.guide_step.set_summary(summary)
        platform_text = self.window.guide_step.platform_tips.toPlainText()
        for text in (
            "视频分辨率",
            "本地（你的屏幕分辨率，例如 1920×1200）",
            "原生",
            "60 FPS",
            "首选 HEVC",
            "优先使用 HEVC",
            "视频帧速调节",
            "优先视频流畅度",
            "优化游戏设置",
            "将触控屏作为触控板使用",
            "移动端客户端推荐（双选项）",
            "Artemis",
            "https://github.com/ClassicOldSong/moonlight-android/releases",
            "自定义非 16:9 分辨率",
            "双指缩放",
            "悬浮控制球",
        ):
            self.assertIn(text, platform_text)
        self.assertFalse(self.window.guide_step.artemis_button.isHidden())
        error_text = self.window.guide_step.error_tips.toPlainText()
        self.assertIn("error -5", error_text)
        self.assertIn("将画面拉伸至全屏", error_text)
        self.assertIn("拉伸视频以铺满全屏", error_text)


if __name__ == "__main__":
    unittest.main()
