"""Step 3：后台执行、实时日志、进度与回滚。"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from core.plan import SetupPlan, SetupSummary, StepOutcome
from ui.widgets import Banner, Card, StatusPill, heading, make_button, muted_label
from ui.workers import RollbackWorker, SetupWorker


class ExecuteStep(QWidget):
    """配置执行页面。"""

    setup_finished = Signal(object)
    rollback_finished = Signal(object)
    execution_started = Signal()
    busy_changed = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._plan: SetupPlan | None = None
        self._summary: SetupSummary | None = None
        self._thread: QThread | None = None
        self._worker = None
        self._running_kind: str | None = None
        self._busy = False
        self._last_error: str | None = None
        self._rollback_outcomes: tuple[StepOutcome, ...] | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)

        status_card = Card()
        header = QHBoxLayout()
        self.status_title = heading("等待开始")
        header.addWidget(self.status_title)
        header.addStretch()
        self.status_pill = StatusPill("待执行", "muted")
        header.addWidget(self.status_pill)
        status_card.add_layout(header)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(10)
        status_card.add(self.progress)

        progress_row = QHBoxLayout()
        self.current_label = muted_label("点击「开始配置」后，后台线程会逐步执行。")
        progress_row.addWidget(self.current_label, 1)
        self.percent_label = QLabel("0%")
        self.percent_label.setObjectName("Muted")
        progress_row.addWidget(self.percent_label)
        status_card.add_layout(progress_row)

        self.result_banner = Banner("", tone="info")
        self.result_banner.setVisible(False)
        status_card.add(self.result_banner)
        root.addWidget(status_card)

        log_card = Card("实时日志")
        self.log_view = QPlainTextEdit()
        self.log_view.setObjectName("LogView")
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(5000)
        self.log_view.setMinimumHeight(300)
        log_card.add(self.log_view)
        root.addWidget(log_card, 1)

        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        self.start_button = make_button("开始配置", "primary")
        self.cancel_button = make_button("取消", "secondary")
        self.rollback_button = make_button("一键回滚", "danger")
        self.cancel_button.setEnabled(False)
        self.rollback_button.setEnabled(False)
        self.start_button.clicked.connect(self.start)
        self.cancel_button.clicked.connect(self.cancel)
        self.rollback_button.clicked.connect(self.rollback)
        buttons.addWidget(self.start_button)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.rollback_button)
        buttons.addStretch()
        root.addLayout(buttons)

        root.addWidget(
            muted_label(
                "执行在后台线程中进行，界面不会卡死。取消只会在当前步骤结束后生效；"
                "已完成的部分可以通过「一键回滚」撤销。"
            )
        )

    # ------------------------------------------------------------------
    # 外部调用
    # ------------------------------------------------------------------

    @property
    def is_busy(self) -> bool:
        return self._busy

    @property
    def summary(self) -> SetupSummary | None:
        return self._summary

    def set_plan(self, plan: SetupPlan) -> None:
        if self._busy:
            return
        if self._plan is not None and plan != self._plan and self._summary is not None:
            self.reset()
            self.append_log("配置计划已更新，请重新执行。")
        self._plan = plan
        self.start_button.setText("开始模拟运行" if plan.dry_run else "开始配置")
        if not self._busy and not self._summary:
            self.status_pill.set_status(
                "Dry-Run 待执行" if plan.dry_run else "待执行",
                "info" if plan.dry_run else "muted",
            )

    def start(self) -> None:
        if self._busy:
            return
        if self._plan is None:
            QMessageBox.warning(self, "无法开始", "配置计划尚未准备完成。")
            return
        self._summary = None
        self._last_error = None
        self._rollback_outcomes = None
        self.log_view.clear()
        self.progress.setValue(0)
        self.percent_label.setText("0%")
        self.result_banner.setVisible(False)
        self.status_title.setText("正在执行配置")
        self.status_pill.set_status("执行中", "info")
        self.current_label.setText("准备后台任务…")
        self.append_log("开始执行配置流程。")
        self.execution_started.emit()
        self._launch(SetupWorker(self._plan), "setup")

    def cancel(self) -> None:
        if self._running_kind != "setup" or not isinstance(self._worker, SetupWorker):
            return
        self._worker.request_cancel()
        self.cancel_button.setEnabled(False)
        self.status_pill.set_status("正在取消", "warning")
        self.append_log("已请求取消，将在当前步骤结束后停止。")

    def rollback(self) -> None:
        if self._busy or self._summary is None:
            return
        if self._summary.plan.dry_run:
            QMessageBox.information(self, "无需回滚", "Dry-Run 未写入系统，无需回滚。")
            return
        if not self._summary.rollback_available:
            QMessageBox.information(self, "无需回滚", "本次执行没有可回滚的写入项。")
            return
        self.log_view.clear()
        self.progress.setValue(0)
        self.result_banner.setVisible(False)
        self.status_title.setText("正在回滚配置")
        self.status_pill.set_status("回滚中", "warning")
        self.append_log("开始回滚本次配置。")
        self._launch(RollbackWorker(self._summary), "rollback")

    def reset(self) -> None:
        self._summary = None
        self._last_error = None
        self._rollback_outcomes = None
        self.log_view.clear()
        self.progress.setValue(0)
        self.percent_label.setText("0%")
        self.current_label.setText("点击「开始配置」后，后台线程会逐步执行。")
        self.status_title.setText("等待开始")
        self.status_pill.set_status(
            "Dry-Run 待执行" if self._plan and self._plan.dry_run else "待执行",
            "info" if self._plan and self._plan.dry_run else "muted",
        )
        self.result_banner.setVisible(False)
        self.rollback_button.setEnabled(False)
        self.start_button.setEnabled(not self._busy)

    def append_log(self, message: str) -> None:
        if not message:
            return
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_view.appendPlainText(f"[{stamp}] {message}")
        scrollbar = self.log_view.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    # ------------------------------------------------------------------
    # QThread 管理
    # ------------------------------------------------------------------

    def _launch(self, worker, kind: str) -> None:
        self._running_kind = kind
        self._set_busy(True, kind)
        thread = QThread(self)
        self._thread = thread
        self._worker = worker
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.log.connect(self.append_log)
        worker.progress.connect(self._on_progress)
        worker.finished.connect(self._on_worker_finished)
        worker.failed.connect(self._on_worker_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._on_thread_finished)
        thread.start()

    def _on_progress(self, value: int, text: str) -> None:
        self.progress.setValue(value)
        self.percent_label.setText(f"{value}%")
        self.current_label.setText(text)

    def _on_worker_finished(self, result) -> None:
        if self._running_kind == "setup":
            summary: SetupSummary = result
            self._summary = summary
            self.progress.setValue(100)
            self.percent_label.setText("100%")
            for outcome in summary.outcomes:
                prefix = "[成功]" if outcome.ok else "[失败]"
                self.append_log(f"{prefix} {outcome.label}：{outcome.message}")
            if summary.success:
                self.status_title.setText("配置完成")
                self.status_pill.set_status("成功", "success")
                self.result_banner.set_text(
                    "关键配置已全部写入。下一步可查看分平台配对指引。",
                    tone="success",
                    title="配置成功",
                )
            else:
                self.status_title.setText("配置未完全成功")
                self.status_pill.set_status("存在失败项", "danger")
                failed = "；".join(
                    f"{item.label}：{item.message}" for item in summary.failed_outcomes
                )
                self.result_banner.set_text(
                    failed or "请查看实时日志。",
                    tone="danger",
                    title="需要处理",
                )
            self.result_banner.setVisible(True)
            self.start_button.setText("重新执行")
            self.setup_finished.emit(summary)
        elif self._running_kind == "rollback":
            outcomes: tuple[StepOutcome, ...] = result
            self._rollback_outcomes = outcomes
            for outcome in outcomes:
                prefix = "[成功]" if outcome.ok else "[失败]"
                self.append_log(f"{prefix} {outcome.label}：{outcome.message}")
            self.status_title.setText("已执行回滚")
            self.status_pill.set_status("回滚结束", "muted")
            self.result_banner.set_text(
                "已尝试撤销本次配置。请重新检测环境确认状态。",
                tone="info",
                title="回滚完成",
            )
            self.result_banner.setVisible(True)
            self._summary = None
            self.start_button.setText("重新配置")

    def _on_worker_failed(self, message: str) -> None:
        self._last_error = message
        self.append_log(f"[失败] 后台任务异常：{message}")
        self.status_title.setText("执行失败")
        self.status_pill.set_status("异常", "danger")

    def _on_thread_finished(self) -> None:
        kind = self._running_kind
        self._set_busy(False, None)
        self._thread = None
        self._worker = None
        self._running_kind = None

        if kind == "rollback":
            outcomes = self._rollback_outcomes
            self._rollback_outcomes = None
            if outcomes is not None:
                self.rollback_finished.emit(outcomes)
            self.rollback_button.setEnabled(False)
        elif kind == "setup" and self._summary is not None:
            self.rollback_button.setEnabled(self._summary.rollback_available)

        if self._last_error:
            error = self._last_error
            self._last_error = None
            QMessageBox.warning(self, "后台任务失败", error)

    def _set_busy(self, busy: bool, kind: str | None) -> None:
        self._busy = busy
        self.start_button.setEnabled(not busy)
        self.cancel_button.setEnabled(busy and kind == "setup")
        self.rollback_button.setEnabled(
            not busy
            and self._summary is not None
            and self._summary.rollback_available
        )
        self.busy_changed.emit(busy)
