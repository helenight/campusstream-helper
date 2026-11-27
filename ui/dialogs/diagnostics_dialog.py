"""「排障诊断」对话框：后台采集、脱敏预览、复制与导出 Markdown。"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import QThread, QTimer
from PySide6.QtGui import QCloseEvent, QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from core.common import CommandRunner, default_state_dir
from core.diagnostics import DIAGNOSTIC_FILENAME, DiagnosticsReport
from ui.theme import ThemeManager
from ui.widgets import (
    Banner,
    Card,
    StatusPill,
    heading,
    make_button,
    muted_label,
    repolish,
)
from ui.workers import DiagnosticsWorker


class DiagnosticsDialog(QDialog):
    """只读采集本机诊断信息，并允许用户复制或导出脱敏报告。"""

    def __init__(
        self,
        parent: QWidget | None,
        *,
        dry_run: bool = False,
        state_dir: Path | None = None,
        collector: Callable[..., DiagnosticsReport] | None = None,
        runner_factory: Callable[[bool], CommandRunner] | None = None,
        probe_timeout_s: float = 1.0,
        theme: ThemeManager | None = None,
    ) -> None:
        super().__init__(parent)
        _ = theme  # 对话框跟随全局 QSS；参数保留给统一构造习惯。
        self._dry_run = dry_run
        self._state_dir = (
            Path(state_dir) if state_dir is not None else default_state_dir()
        )
        self._collector = collector
        self._runner_factory = runner_factory
        self._probe_timeout_s = probe_timeout_s

        self._thread: QThread | None = None
        self._worker: DiagnosticsWorker | None = None
        self._running = False
        self._started = False
        self._report: DiagnosticsReport | None = None
        self._reset_copy_timer = QTimer(self)
        self._reset_copy_timer.setSingleShot(True)
        self._reset_copy_timer.timeout.connect(
            lambda: self.copy_button.setText("复制报告")
        )

        self.setWindowTitle("排障诊断 · CampusStream Helper")
        self.setMinimumSize(860, 680)
        self.resize(940, 760)
        self.setModal(True)
        self._build_ui()

    # ------------------------------------------------------------------
    # 外部属性
    # ------------------------------------------------------------------

    @property
    def report(self) -> DiagnosticsReport | None:
        return self._report

    @property
    def is_running(self) -> bool:
        return self._running

    # ------------------------------------------------------------------
    # 构建
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(14)

        header = QHBoxLayout()
        header_text = QVBoxLayout()
        header_text.setSpacing(2)
        title = QLabel("排障诊断")
        title.setObjectName("PageTitle")
        subtitle = QLabel(
            "只读收集系统代理、显示/显卡、Sunshine、VDD、网络与运行日志，"
            "生成可分享的 Markdown 报告。"
        )
        subtitle.setObjectName("PageSubtitle")
        subtitle.setWordWrap(True)
        header_text.addWidget(title)
        header_text.addWidget(subtitle)
        header.addLayout(header_text, 1)
        self.status_pill = StatusPill("准备中", "muted")
        header.addWidget(self.status_pill)
        root.addLayout(header)

        self.stack = QStackedWidget()
        self.progress_page = self._build_progress_page()
        self.result_page = self._build_result_page()
        self.stack.addWidget(self.progress_page)
        self.stack.addWidget(self.result_page)
        root.addWidget(self.stack, 1)

    def _build_progress_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        info = Banner(
            "诊断只读取本机信息，不会修改系统、不会自动上传。"
            "报告可能包含局域网 IP 与安装路径，默认会自动脱敏。",
            tone="info",
            title="只读检测",
        )
        layout.addWidget(info)

        card = Card()
        row = QHBoxLayout()
        row.addWidget(heading("正在收集诊断信息"))
        row.addStretch()
        self.progress_pill = StatusPill("采集中", "info")
        row.addWidget(self.progress_pill)
        card.add_layout(row)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(10)
        card.add(self.progress_bar)

        detail_row = QHBoxLayout()
        self.current_label = muted_label("准备后台采集任务…")
        detail_row.addWidget(self.current_label, 1)
        self.percent_label = QLabel("0%")
        self.percent_label.setObjectName("Muted")
        detail_row.addWidget(self.percent_label)
        card.add_layout(detail_row)
        layout.addWidget(card)

        log_card = Card("采集日志")
        self.log_view = QPlainTextEdit()
        self.log_view.setObjectName("LogView")
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(3000)
        self.log_view.setMinimumHeight(360)
        log_card.add(self.log_view)
        layout.addWidget(log_card, 1)

        hint = muted_label("采集在后台线程执行，窗口会保持响应。完成后可预览、复制或导出。")
        layout.addWidget(hint)
        return page

    def _build_result_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        self.result_banner = Banner("", tone="info")
        self.result_banner.setVisible(False)
        layout.addWidget(self.result_banner)

        redact_card = Card("隐私与脱敏")
        redact_row = QHBoxLayout()
        self.redact_check = QCheckBox("自动脱敏（推荐）")
        self.redact_check.setChecked(True)
        self.redact_check.toggled.connect(self._on_redact_toggled)
        redact_row.addWidget(self.redact_check)
        redact_row.addStretch()
        self.redact_hint = muted_label("已启用自动脱敏")
        redact_row.addWidget(self.redact_hint)
        redact_card.add_layout(redact_row)
        redact_card.add(
            muted_label(
                "开启后会把 C:\\Users\\<用户名> 替换为 C:\\Users\\<USER>，"
                "并隐藏 password / token / PIN 等敏感值。"
                "关闭前请确认报告可以公开分享。"
            )
        )
        layout.addWidget(redact_card)

        preview_card = Card("报告预览（Markdown）")
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.preview.setMinimumHeight(330)
        preview_card.add(self.preview)
        layout.addWidget(preview_card, 1)

        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        self.copy_button = make_button("复制报告", "primary")
        self.export_button = make_button("导出为 diagnostic_report.md", "secondary")
        self.close_button = make_button("关闭", "secondary")
        self.copy_button.clicked.connect(self.copy_report)
        self.export_button.clicked.connect(self.export_report)
        self.close_button.clicked.connect(self.accept)
        buttons.addWidget(self.copy_button)
        buttons.addWidget(self.export_button)
        buttons.addStretch()
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)
        return page

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        if not self._started:
            self.start()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._running:
            self.current_label.setText("采集仍在进行，请稍候再关闭…")
            event.ignore()
            return
        event.accept()

    def start(self) -> None:
        """开始采集；显示对话框时自动调用，测试也可直接调用。"""
        if self._running:
            return
        self._started = True
        self._running = True
        self._report = None
        self.stack.setCurrentWidget(self.progress_page)
        self.status_pill.set_status("采集中", "info")
        self.progress_pill.set_status("采集中", "info")
        self.progress_bar.setValue(0)
        self.progress_bar.setProperty("result", "")
        repolish(self.progress_bar)
        self.percent_label.setText("0%")
        self.current_label.setText("准备后台采集任务…")
        self.log_view.clear()
        self._append_log("[开始] 只读诊断采集已启动")

        thread = QThread(self)
        worker = DiagnosticsWorker(
            dry_run=self._dry_run,
            state_dir=self._state_dir,
            probe_timeout_s=self._probe_timeout_s,
            collector=self._collector,
            runner_factory=self._runner_factory,
        )
        self._thread = thread
        self._worker = worker
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_progress)
        worker.log.connect(self._append_log)
        worker.finished.connect(self._on_finished)
        worker.failed.connect(self._on_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._on_thread_finished)
        thread.start()

    # ------------------------------------------------------------------
    # 回调
    # ------------------------------------------------------------------

    def _on_progress(self, percent: int, message: str) -> None:
        value = max(0, min(100, int(percent)))
        self.progress_bar.setValue(value)
        self.percent_label.setText(f"{value}%")
        self.current_label.setText(message)

    def _append_log(self, message: str) -> None:
        self.log_view.appendPlainText(message)

    def _on_finished(self, report: DiagnosticsReport) -> None:
        self._running = False
        self._report = report
        self.stack.setCurrentWidget(self.result_page)
        self.close_button.setEnabled(True)
        self.redact_check.setChecked(True)

        failed = report.failed_sections
        if failed:
            self.status_pill.set_status("部分失败", "warning")
            self.result_banner.set_text(
                f"共生成 {len(report.sections)} 个板块，其中 {len(failed)} 个采集失败。"
                "失败原因已写入对应板块，可继续复制或导出报告。",
                tone="warning",
                title="诊断已完成，但存在失败板块",
            )
        else:
            self.status_pill.set_status("已完成", "success")
            self.result_banner.set_text(
                f"已生成 {len(report.sections)} 个板块的完整诊断报告。"
                "默认已脱敏，可直接复制或导出。",
                tone="success",
                title="诊断完成",
            )
        self.result_banner.setVisible(True)
        self._render_preview()
        self._append_log("[完成] 诊断报告已生成")

    def _on_failed(self, message: str) -> None:
        self._running = False
        self._report = None
        self.stack.setCurrentWidget(self.result_page)
        self.close_button.setEnabled(True)
        self.status_pill.set_status("失败", "danger")
        self.result_banner.set_text(
            f"诊断采集失败：{message}",
            tone="danger",
            title="无法生成报告",
        )
        self.result_banner.setVisible(True)
        self.preview.setPlainText(
            f"诊断采集失败：{message}\n\n"
            "请关闭窗口后重试；若反复失败，请查看 "
            f"{self._state_dir / 'crash.log'}。"
        )
        self.copy_button.setEnabled(False)
        self.export_button.setEnabled(False)
        self._append_log(f"[失败] {message}")

    def _on_thread_finished(self) -> None:
        self._thread = None
        self._worker = None
        if self._running:
            self._running = False
            self._on_failed("诊断线程意外结束")

    # ------------------------------------------------------------------
    # 脱敏 / 复制 / 导出
    # ------------------------------------------------------------------

    def _on_redact_toggled(self, enabled: bool) -> None:
        self.redact_hint.setText("已启用自动脱敏" if enabled else "⚠ 未脱敏")
        self._render_preview()

    def _render_preview(self) -> None:
        if self._report is None:
            return
        self.preview.setPlainText(
            self._report.to_markdown(redact=self.redact_check.isChecked())
        )
        self.copy_button.setEnabled(True)
        self.export_button.setEnabled(True)

    def copy_report(self) -> None:
        text = self.preview.toPlainText()
        if not text:
            return
        QGuiApplication.clipboard().setText(text)
        self.copy_button.setText("已复制")
        self._reset_copy_timer.start(1500)

    @staticmethod
    def default_export_path() -> Path:
        desktop = Path.home() / "Desktop"
        base = desktop if desktop.is_dir() else Path.home()
        return base / DIAGNOSTIC_FILENAME

    def export_report(self) -> None:
        text = self.preview.toPlainText()
        if not text:
            return
        path_text, _filter = QFileDialog.getSaveFileName(
            self,
            "导出诊断报告",
            str(self.default_export_path()),
            "Markdown (*.md);;所有文件 (*.*)",
        )
        if not path_text:
            return
        path = Path(path_text)
        if not path.suffix:
            path = path.with_suffix(".md")
        try:
            path.write_text(text, encoding="utf-8")
        except OSError as exc:
            QMessageBox.warning(self, "导出失败", f"无法写入文件：{exc}")
            return
        QMessageBox.information(
            self,
            "导出成功",
            f"诊断报告已保存到：\n{path}\n\n"
            "分享前请再次确认报告中的个人信息与网络地址。",
        )
