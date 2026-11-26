"""四步向导主窗口。"""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import QEvent, Qt, QThread, QTimer
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from core import __version__
from core.common import CommandRunner, OperationResult
from core.network import enable_ipv6_binding
from core.plan import SetupPlan, SetupSummary
from core.privileges import is_admin, relaunch_as_admin
from core.vdd import restore_native_display
from ui.steps.step_devices import DevicesStep
from ui.steps.step_environment import EnvironmentStep
from ui.steps.step_execute import ExecuteStep
from ui.steps.step_guide import GuideStep
from ui.theme import ThemeManager, ThemeSwitcher
from ui.widgets import make_button, repolish
from ui.workers import DetectionWorker, EnvironmentData, FunctionWorker

STEP_META: tuple[tuple[str, str], ...] = (
    ("环境自检", "权限、服务、驱动与网络"),
    ("客户端与设备", "选择平台与分辨率"),
    ("一键配置", "后台执行与回滚"),
    ("配对指引", "连接地址与分平台提示"),
)


class SidebarStep(QWidget):
    """左侧步骤指示器。"""

    def __init__(self, index: int, title: str, subtitle: str) -> None:
        super().__init__()
        self.setObjectName("SidebarStep")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(10)

        self.index_label = QLabel(str(index))
        self.index_label.setObjectName("StepIndex")
        self.index_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.index_label)

        text_layout = QVBoxLayout()
        text_layout.setSpacing(1)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("StepTitle")
        self.subtitle_label = QLabel(subtitle)
        self.subtitle_label.setObjectName("StepSubtitle")
        text_layout.addWidget(self.title_label)
        text_layout.addWidget(self.subtitle_label)
        layout.addLayout(text_layout, 1)

        self.set_state("pending")

    def set_state(self, state: str) -> None:
        self.setProperty("state", state)
        repolish(self)


