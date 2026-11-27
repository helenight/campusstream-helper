"""「一键还原与卸载」对话框离屏测试。"""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication, QMessageBox

    from core.uninstaller import UninstallReport
    from tests.fakes import FakeRunner
    from ui.dialogs import UninstallDialog
except ImportError:  # PySide6 未安装时跳过
    QApplication = None  # type: ignore[assignment]
    QMessageBox = None  # type: ignore[assignment]
    UninstallDialog = None  # type: ignore[assignment]
    FakeRunner = None  # type: ignore[assignment]
    UninstallReport = None  # type: ignore[assignment]


@unittest.skipIf(QApplication is None, "PySide6 未安装")
class UninstallDialogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.state_dir = self.root / "CampusStreamHelper"
        self.state_dir.mkdir()
        (self.state_dir / "crash.log").write_text("log", encoding="utf-8")
        self.vdd_dir = self.root / "VirtualDisplayDriver"
        self.vdd_dir.mkdir()
        self.dialogs: list[UninstallDialog] = []

    def tearDown(self) -> None:
        for dialog in self.dialogs:
            if dialog._thread is not None and dialog._thread.isRunning():
                dialog._thread.quit()
                dialog._thread.wait(2000)
            dialog.close()
            dialog.deleteLater()
        self.app.processEvents()

    def _dialog(self, *, dry_run: bool = True) -> UninstallDialog:
        dialog = UninstallDialog(
            None,
            dry_run=dry_run,
            state_dir=self.state_dir,
            vdd_install_dir=self.vdd_dir,
            runner_factory=lambda dry: FakeRunner(dry_run=dry),
        )
        self.dialogs.append(dialog)
        return dialog

    def test_defaults_select_everything(self) -> None:
        dialog = self._dialog()
        options = dialog.selected_options()

        self.assertTrue(options.restore_display)
        self.assertTrue(options.restore_power)
        self.assertTrue(options.remove_firewall)
        self.assertTrue(options.uninstall_sunshine)
        self.assertTrue(options.uninstall_vdd)
        self.assertTrue(options.delete_vdd_directory)
        self.assertTrue(options.clean_cache)
        self.assertEqual(len(dialog.selected_labels()), 8)
        self.assertEqual(
            dialog.selected_labels()[:2],
            (
                "恢复显示模式为「仅电脑屏幕」",
                "停止 SunshineService（随卸载 Sunshine 自动执行）",
            ),
        )
        self.assertTrue(dialog.start_button.isEnabled())
        self.assertEqual(dialog.start_button.text(), "开始 Dry-Run 模拟")

    def test_vdd_toggle_controls_subdirectory(self) -> None:
        dialog = self._dialog()
        dialog.vdd_check.setChecked(False)

        self.assertFalse(dialog.vdd_dir_check.isEnabled())
        self.assertFalse(dialog.selected_options().delete_vdd_directory)

        dialog.vdd_check.setChecked(True)
        self.assertTrue(dialog.vdd_dir_check.isEnabled())
        self.assertTrue(dialog.selected_options().delete_vdd_directory)

    def test_warning_when_display_restore_unchecked(self) -> None:
        dialog = self._dialog()
        dialog.display_check.setChecked(False)

        self.assertFalse(dialog.warning_banner.isHidden())
        self.assertIn("黑屏", dialog.warning_banner._text.text())

    def test_start_disabled_when_nothing_selected(self) -> None:
        dialog = self._dialog()
        for checkbox in dialog._option_checkboxes():
            checkbox.setChecked(False)

        self.assertFalse(dialog.start_button.isEnabled())
        self.assertFalse(dialog.selected_options().has_selection)

    def test_confirmation_yes_allows_start(self) -> None:
        dialog = self._dialog()
        with patch.object(
            QMessageBox,
            "exec",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            self.assertTrue(
                dialog._confirm(dialog.selected_options(), dry_run=True)
            )

    def test_confirmation_cancel_blocks_start(self) -> None:
        dialog = self._dialog()
        with patch.object(
            QMessageBox,
            "exec",
            return_value=QMessageBox.StandardButton.Cancel,
        ):
            self.assertFalse(
                dialog._confirm(dialog.selected_options(), dry_run=True)
            )

    def test_worker_runs_and_updates_progress(self) -> None:
        dialog = self._dialog()
        dialog.power_check.setChecked(False)
        dialog.firewall_check.setChecked(False)
        dialog.sunshine_check.setChecked(False)
        dialog.vdd_check.setChecked(False)
        dialog.cache_check.setChecked(True)
        reports: list[UninstallReport] = []
        dialog.report_ready.connect(reports.append)

        with patch.object(dialog, "_confirm", return_value=True):
            dialog.start()

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            self.app.processEvents()
            if reports and dialog._thread is None:
                break
            time.sleep(0.01)

        self.assertTrue(dialog.executed)
        self.assertTrue(dialog.was_dry_run)
        self.assertEqual(len(reports), 1)
        self.assertTrue(reports[0].dry_run)
        self.assertEqual(dialog.progress_bar.value(), 100)
        self.assertEqual(dialog.status_pill.text(), "Dry-Run 完成")
        self.assertTrue(dialog.close_button.isEnabled())
        self.assertTrue(self.state_dir.exists())


if __name__ == "__main__":
    unittest.main()
