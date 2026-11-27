"""「一键还原与卸载」勾选、二次确认与后台进度对话框。"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
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
from core.downloader import DEFAULT_VDD_DIR
from core.privileges import is_admin
from core.uninstaller import (
    UninstallOptions,
    UninstallReport,
    UninstallState,
)
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
from ui.workers import UninstallWorker

_STEP_ICONS = {
    UninstallState.SUCCESS: "✅",
    UninstallState.WARNING: "⚠️",
    UninstallState.SKIPPED: "⏭️",
    UninstallState.FAILED: "❌",
    UninstallState.SIMULATED: "🧪",
}


class UninstallDialog(QDialog):
    """勾选清理项目，确认后在后台执行并展示实时结果。"""

    report_ready = Signal(object)  # UninstallReport

    def __init__(
        self,
        parent: QWidget | None,
        *,
        dry_run: bool = False,
        state_dir: Path | None = None,
        vdd_install_dir: Path | None = None,
        runner_factory: Callable[[bool], CommandRunner] | None = None,
        theme: ThemeManager | None = None,
    ) -> None:
        super().__init__(parent)
        _ = theme  # 对话框跟随全局 QSS；参数保留给统一构造习惯。
        self._global_dry_run = dry_run
        self._state_dir = (
            Path(state_dir) if state_dir is not None else default_state_dir()
        )
        self._vdd_dir = (
            Path(vdd_install_dir)
            if vdd_install_dir is not None
            else DEFAULT_VDD_DIR
        )
        self._runner_factory = runner_factory

        self._thread: QThread | None = None
        self._worker: UninstallWorker | None = None
        self._running = False
        self._executed = False
        self._was_dry_run = dry_run
        self._report: UninstallReport | None = None
        self._finish_check = QTimer(self)
        self._finish_check.setSingleShot(True)
        self._finish_check.timeout.connect(self._check_unexpected_finish)

        self.setWindowTitle("一键还原与卸载 · CampusStream Helper")
        self.setMinimumSize(780, 700)
        self.resize(820, 740)
        self.setModal(True)
        self._build_ui()
        self._refresh_selection_state()

    # ------------------------------------------------------------------
    # 外部属性
    # ------------------------------------------------------------------

    @property
    def executed(self) -> bool:
        return self._executed

    @property
    def was_dry_run(self) -> bool:
        return self._was_dry_run

    @property
    def report(self) -> UninstallReport | None:
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
        self.title_label = QLabel("一键还原与卸载")
        self.title_label.setObjectName("PageTitle")
        subtitle = QLabel(
            "选择要清理的项目；真实卸载不可撤销。执行顺序经过安全排列，"
            "建议首次先使用 Dry-Run 预览。"
        )
        subtitle.setObjectName("PageSubtitle")
        subtitle.setWordWrap(True)
        header_text.addWidget(self.title_label)
        header_text.addWidget(subtitle)
        header.addLayout(header_text, 1)
        self.status_pill = StatusPill("待选择", "muted")
        header.addWidget(self.status_pill, 0, Qt.AlignmentFlag.AlignTop)
        root.addLayout(header)

        self.stack = QStackedWidget()
        self.options_page = self._build_options_page()
        self.progress_page = self._build_progress_page()
        self.stack.addWidget(self.options_page)
        self.stack.addWidget(self.progress_page)
        root.addWidget(self.stack, 1)

    def _build_options_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        intro = Banner(
            "真实清理会停止服务、卸载驱动并删除缓存。请先关闭正在使用远控的客户端。",
            tone="danger",
            title="操作前请注意",
        )
        layout.addWidget(intro)

        card = Card("清理项目（默认全选）")
        self.display_check = QCheckBox("1. 恢复显示模式为「仅电脑屏幕」")
        self._add_option(
            card,
            self.display_check,
            "必须最先执行 DisplaySwitch.exe /internal，避免卸载虚拟屏后黑屏。",
        )
        self.power_check = QCheckBox("2. 还原电源计划")
        self._add_option(
            card,
            self.power_check,
            "读取 power_state.json，恢复原来的睡眠与息屏时间，并删除备份文件。",
        )
        self.firewall_check = QCheckBox("3. 移除防火墙规则")
        self._add_option(
            card,
            self.firewall_check,
            "删除本软件创建的「CampusStream Helper - Sunshine」入站放行规则。",
        )
        self.sunshine_check = QCheckBox("4. 卸载 Sunshine")
        self._add_option(
            card,
            self.sunshine_check,
            "先停止并清理 SunshineService，再通过注册表 MSI ProductCode 静默卸载。",
        )
        self.vdd_check = QCheckBox("5. 卸载 Virtual Display Driver（VDD）")
        self._add_option(
            card,
            self.vdd_check,
            "通过 pnputil 移除虚拟显示器设备；失败时会提示使用 VDD Control 手动完成。",
        )
        self.vdd_dir_check = QCheckBox(
            f"同时删除 VDD 部署目录：{self._vdd_dir}"
        )
        self.vdd_dir_check.setChecked(True)
        sub_row = QWidget()
        sub_layout = QHBoxLayout(sub_row)
        sub_layout.setContentsMargins(26, 0, 0, 4)
        sub_layout.addWidget(self.vdd_dir_check)
        sub_layout.addStretch()
        card.add(sub_row)
        self.cache_check = QCheckBox("6. 清理本软件缓存目录")
        self._add_option(
            card,
            self.cache_check,
            f"删除 {self._state_dir} 下的日志、下载缓存与临时状态文件。",
        )
        layout.addWidget(card)

        self.warning_banner = Banner("", tone="warning")
        self.warning_banner.setVisible(False)
        layout.addWidget(self.warning_banner)

        dry_card = Card("运行模式")
        self.dry_run_check = QCheckBox(
            "Dry-Run 模拟运行（只打印将要执行的命令，不实际删除）"
        )
        self.dry_run_check.setChecked(self._global_dry_run)
        if self._global_dry_run:
            self.dry_run_check.setEnabled(False)
            self.dry_run_check.setToolTip(
                "主界面已开启 Dry-Run，本次清理将强制保持模拟模式。"
            )
        dry_card.add(self.dry_run_check)
        dry_card.add(
            muted_label(
                "关闭 Dry-Run 后会真实停止服务、调用 msiexec / pnputil 并删除目录。"
            )
        )
        layout.addWidget(dry_card)

        for checkbox in self._option_checkboxes():
            checkbox.toggled.connect(self._refresh_selection_state)
        self.dry_run_check.toggled.connect(self._refresh_selection_state)

        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        self.start_button = make_button("开始清理", "danger")
        self.cancel_button = make_button("取消", "secondary")
        self.start_button.clicked.connect(self.start)
        self.cancel_button.clicked.connect(self.reject)
        buttons.addWidget(self.start_button)
        buttons.addWidget(self.cancel_button)
        buttons.addStretch()
        layout.addLayout(buttons)

        layout.addWidget(
            muted_label(
                "固定执行顺序：切回主屏 → 停止 Sunshine 服务 → 卸载 VDD → "
                "卸载 Sunshine → 防火墙 → 电源 → 缓存。"
            )
        )
        return page

    def _build_progress_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        status_card = Card()
        header = QHBoxLayout()
        self.progress_title = heading("清理进度")
        header.addWidget(self.progress_title)
        header.addStretch()
        self.progress_pill = StatusPill("准备中", "info")
        header.addWidget(self.progress_pill)
        status_card.add_layout(header)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(10)
        status_card.add(self.progress_bar)

        row = QHBoxLayout()
        self.current_label = muted_label("准备后台清理任务…")
        row.addWidget(self.current_label, 1)
        self.percent_label = QLabel("0%")
        self.percent_label.setObjectName("Muted")
        row.addWidget(self.percent_label)
        status_card.add_layout(row)

        self.result_banner = Banner("", tone="info")
        self.result_banner.setVisible(False)
        status_card.add(self.result_banner)
        layout.addWidget(status_card)

        log_card = Card("实时结果")
        self.log_view = QPlainTextEdit()
        self.log_view.setObjectName("LogView")
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(5000)
        self.log_view.setMinimumHeight(330)
        log_card.add(self.log_view)
        layout.addWidget(log_card, 1)

        buttons = QHBoxLayout()
        buttons.addStretch()
        self.close_button = make_button("关闭", "secondary")
        self.close_button.setEnabled(False)
        self.close_button.clicked.connect(self.accept)
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)

        layout.addWidget(
            muted_label(
                "清理在后台线程执行；窗口会实时显示每一步结果。"
                "关闭窗口后主界面会自动重新检测环境。"
            )
        )
        return page

    def _add_option(
        self,
        card: Card,
        checkbox: QCheckBox,
        description: str,
    ) -> None:
        checkbox.setChecked(True)
        card.add(checkbox)
        label = muted_label(description)
        label.setContentsMargins(26, 0, 0, 4)
        card.add(label)

    def _option_checkboxes(self) -> tuple[QCheckBox, ...]:
        return (
            self.display_check,
            self.power_check,
            self.firewall_check,
            self.sunshine_check,
            self.vdd_check,
            self.vdd_dir_check,
            self.cache_check,
        )

    # ------------------------------------------------------------------
    # 选择状态 / 确认
    # ------------------------------------------------------------------

    def selected_options(self) -> UninstallOptions:
        vdd = self.vdd_check.isChecked()
        return UninstallOptions(
            restore_display=self.display_check.isChecked(),
            restore_power=self.power_check.isChecked(),
            remove_firewall=self.firewall_check.isChecked(),
            uninstall_sunshine=self.sunshine_check.isChecked(),
            uninstall_vdd=vdd,
            delete_vdd_directory=vdd and self.vdd_dir_check.isChecked(),
            clean_cache=self.cache_check.isChecked(),
            state_dir=self._state_dir,
            vdd_install_dir=self._vdd_dir,
        )

    def selected_labels(self) -> tuple[str, ...]:
        """按实际执行顺序生成二次确认清单。"""
        labels: list[str] = []
        if self.display_check.isChecked():
            labels.append("恢复显示模式为「仅电脑屏幕」")
        if self.sunshine_check.isChecked():
            labels.append("停止 SunshineService（随卸载 Sunshine 自动执行）")
        if self.vdd_check.isChecked():
            labels.append("卸载 Virtual Display Driver")
            if self.vdd_dir_check.isChecked():
                labels.append(f"删除 VDD 目录：{self._vdd_dir}")
        if self.sunshine_check.isChecked():
            labels.append("卸载 Sunshine")
        if self.firewall_check.isChecked():
            labels.append("移除防火墙规则")
        if self.power_check.isChecked():
            labels.append("还原电源计划")
        if self.cache_check.isChecked():
            labels.append("清理本软件缓存目录")
        return tuple(labels)

    def _refresh_selection_state(self, *_args: object) -> None:
        if self._running:
            return
        vdd = self.vdd_check.isChecked()
        self.vdd_dir_check.setEnabled(vdd)
        options = self.selected_options()
        dry_run = self.dry_run_check.isChecked()

        self.start_button.setText(
            "开始 Dry-Run 模拟" if dry_run else "开始清理"
        )
        self.start_button.setEnabled(options.has_selection)

        warnings = options.warnings()
        if warnings:
            self.warning_banner.set_text(
                "\n".join(f"• {warning}" for warning in warnings),
                tone="warning",
                title="安全提醒",
            )
            self.warning_banner.setVisible(True)
        else:
            self.warning_banner.setVisible(False)

    def start(self) -> None:
        if self._running:
            return
        options = self.selected_options()
        if not options.has_selection:
            QMessageBox.warning(self, "未选择项目", "请至少勾选一项需要清理的内容。")
            return

        dry_run = self.dry_run_check.isChecked()
        if not dry_run and not is_admin():
            QMessageBox.warning(
                self,
                "需要管理员权限",
                "真实卸载需要管理员权限。请关闭窗口后以管理员身份重启，"
                "或勾选 Dry-Run 先预览清理命令。",
            )
            return
        if not self._confirm(options, dry_run):
            return

        self._executed = True
        self._was_dry_run = dry_run
        self._running = True
        self.stack.setCurrentWidget(self.progress_page)
        self.status_pill.set_status(
            "Dry-Run 运行中" if dry_run else "清理中",
            "info",
        )
        self.progress_pill.set_status(
            "Dry-Run 运行中" if dry_run else "清理中",
            "info",
        )
        self.progress_title.setText("正在清理")
        self.progress_bar.setValue(0)
        self.progress_bar.setProperty("result", "")
        repolish(self.progress_bar)
        self.percent_label.setText("0%")
        self.current_label.setText("准备后台清理任务…")
        self.result_banner.setVisible(False)
        self.log_view.clear()
        self.close_button.setEnabled(False)
        self._append_log(
            "[开始] Dry-Run 模拟清理"
            if dry_run
            else "[开始] 开始真实清理与卸载"
        )
        self._spawn_worker(options, dry_run)

    def _confirm(self, options: UninstallOptions, dry_run: bool) -> bool:
        mode = (
            "Dry-Run 模拟（只记录命令，不会修改系统）"
            if dry_run
            else "真实执行（不可撤销）"
        )
        lines: list[str] = [f"模式：{mode}", "", "将执行："]
        lines.extend(f"• {label}" for label in self.selected_labels())
        lines.extend(
            [
                "",
                "固定顺序：切回主屏 → 停止 Sunshine 服务 → 卸载 VDD → "
                "卸载 Sunshine → 防火墙 → 电源 → 缓存。",
            ]
        )
        warnings = options.warnings()
        if warnings:
            lines.extend(["", "安全提醒："])
            lines.extend(f"⚠ {warning}" for warning in warnings)

        box = QMessageBox(self)
        box.setIcon(
            QMessageBox.Icon.Information
            if dry_run
            else QMessageBox.Icon.Warning
        )
        box.setWindowTitle("确认 Dry-Run 模拟" if dry_run else "确认一键还原与卸载")
        box.setText(
            "只打印将要执行的清理命令，不会修改系统。"
            if dry_run
            else "此操作会清理所选系统配置，且不可撤销。"
        )
        box.setInformativeText("\n".join(lines))
        box.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel
        )
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        yes = box.button(QMessageBox.StandardButton.Yes)
        if yes is not None:
            yes.setText("继续 Dry-Run" if dry_run else "确认清理")
        return box.exec() == QMessageBox.StandardButton.Yes

    # ------------------------------------------------------------------
    # 后台任务
    # ------------------------------------------------------------------

    def _spawn_worker(self, options: UninstallOptions, dry_run: bool) -> None:
        worker = UninstallWorker(
            options,
            dry_run=dry_run,
            runner_factory=self._runner_factory,
        )
        thread = QThread(self)
        self._worker = worker
        self._thread = thread
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_progress)
        worker.step.connect(self._on_step)
        worker.log.connect(self._append_log)
        worker.finished.connect(self._on_finished)
        worker.failed.connect(self._on_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._on_thread_finished)
        thread.start()

    def _append_log(self, message: str) -> None:
        self.log_view.appendPlainText(message)
        bar = self.log_view.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _on_progress(self, percent: int, message: str) -> None:
        value = max(0, min(100, int(percent)))
        self.progress_bar.setValue(value)
        self.percent_label.setText(f"{value}%")
        if message:
            self.current_label.setText(message)

    def _on_step(self, step: object) -> None:
        icon = _STEP_ICONS.get(getattr(step, "state", ""), "•")
        label = getattr(step, "label", "清理步骤")
        message = getattr(step, "message", "")
        self._append_log(f"{icon} {label}：{message}")
        details = getattr(step, "details", ())
        for detail in details:
            if detail:
                self._append_log(f"    {detail}")

    def _on_finished(self, report: UninstallReport) -> None:
        self._running = False
        self._report = report
        self.progress_bar.setValue(100)
        self.percent_label.setText("100%")
        self.current_label.setText("清理流程结束")
        if report.failed_steps:
            tone = "danger"
            title = "清理完成但有失败项"
            pill = "完成（有失败）"
            progress_result = "failed"
        elif report.warning_steps:
            tone = "warning"
            title = "清理完成，请查看提醒"
            pill = "完成（需留意）"
            progress_result = "warning"
        elif report.dry_run:
            tone = "info"
            title = "Dry-Run 完成"
            pill = "Dry-Run 完成"
            progress_result = "success"
        else:
            tone = "success"
            title = "清理完成"
            pill = "清理完成"
            progress_result = "success"

        self.status_pill.set_status(pill, tone)
        self.progress_pill.set_status(pill, tone)
        self.progress_title.setText("清理结果")
        self.progress_bar.setProperty("result", progress_result)
        repolish(self.progress_bar)
        self.result_banner.set_text(
            report.summary,
            tone=tone,
            title=title,
        )
        self.result_banner.setVisible(True)
        self.report_ready.emit(report)

    def _on_failed(self, message: str) -> None:
        self._running = False
        self.status_pill.set_status("任务失败", "danger")
        self.progress_pill.set_status("任务失败", "danger")
        self.progress_bar.setProperty("result", "failed")
        repolish(self.progress_bar)
        self.current_label.setText("后台清理任务异常结束")
        self.result_banner.set_text(
            message,
            tone="danger",
            title="清理任务失败",
        )
        self.result_banner.setVisible(True)
        self._append_log(f"[错误] {message}")

    def _on_thread_finished(self) -> None:
        self._thread = None
        self._worker = None
        if not self._running:
            self.close_button.setEnabled(True)
            self.close_button.setFocus()
            return
        # ``finished`` / ``failed`` 是跨线程队列信号；稍后检查，
        # 避免正常的完成信号尚未送达时被误判为线程意外结束。
        self._finish_check.start(100)

    def _check_unexpected_finish(self) -> None:
        if self._running:
            self._on_failed("后台清理线程意外结束，请重新打开窗口重试。")
        self.close_button.setEnabled(True)
        self.close_button.setFocus()

    # ------------------------------------------------------------------
    # 关闭
    # ------------------------------------------------------------------

    def _close_blocked(self) -> bool:
        if self._running or (
            self._thread is not None and self._thread.isRunning()
        ):
            QMessageBox.information(
                self,
                "清理进行中",
                "后台清理任务仍在执行，请等待完成后再关闭窗口。",
            )
            return True
        return False

    def accept(self) -> None:
        if self._close_blocked():
            return
        super().accept()

    def reject(self) -> None:
        if self._close_blocked():
            return
        super().reject()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._close_blocked():
            event.ignore()
            return
        event.accept()