class WizardWindow(QMainWindow):
    """CampusStream Helper 主窗口。"""

    def __init__(
        self,
        *,
        force_dry_run: bool = False,
        auto_detect: bool = True,
        theme: ThemeManager | None = None,
    ) -> None:
        super().__init__()
        if theme is None:
            app = QApplication.instance()
            if app is None:
                raise RuntimeError("创建窗口前必须先创建 QApplication")
            theme = ThemeManager(app)
        self.theme = theme
        self.dry_run = force_dry_run
        self.env_data: EnvironmentData | None = None
        self.summary: SetupSummary | None = None
        self.pending_plan: SetupPlan | None = None

        self._detect_thread: QThread | None = None
        self._detect_worker: DetectionWorker | None = None
        self._task_thread: QThread | None = None
        self._task_worker: FunctionWorker | None = None

        self.setWindowTitle("CampusStream Helper · 跨端远控一键配置助手")
        self.resize(1180, 780)
        self.setMinimumSize(1000, 680)
        self._build_ui()
        self._wire_signals()
        self._go_to(0)
        if auto_detect:
            QTimer.singleShot(200, self.refresh_environment)

    # ------------------------------------------------------------------
    # 构建
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("Root")
        root.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        sidebar = QWidget()
        sidebar.setObjectName("Sidebar")
        sidebar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        sidebar.setFixedWidth(258)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(24, 28, 24, 22)
        sidebar_layout.setSpacing(6)

        title = QLabel("CampusStream")
        title.setObjectName("AppTitle")
        subtitle = QLabel("跨端远控一键配置助手")
        subtitle.setObjectName("AppSubtitle")
        sidebar_layout.addWidget(title)
        sidebar_layout.addWidget(subtitle)
        sidebar_layout.addSpacing(26)

        self.sidebar_steps: list[SidebarStep] = []
        for index, (step_title, step_subtitle) in enumerate(STEP_META, start=1):
            step = SidebarStep(index, step_title, step_subtitle)
            self.sidebar_steps.append(step)
            sidebar_layout.addWidget(step)

        sidebar_layout.addStretch()
        self.dry_run_check = QCheckBox("Dry-Run 模拟运行")
        self.dry_run_check.setObjectName("DryRunToggle")
        self.dry_run_check.setChecked(self.dry_run)
        self.dry_run_check.setToolTip(
            "开启后所有写操作只记录日志，不修改系统；适合开发测试和首次体验。"
        )
        sidebar_layout.addWidget(self.dry_run_check)
        self.admin_label = QLabel()
        self.admin_label.setObjectName("AdminPill")
        self.admin_label.setWordWrap(True)
        sidebar_layout.addWidget(self.admin_label)
        version_label = QLabel(f"v{__version__} · MIT License")
        version_label.setObjectName("SidebarFootnote")
        sidebar_layout.addWidget(version_label)
        self._update_admin_label()

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(32, 24, 32, 20)
        content_layout.setSpacing(16)

        header = QHBoxLayout()
        header_text = QVBoxLayout()
        header_text.setSpacing(2)
        self.page_title = QLabel()
        self.page_title.setObjectName("PageTitle")
        self.page_subtitle = QLabel()
        self.page_subtitle.setObjectName("PageSubtitle")
        header_text.addWidget(self.page_title)
        header_text.addWidget(self.page_subtitle)
        header.addLayout(header_text)
        header.addStretch()
        self.step_counter = QLabel()
        self.step_counter.setObjectName("Muted")
        header.addWidget(self.step_counter)
        header.addSpacing(10)
        self.theme_switcher = ThemeSwitcher(self.theme)
        header.addWidget(self.theme_switcher)
        content_layout.addLayout(header)

        self.stack = QStackedWidget()
        self.environment_step = EnvironmentStep()
        self.devices_step = DevicesStep()
        self.execute_step = ExecuteStep()
        self.guide_step = GuideStep()
        self.stack.addWidget(self.environment_step)
        self.stack.addWidget(self.devices_step)
        self.stack.addWidget(self.execute_step)
        self.stack.addWidget(self.guide_step)
        self.environment_step.set_mode(self.dry_run)
        content_layout.addWidget(self.stack, 1)

        nav = QHBoxLayout()
        nav.setSpacing(10)
        self.back_button = make_button("上一步", "secondary")
        self.next_button = make_button("下一步", "primary")
        self.nav_hint = QLabel()
        self.nav_hint.setObjectName("Muted")
        nav.addWidget(self.back_button)
        nav.addWidget(self.nav_hint, 1)
        nav.addWidget(self.next_button)
        content_layout.addLayout(nav)

        root_layout.addWidget(sidebar)
        root_layout.addWidget(content, 1)
        self.setCentralWidget(root)

    def _wire_signals(self) -> None:
        self.environment_step.refresh_requested.connect(self.refresh_environment)
        self.environment_step.fix_ipv6_requested.connect(self._fix_ipv6)
        self.environment_step.relaunch_requested.connect(self._relaunch)
        self.devices_step.selection_changed.connect(self._on_selection_changed)
        self.execute_step.setup_finished.connect(self._on_setup_finished)
        self.execute_step.rollback_finished.connect(self._on_rollback_finished)
        self.execute_step.execution_started.connect(self._on_execution_started)
        self.execute_step.busy_changed.connect(self._on_busy_changed)
        self.guide_step.restore_display_requested.connect(self._restore_display)
        self.dry_run_check.toggled.connect(self._on_dry_run_toggled)
        self.back_button.clicked.connect(self._go_back)
        self.next_button.clicked.connect(self._go_next)
        self.theme.changed.connect(self._on_theme_changed)

    # ------------------------------------------------------------------
    # 属性 / 导航
    # ------------------------------------------------------------------

    @property
    def step_count(self) -> int:
        return len(STEP_META)

    @property
    def current_step(self) -> int:
        return self.stack.currentIndex()

    def _go_to(self, index: int) -> None:
        if index < 0 or index >= self.step_count:
            return
        if index == 2:
            if self.pending_plan is None:
                return
            self.execute_step.set_plan(self.pending_plan)
        if index == 3:
            if self.summary is None:
                return
            self.guide_step.set_summary(self.summary)

        self.stack.setCurrentIndex(index)
        title, subtitle = STEP_META[index]
        self.page_title.setText(title)
        self.page_subtitle.setText(subtitle)
        self.step_counter.setText(f"第 {index + 1} 步 / 共 {self.step_count} 步")
        self._refresh_sidebar()
        self._update_nav()

    def _refresh_sidebar(self) -> None:
        index = self.current_step
        for step_index, step in enumerate(self.sidebar_steps):
            if step_index == index:
                state = "current"
            elif step_index < index or (step_index == 2 and self.summary is not None):
                state = "done"
            else:
                state = "pending"
            step.set_state(state)

    def _update_nav(self) -> None:
        index = self.current_step
        busy = self.execute_step.is_busy
        self.back_button.setEnabled(index > 0 and not busy)
        if busy:
            self.next_button.setEnabled(False)
            self.next_button.setText("执行中…")
            self.nav_hint.setText("后台任务执行中，界面保持响应，请稍候…")
            return
        if index == 2:
            self.next_button.setText("查看指引")
            self.next_button.setEnabled(self.summary is not None)
        elif index == 3:
            self.next_button.setText("完成")
            self.next_button.setEnabled(True)
        else:
            self.next_button.setText("下一步")
            self.next_button.setEnabled(True)

        if index == 0:
            self.nav_hint.setText(
                "检测完成，可继续下一步。"
                if self.env_data is not None
                else "正在检测系统环境…"
            )
        elif index == 1:
            monitors = self.devices_step.selected_monitors()
            self.nav_hint.setText(
                f"已选择 {len(monitors)} 个分辨率。"
                if monitors
                else "请至少选择一个设备分辨率。"
            )
        elif index == 2:
            self.nav_hint.setText(
                "配置执行完成，点击「查看指引」。"
                if self.summary is not None
                else "点击「开始配置」执行；执行完成后继续。"
            )
        else:
            self.nav_hint.setText("按指引在移动端完成 Moonlight 配对。")

    def _go_back(self) -> None:
        if self.execute_step.is_busy:
            QMessageBox.information(self, "请稍候", "后台任务执行中，暂时不能返回。")
            return
        self._go_to(self.current_step - 1)

    def _go_next(self) -> None:
        if self.execute_step.is_busy:
            QMessageBox.information(self, "请稍候", "后台任务执行中，请等待完成。")
            return
        index = self.current_step

        if index == 0:
            if self.env_data is None:
                QMessageBox.information(self, "检测未完成", "环境检测尚未完成，请稍候。")
                return
            if not self.env_data.sunshine.installed or not self.env_data.vdd.installed:
                answer = QMessageBox.question(
                    self,
                    "环境未就绪",
                    "未检测到 Sunshine 或 Virtual Display Driver。\n"
                    "继续后 Step 3 将无法完成核心配置。仍要继续吗？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if answer != QMessageBox.StandardButton.Yes:
                    return
            self._go_to(1)
            return

        if index == 1:
            if not self.devices_step.is_valid():
                QMessageBox.warning(
                    self,
                    "尚未选择设备",
                    self.devices_step.validation_message(),
                )
                return
            try:
                plan = self.devices_step.build_plan(self.dry_run)
            except ValueError as exc:
                QMessageBox.warning(self, "配置无效", str(exc))
                return
            self.pending_plan = plan
            self.summary = None
            self.execute_step.set_plan(plan)
            self.guide_step.clear()
            self._go_to(2)
            return

        if index == 2:
            if self.summary is None:
                QMessageBox.information(
                    self,
                    "尚未执行",
                    "请先点击「开始配置」，等待执行完成后再查看指引。",
                )
                return
            self._go_to(3)
            return

        self.close()

    # ------------------------------------------------------------------
    # 环境检测
    # ------------------------------------------------------------------

    def refresh_environment(self) -> None:
        if self._detect_thread is not None and self._detect_thread.isRunning():
            return
        self.environment_step.set_loading(True)
        self.environment_step.set_mode(self.dry_run)
        thread = QThread(self)
        worker = DetectionWorker(dry_run=self.dry_run)
        self._detect_thread = thread
        self._detect_worker = worker
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.log.connect(self._on_detection_log)
        worker.finished.connect(self._on_environment_ready)
        worker.failed.connect(self._on_environment_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._on_detect_thread_finished)
        thread.start()

    def _on_detection_log(self, message: str) -> None:
        self.statusBar().showMessage(message, 3000)

    def _on_environment_ready(self, data: EnvironmentData) -> None:
        self.env_data = data
        self.environment_step.set_loading(False)
        self.environment_step.set_data(data)
        self.devices_step.set_environment(data)
        self._update_nav()

    def _on_environment_failed(self, message: str) -> None:
        self.environment_step.set_loading(False)
        self.environment_step.set_error(message)
        self.statusBar().showMessage(f"环境检测失败：{message}", 6000)

    def _on_detect_thread_finished(self) -> None:
        self._detect_thread = None
        self._detect_worker = None
        self._update_nav()

    # ------------------------------------------------------------------
    # 执行 / 回滚回调
    # ------------------------------------------------------------------

    def _on_execution_started(self) -> None:
        self.summary = None
        self.guide_step.clear()
        self._refresh_sidebar()
        self._update_nav()

    def _on_setup_finished(self, summary: SetupSummary) -> None:
        self.summary = summary
        self.guide_step.set_summary(summary)
        self.statusBar().showMessage(
            "配置成功" if summary.success else "配置存在失败项，请查看执行日志",
            6000,
        )
        self._refresh_sidebar()
        self._update_nav()

    def _on_rollback_finished(self, outcomes) -> None:
        self.summary = None
        self.guide_step.clear()
        self.refresh_environment()
        failed = [item for item in outcomes if not item.ok]
        if failed:
            QMessageBox.warning(
                self,
                "回滚完成但存在失败项",
                "\n".join(f"• {item.label}: {item.message}" for item in failed),
            )
        else:
            QMessageBox.information(self, "回滚完成", "已撤销本次写入的配置。")
        self._refresh_sidebar()
        self._update_nav()

    def _on_busy_changed(self, busy: bool) -> None:
        self.dry_run_check.setEnabled(not busy)
        self._update_nav()

    def _on_selection_changed(self) -> None:
        if self.summary is not None:
            self.summary = None
            self.guide_step.clear()
            self.execute_step.reset()
        self._refresh_sidebar()
        self._update_nav()

    # ------------------------------------------------------------------
    # 模式 / 提权 / 单项操作
    # ------------------------------------------------------------------

    def _on_theme_changed(self, _effective: str) -> None:
        self.environment_step.refresh_theme()
        self.guide_step.refresh_theme()

    def event(self, event: QEvent) -> bool:
        if event.type() in (
            QEvent.Type.ApplicationActivate,
            QEvent.Type.WindowActivate,
        ):
            self.theme.refresh_system()
        return super().event(event)

    def _on_dry_run_toggled(self, checked: bool) -> None:
        self.dry_run = checked
        self.environment_step.set_mode(checked)
        self._update_admin_label()
        if self.summary is not None and self.summary.plan.dry_run != checked:
            self.summary = None
            self.guide_step.clear()
            self.execute_step.reset()
        # 已生成的计划必须同步模式，避免界面显示 Dry-Run 却按旧计划真实写入。
        if self.pending_plan is not None and self.pending_plan.dry_run != checked:
            self.pending_plan = replace(self.pending_plan, dry_run=checked)
            self.execute_step.set_plan(self.pending_plan)
        self._update_nav()

    def _update_admin_label(self) -> None:
        if is_admin():
            text = "管理员权限 · 可以真实配置"
        elif self.dry_run:
            text = "普通权限 · 仅 Dry-Run"
        else:
            text = "普通权限 · 真实配置可能失败"
        self.admin_label.setText(text)

    def _relaunch(self) -> None:
        args = ["--dry-run"] if self.dry_run else []
        if relaunch_as_admin(args):
            QApplication.quit()
        else:
            QMessageBox.warning(
                self,
                "提权失败",
                "用户取消了 UAC，或系统拒绝提权。你仍可开启 Dry-Run 体验流程。",
            )

    def _fix_ipv6(self, adapters: list[str]) -> None:
        if not adapters:
            return

        def fix(runner: CommandRunner) -> OperationResult:
            results = [enable_ipv6_binding(runner, name) for name in adapters]
            ok = all(result.ok for result in results)
            details = tuple(result.message for result in results)
            if ok:
                return OperationResult.success(
                    f"已为 {len(adapters)} 个网卡启用 IPv6",
                    details=details,
                )
            return OperationResult.failure("部分网卡 IPv6 启用失败", details=details)

        self._run_task(
            FunctionWorker(fix, dry_run=self.dry_run),
            self._on_ipv6_fixed,
        )

    def _on_ipv6_fixed(self, result: OperationResult) -> None:
        if result.ok:
            QMessageBox.information(self, "IPv6 修复", result.message)
        else:
            QMessageBox.warning(self, "IPv6 修复失败", result.message)
        self.refresh_environment()

    def _restore_display(self) -> None:
        self._run_task(
            FunctionWorker(
                lambda runner: restore_native_display(runner),
                dry_run=self.dry_run,
            ),
            self._on_display_restored,
        )

    def _on_display_restored(self, result: OperationResult) -> None:
        if result.ok:
            QMessageBox.information(self, "显示模式", result.message)
        else:
            QMessageBox.warning(self, "显示模式切换失败", result.message)

    def _run_task(self, worker: FunctionWorker, on_finished) -> None:
        if self._task_thread is not None and self._task_thread.isRunning():
            QMessageBox.information(self, "请稍候", "已有任务正在执行。")
            return
        thread = QThread(self)
        self._task_thread = thread
        self._task_worker = worker
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.log.connect(self._on_detection_log)
        worker.finished.connect(on_finished)
        worker.failed.connect(self._on_task_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._on_task_thread_finished)
        thread.start()

    def _on_task_failed(self, message: str) -> None:
        QMessageBox.warning(self, "操作失败", message)

    def _on_task_thread_finished(self) -> None:
        self._task_thread = None
        self._task_worker = None

    # ------------------------------------------------------------------
    # 关闭
    # ------------------------------------------------------------------

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.execute_step.is_busy:
            QMessageBox.warning(self, "配置执行中", "请等待配置执行完成后再关闭窗口。")
            event.ignore()
            return
        if self._detect_thread is not None and self._detect_thread.isRunning():
            QMessageBox.warning(self, "环境检测中", "请等待环境检测完成后再关闭窗口。")
            event.ignore()
            return
        if self._task_thread is not None and self._task_thread.isRunning():
            QMessageBox.warning(self, "任务执行中", "请等待当前操作完成后再关闭窗口。")
            event.ignore()
            return
        event.accept()
