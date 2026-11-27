"""四步向导主窗口。"""

from __future__ import annotations

import os
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
from core.network import enable_ipv6_binding, ensure_firewall_rule
from core.plan import SetupPlan, SetupSummary
from core.privileges import is_admin, relaunch_as_admin
from core.sunshine import (
    pairing_port,
    restart_service,
    start_service,
    wait_for_local_port,
)
from core.vdd import restore_native_display
from ui.dialogs import DiagnosticsDialog, PackageInstallDialog, UninstallDialog
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
        self._detecting = False

        self._detect_thread: QThread | None = None
        self._detect_worker: DetectionWorker | None = None
        self._task_thread: QThread | None = None
        self._task_worker: FunctionWorker | None = None
        self._package_dialog: PackageInstallDialog | None = None
        self._diagnostics_dialog: DiagnosticsDialog | None = None
        self._package_install_done = False
        self._package_refresh_timer = QTimer(self)
        self._package_refresh_timer.setSingleShot(True)
        self._package_refresh_timer.timeout.connect(self.refresh_environment)

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
        self.diagnostics_button = make_button("排障诊断", "secondary")
        self.diagnostics_button.setToolTip(
            "只读收集系统、Sunshine、VDD、网络与日志信息，"
            "生成可脱敏的 Markdown 报告。"
        )
        header.addWidget(self.diagnostics_button)
        header.addSpacing(6)
        self.uninstall_button = make_button("一键还原与卸载", "danger")
        self.uninstall_button.setToolTip(
            "恢复显示模式、还原电源、移除防火墙，并可卸载 Sunshine 与 VDD。"
        )
        header.addWidget(self.uninstall_button)
        header.addSpacing(6)
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
        self.environment_step.download_requested.connect(self._open_package_dialog)
        self.environment_step.restart_sunshine_requested.connect(
            self._restart_sunshine
        )
        self.environment_step.add_firewall_requested.connect(self._add_firewall)
        self.environment_step.device_manager_requested.connect(
            self._open_device_manager
        )
        self.devices_step.selection_changed.connect(self._on_selection_changed)
        self.execute_step.setup_finished.connect(self._on_setup_finished)
        self.execute_step.rollback_finished.connect(self._on_rollback_finished)
        self.execute_step.execution_started.connect(self._on_execution_started)
        self.execute_step.busy_changed.connect(self._on_busy_changed)
        self.guide_step.restore_display_requested.connect(self._restore_display)
        self.guide_step.pairing_service_requested.connect(
            self._start_sunshine_for_pairing
        )
        self.diagnostics_button.clicked.connect(self._open_diagnostics_dialog)
        self.uninstall_button.clicked.connect(self._open_uninstall_dialog)
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
        self.back_button.setEnabled(index > 0 and not busy and not self._detecting)
        if busy:
            self.next_button.setEnabled(False)
            self.next_button.setText("执行中…")
            self.nav_hint.setText("后台任务执行中，界面保持响应，请稍候…")
            return
        if self._detecting:
            self.next_button.setText("检测中…")
            self.next_button.setEnabled(False)
            self.nav_hint.setText("正在检测系统环境，请稍候…")
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
                else "检测未完成，请点击「重新检测」。"
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
        if self._detecting:
            QMessageBox.information(self, "检测中", "请等待环境检测完成后再继续。")
            return
        index = self.current_step

        if index == 0:
            if self.env_data is None:
                QMessageBox.information(
                    self, "检测未完成", "环境检测尚未完成，请点击「重新检测」。"
                )
                return
            sunshine = self.env_data.sunshine
            vdd = self.env_data.vdd
            if (
                sunshine is None
                or not sunshine.installed
                or vdd is None
                or not vdd.installed
                or not vdd.device_ready
            ):
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
        self._detecting = True
        self.env_data = None
        self.environment_step.set_mode(self.dry_run)
        self.environment_step.start_detection()
        self._update_nav()
        thread = QThread(self)
        worker = DetectionWorker(dry_run=self.dry_run)
        self._detect_thread = thread
        self._detect_worker = worker
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.log.connect(self._on_detection_log)
        worker.item_changed.connect(self.environment_step.apply_item)
        worker.progress.connect(self.environment_step.set_progress)
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
        self._detecting = False
        self.environment_step.finish(data)
        self.devices_step.set_environment(data)
        self._update_nav()

    def _on_environment_failed(self, message: str) -> None:
        self._detecting = False
        self.environment_step.set_error(message)
        self._update_nav()
        self.statusBar().showMessage(f"环境检测失败：{message}", 6000)

    def _on_detect_thread_finished(self) -> None:
        self._detect_thread = None
        self._detect_worker = None
        if self._detecting:
            self._detecting = False
            self.environment_step.set_error("检测线程意外结束，请重试。")
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
        self.uninstall_button.setEnabled(not busy)
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

    def _open_package_dialog(self, package: str) -> None:
        """打开自动下载安装对话框；完成后自动重新检测。"""
        if package not in {"sunshine", "vdd"}:
            return
        if self._package_dialog is not None and self._package_dialog.isVisible():
            self._package_dialog.raise_()
            self._package_dialog.activateWindow()
            return

        dialog_dry_run = self.dry_run
        if not dialog_dry_run and not is_admin():
            box = QMessageBox(self)
            box.setWindowTitle("管理员权限")
            box.setIcon(QMessageBox.Icon.Warning)
            box.setText("自动安装通常需要管理员权限。")
            box.setInformativeText(
                "你可以先以 Dry-Run 预览下载地址与安装命令，"
                "或仍然尝试安装（可能触发 UAC / 被系统拒绝）。"
            )
            preview = box.addButton(
                "以 Dry-Run 预览", QMessageBox.ButtonRole.AcceptRole
            )
            attempt = box.addButton(
                "仍然尝试安装", QMessageBox.ButtonRole.ActionRole
            )
            box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
            box.setDefaultButton(preview)
            box.exec()
            clicked = box.clickedButton()
            if clicked is preview:
                dialog_dry_run = True
            elif clicked is not attempt:
                return

        vdd_dir = None
        if self.env_data is not None and self.env_data.vdd is not None:
            vdd_dir = self.env_data.vdd.install_dir
        dialog = PackageInstallDialog(
            self,
            package,
            dry_run=dialog_dry_run,
            vdd_install_dir=vdd_dir,
            auto_start=True,
            theme=self.theme,
        )
        self._package_dialog = dialog
        self._package_install_done = False

        def on_installed(name: str) -> None:
            self._package_install_done = True
            if name == "sunshine":
                message = (
                    "Sunshine 安装完成，服务已在后台自启；"
                    "无需手动双击桌面 Sunshine 图标"
                )
                delay_ms = 4000
            else:
                message = "VDD 安装流程结束，正在重新检测驱动状态…"
                delay_ms = 1200
            self.statusBar().showMessage(message, 8000)
            self._package_refresh_timer.start(delay_ms)

        dialog.installed.connect(on_installed)
        dialog.exec()
        self._package_dialog = None
        if self._package_install_done and self._package_refresh_timer.isActive():
            self._package_refresh_timer.stop()
            self.refresh_environment()

    def _open_diagnostics_dialog(self) -> None:
        """打开只读排障诊断；采集期间仍可继续操作主界面之外的其他窗口。"""
        if self._diagnostics_dialog is not None and self._diagnostics_dialog.isVisible():
            self._diagnostics_dialog.raise_()
            self._diagnostics_dialog.activateWindow()
            return
        dialog = DiagnosticsDialog(
            self,
            dry_run=self.dry_run,
            theme=self.theme,
        )
        self._diagnostics_dialog = dialog
        dialog.exec()
        self._diagnostics_dialog = None
        if dialog.report is not None and dialog.report.failed_sections:
            self.statusBar().showMessage(
                "诊断完成，但存在采集失败的板块；可查看报告中的失败原因",
                8000,
            )
        elif dialog.report is not None:
            self.statusBar().showMessage("诊断报告已生成，可复制或导出", 6000)

    def _open_uninstall_dialog(self) -> None:
        """打开勾选式清理/卸载窗口；真实执行后重新检测环境。"""
        if (
            self._detecting
            or self.execute_step.is_busy
            or (
                self._task_thread is not None
                and self._task_thread.isRunning()
            )
        ):
            QMessageBox.information(
                self,
                "请稍候",
                "当前有检测或配置任务正在执行，请完成后再打开清理与卸载。",
            )
            return

        vdd_dir = None
        if self.env_data is not None and self.env_data.vdd is not None:
            vdd_dir = self.env_data.vdd.install_dir
        dialog = UninstallDialog(
            self,
            dry_run=self.dry_run,
            vdd_install_dir=vdd_dir,
            theme=self.theme,
        )
        dialog.exec()
        if not dialog.executed:
            return
        if dialog.was_dry_run:
            self.statusBar().showMessage(
                "Dry-Run 清理预览完成，未修改系统",
                6000,
            )
            return

        report = dialog.report
        self.pending_plan = None
        self.summary = None
        self.execute_step.reset()
        self.guide_step.clear()
        self._refresh_sidebar()
        self._update_nav()
        if report is None:
            self.statusBar().showMessage(
                "清理任务异常结束，请查看清理日志",
                8000,
            )
        elif report.failed_steps:
            self.statusBar().showMessage(
                "清理完成但存在失败项，请查看清理日志",
                8000,
            )
        else:
            self.statusBar().showMessage(
                "清理与卸载完成，正在重新检测环境…",
                8000,
            )
        self._go_to(0)
        self.refresh_environment()

    def _restart_sunshine(self) -> None:
        sunshine = self.env_data.sunshine if self.env_data else None
        if sunshine is None:
            QMessageBox.warning(self, "无法启动", "尚未检测到 Sunshine 安装信息。")
            return
        self._run_task(
            FunctionWorker(
                lambda runner: restart_service(runner, sunshine),
                dry_run=self.dry_run,
            ),
            self._on_repair_finished,
        )

    def _start_sunshine_for_pairing(self) -> None:
        """Step 4 配对页未监听时，一键启动 SunshineService 并等待端口。"""
        sunshine = self.env_data.sunshine if self.env_data else None
        if sunshine is None or not sunshine.installed:
            self.guide_step.pairing_failed(
                "未检测到 Sunshine 安装信息，请先安装，或回到 Step 1 点击"
                "「重新修复安装」。"
            )
            return
        if self._task_thread is not None and self._task_thread.isRunning():
            self.guide_step.reset_pairing_state()
            QMessageBox.information(
                self,
                "请稍候",
                "已有任务正在执行，请完成后再启动 Sunshine 服务。",
            )
            return

        port = pairing_port(sunshine)
        service_name = sunshine.service_name

        def start_and_wait(runner: CommandRunner) -> OperationResult:
            try:
                result = start_service(runner, service_name)
                if not result.ok or result.simulated:
                    return result
                if wait_for_local_port(port, timeout_s=5.0):
                    return OperationResult.success(
                        f"Sunshine 服务已启动，127.0.0.1:{port} 已监听",
                        details=result.details,
                    )
                return OperationResult.failure(
                    f"服务已启动，但 127.0.0.1:{port} 仍未监听。"
                    "请点击右上角「排障诊断」查看端口、服务与日志。",
                    details=result.details,
                )
            except Exception as exc:
                return OperationResult.failure(
                    f"启动服务时发生异常：{type(exc).__name__}: {exc}"
                )

        self._run_task(
            FunctionWorker(start_and_wait, dry_run=self.dry_run),
            self._on_pairing_service_started,
        )

    def _on_pairing_service_started(self, result: OperationResult) -> None:
        if result.simulated:
            QMessageBox.information(
                self,
                "Dry-Run 模拟",
                f"{result.message}\n\n"
                "当前是 Dry-Run，未实际启动服务；关闭 Dry-Run 后重试即可打开配对页。",
            )
            self.guide_step.reset_pairing_state()
            return
        if result.ok:
            self.statusBar().showMessage(result.message, 6000)
            self.guide_step.resume_pairing()
            return
        self.guide_step.pairing_failed(result.message)

    def _add_firewall(self) -> None:
        sunshine = self.env_data.sunshine if self.env_data else None
        exe = sunshine.executable if sunshine else None
        if exe is None:
            QMessageBox.warning(
                self,
                "无法添加规则",
                "未找到 sunshine.exe，请先安装 Sunshine。",
            )
            return
        self._run_task(
            FunctionWorker(
                lambda runner: ensure_firewall_rule(runner, exe),
                dry_run=self.dry_run,
            ),
            self._on_repair_finished,
        )

    def _on_repair_finished(self, result: OperationResult) -> None:
        if result.ok:
            QMessageBox.information(self, "修复完成", result.message)
        else:
            QMessageBox.warning(self, "修复失败", result.message)
        self.refresh_environment()

    def _open_device_manager(self) -> None:
        try:
            os.startfile("devmgmt.msc")  # type: ignore[attr-defined]
        except OSError as exc:
            QMessageBox.warning(self, "无法打开设备管理器", str(exc))

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
