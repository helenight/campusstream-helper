"""「排障诊断」对话框离屏测试：脱敏切换、复制、导出与失败态。"""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

    from core.diagnostics import DiagnosticSection, DiagnosticsReport
    from tests.fakes import FakeRunner
    from ui.dialogs import DiagnosticsDialog
except ImportError:  # PySide6 未安装时跳过
    QApplication = None  # type: ignore[assignment]
    QFileDialog = None  # type: ignore[assignment]
    QMessageBox = None  # type: ignore[assignment]
    QGuiApplication = None  # type: ignore[assignment]
    DiagnosticsDialog = None  # type: ignore[assignment]
    DiagnosticSection = None  # type: ignore[assignment]
    DiagnosticsReport = None  # type: ignore[assignment]
    FakeRunner = None  # type: ignore[assignment]


def _fixed_report() -> "DiagnosticsReport":
    return DiagnosticsReport(
        sections=(
            DiagnosticSection(
                "system",
                "系统与代理",
                r"路径：C:\Users\周成儒\AppData\Local\Sunshine"
                + "\npassword = top-secret",
            ),
            DiagnosticSection("network", "网络与防火墙", "IPv4：192.168.1.10"),
        ),
        generated_at=datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc),
        dry_run=True,
        admin=True,
        state_dir=Path(r"C:\Users\周成儒\AppData\Local\CampusStreamHelper"),
    )


@unittest.skipIf(QApplication is None, "PySide6 未安装")
class DiagnosticsDialogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.dialogs: list[DiagnosticsDialog] = []

    def tearDown(self) -> None:
        for dialog in self.dialogs:
            if dialog._thread is not None and dialog._thread.isRunning():
                dialog._thread.quit()
                dialog._thread.wait(2000)
            dialog.close()
            dialog.deleteLater()
        self.app.processEvents()

    def _dialog(self, collector) -> "DiagnosticsDialog":
        dialog = DiagnosticsDialog(
            None,
            dry_run=True,
            state_dir=self.root,
            collector=collector,
            runner_factory=lambda dry: FakeRunner(dry_run=dry),
        )
        self.dialogs.append(dialog)
        return dialog

    def _wait(self, dialog: "DiagnosticsDialog") -> None:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            self.app.processEvents()
            if not dialog.is_running and dialog._thread is None:
                return
            time.sleep(0.01)
        self.fail("诊断对话框后台任务未在 5 秒内结束")

    def test_default_redaction_and_toggle(self) -> None:
        report = _fixed_report()

        def collector(_runner, *, dry_run=False, state_dir=None, progress=None,
                      probe_timeout_s=1.0):
            if progress is not None:
                progress(50, "采集一半")
                progress(100, "完成")
            return report

        dialog = self._dialog(collector)
        dialog.start()
        self._wait(dialog)

        self.assertIs(dialog.report, report)
        self.assertTrue(dialog.redact_check.isChecked())
        self.assertEqual(dialog.progress_bar.value(), 100)
        self.assertIn(r"C:\Users\<USER>", dialog.preview.toPlainText())
        self.assertNotIn("周成儒", dialog.preview.toPlainText())
        self.assertIn("<已脱敏>", dialog.preview.toPlainText())
        self.assertEqual(dialog.status_pill.text(), "已完成")

        dialog.redact_check.setChecked(False)
        self.assertIn("周成儒", dialog.preview.toPlainText())
        self.assertIn("自动脱敏：未开启", dialog.preview.toPlainText())

    def test_copy_report_copies_preview(self) -> None:
        dialog = self._dialog(lambda *args, **kwargs: _fixed_report())
        dialog.start()
        self._wait(dialog)

        dialog.copy_report()

        clipboard = QGuiApplication.clipboard()
        self.assertEqual(clipboard.text(), dialog.preview.toPlainText())
        self.assertEqual(dialog.copy_button.text(), "已复制")

    def test_export_report_writes_markdown(self) -> None:
        dialog = self._dialog(lambda *args, **kwargs: _fixed_report())
        dialog.start()
        self._wait(dialog)

        target = self.root / "diagnostic_report.md"
        with patch.object(
            QFileDialog,
            "getSaveFileName",
            return_value=(str(target), "Markdown (*.md)"),
        ), patch.object(QMessageBox, "information"):
            dialog.export_report()

        self.assertTrue(target.is_file())
        content = target.read_text(encoding="utf-8")
        self.assertIn("# CampusStream Helper 排障诊断报告", content)
        self.assertIn(r"C:\Users\<USER>", content)
        self.assertNotIn("top-secret", content)

    def test_export_adds_markdown_suffix(self) -> None:
        dialog = self._dialog(lambda *args, **kwargs: _fixed_report())
        dialog.start()
        self._wait(dialog)

        target = self.root / "report_without_suffix"
        with patch.object(
            QFileDialog,
            "getSaveFileName",
            return_value=(str(target), "Markdown (*.md)"),
        ), patch.object(QMessageBox, "information"):
            dialog.export_report()

        self.assertTrue(target.with_suffix(".md").is_file())
        self.assertFalse(target.is_file())

    def test_collector_failure_shows_error(self) -> None:
        def broken(*_args, **_kwargs):
            raise RuntimeError("采集坏掉了")

        dialog = self._dialog(broken)
        dialog.start()
        self._wait(dialog)

        self.assertIsNone(dialog.report)
        self.assertEqual(dialog.status_pill.text(), "失败")
        self.assertIn("采集坏掉了", dialog.preview.toPlainText())
        self.assertFalse(dialog.copy_button.isEnabled())
        self.assertFalse(dialog.export_button.isEnabled())
        self.assertTrue(dialog.close_button.isEnabled())

    def test_partial_failure_marks_warning(self) -> None:
        report = DiagnosticsReport(
            sections=(
                DiagnosticSection("system", "系统与代理", "ok"),
                DiagnosticSection("vdd", "VDD", "采集失败：RuntimeError: boom", False),
            ),
            generated_at=datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc),
            dry_run=True,
            admin=False,
        )
        dialog = self._dialog(lambda *args, **kwargs: report)
        dialog.start()
        self._wait(dialog)

        self.assertEqual(dialog.status_pill.text(), "部分失败")
        self.assertIn("采集失败", dialog.preview.toPlainText())
        self.assertIn("## 2. VDD（采集失败）", dialog.preview.toPlainText())
        self.assertTrue(dialog.copy_button.isEnabled())


if __name__ == "__main__":
    unittest.main()
