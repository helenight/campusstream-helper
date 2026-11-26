"""Sunshine / VDD 自动下载安装对话框。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QSettings, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QCloseEvent, QDesktopServices
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from core.common import CommandRunner, OperationResult
from core.downloader import (
    DEFAULT_VDD_DIR,
    SUNSHINE_PAGE,
    VDD_PAGE,
    HttpTransport,
    ManualDownloadRequired,
    Package,
    apply_proxy,
    default_download_dir,
)
from ui.theme import ThemeManager
from ui.widgets import (
    Banner,
    Card,
    InfoRow,
    StatusPill,
    make_button,
    muted_label,
    repolish,
)
from ui.workers import PackageUpdate, PackageWorker

PROXY_SETTINGS_KEY = "network/github_proxy"


def _format_size(size: int) -> str:
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    if size >= 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size} B"


def _format_speed(speed: float) -> str:
    if speed <= 0:
        return "—"
    if speed >= 1024 * 1024:
        return f"{speed / (1024 * 1024):.1f} MB/s"
    if speed >= 1024:
        return f"{speed / 1024:.0f} KB/s"
    return f"{speed:.0f} B/s"


class PackageInstallDialog(QDialog):
    """自动解析、下载并安装安装包；失败时提供两种手动兜底。"""

    installed = Signal(str)

    def __init__(
        self,
        parent: QWidget | None,
        package: Package,
        *,
        dry_run: bool = False,
        vdd_install_dir: Path | None = None,
        download_dir: Path | None = None,
        transport: HttpTransport | None = None,
        settings: QSettings | None = None,
        require_confirmation: bool = True,
        auto_start: bool = False,
        runner_factory: Callable[[bool], CommandRunner] | None = None,
        theme: ThemeManager | None = None,
    ) -> None:
        super().__init__(parent)
        _ = theme  # 对话框跟随全局 QSS；参数保留给调用方的统一构造习惯。
        self._package = package
        self._dry_run = dry_run
        self._vdd_install_dir = (
            Path(vdd_install_dir) if vdd_install_dir is not None else DEFAULT_VDD_DIR
        )
        self._download_dir = (
            Path(download_dir) if download_dir is not None else None
        )
        self._transport = transport
        self._settings = settings if settings is not None else QSettings()
        self._require_confirmation = require_confirmation
        self._runner_factory = runner_factory
        self._auto_start = auto_start

        self._thread: QThread | None = None
        self._worker: PackageWorker | None = None
        self._running = False
        self._stage = ""
        self._cancel_requested = False
        self._close_when_done = False
        self._manual_error = False
        self._installed_success = False
        self._release_page = SUNSHINE_PAGE if package == "sunshine" else VDD_PAGE

        self.setWindowTitle(
            f"安装 {'Sunshine' if package == 'sunshine' else 'Virtual Display Driver'}"
            " · CampusStream Helper"
        )
        self.setMinimumSize(780, 680)
        self.resize(820, 720)
        self.setModal(True)
        self._build_ui()
        self._wire_signals()
        self._render_default_notice()
        self._update_controls()

        if auto_start:
            QTimer.singleShot(120, self.start)

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
        self.title_label = QLabel(
            "安装 Sunshine"
            if self._package == "sunshine"
            else "安装 Virtual Display Driver"
        )
        self.title_label.setObjectName("PageTitle")
        subtitle = QLabel(
            "从 GitHub 获取最新版本，校验文件大小后安装；自动流程失败时可手动下载或选择本地安装包。"
        )
        subtitle.setObjectName("PageSubtitle")
        subtitle.setWordWrap(True)
        header_text.addWidget(self.title_label)
        header_text.addWidget(subtitle)
        header.addLayout(header_text, 1)
        self.status_pill = StatusPill("准备就绪", "muted")
        header.addWidget(self.status_pill, 0, Qt.AlignmentFlag.AlignTop)
        root.addLayout(header)

        self.notice = Banner("", tone="info", title="")
        self.notice.setVisible(False)
        root.addWidget(self.notice)

        details = Card()
        self.package_row = InfoRow(
            "安装包",
            "Sunshine（自动匹配 Windows 架构）"
            if self._package == "sunshine"
            else "Virtual Display Driver（VDD.Control 压缩包）",
        )
        self.source_row = InfoRow("来源", "GitHub Releases 最新版本")
        details.add(self.package_row)
        details.add(self.source_row)
        proxy_label = QLabel("GitHub 加速代理（可选）")
        proxy_label.setObjectName("SectionTitle")
        details.add(proxy_label)
        self.proxy_edit = QLineEdit()
        self.proxy_edit.setPlaceholderText("例如：https://gh-proxy.com/")
        self.proxy_edit.setClearButtonEnabled(True)
        self.proxy_edit.setText(
            str(self._settings.value(PROXY_SETTINGS_KEY, "") or "")
        )
        details.add(self.proxy_edit)
        details.add(
            muted_label(
                "留空表示直连 GitHub。API 直连失败时会自动尝试该前缀；下载也通过该前缀进行。"
            )
        )
        root.addWidget(details)

        progress_card = Card()
        progress_header = QHBoxLayout()
        self.stage_label = QLabel("尚未开始")
        self.stage_label.setObjectName("SectionTitle")
        progress_header.addWidget(self.stage_label, 1)
        self.percent_label = QLabel("0%")
        self.percent_label.setObjectName("Muted")
        progress_header.addWidget(self.percent_label)
        progress_header.addSpacing(12)
        self.speed_label = QLabel("—")
        self.speed_label.setObjectName("Muted")
        progress_header.addWidget(self.speed_label)
        progress_card.add_layout(progress_header)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(10)
        progress_card.add(self.progress_bar)

        progress_footer = QHBoxLayout()
        self.size_label = muted_label("—")
        progress_footer.addWidget(self.size_label, 1)
        progress_footer.addWidget(muted_label("文件大小校验：完成后再安装"))
        progress_card.add_layout(progress_footer)

        self.log_view = QPlainTextEdit()
        self.log_view.setObjectName("LogView")
        self.log_view.setReadOnly(True)
        self.log_view.setMinimumHeight(170)
        self.log_view.setMaximumBlockCount(2000)
        self.log_view.setPlaceholderText("下载与安装日志会显示在这里。")
        progress_card.add(self.log_view)
        root.addWidget(progress_card, 1)

        fallback = QHBoxLayout()
        fallback.setSpacing(8)
        fallback.addWidget(muted_label("自动流程失败时："))
        self.open_button = make_button("打开浏览器手动下载")
        self.local_button = make_button("选择本地安装包继续安装")
        fallback.addWidget(self.open_button)
        fallback.addWidget(self.local_button)
        fallback.addStretch()
        root.addLayout(fallback)

        bottom = QHBoxLayout()
        bottom.setSpacing(10)
        self.mode_label = QLabel("Dry-Run 预览" if self._dry_run else "真实安装")
        self.mode_label.setObjectName("Muted")
        bottom.addWidget(self.mode_label, 1)
        self.cancel_button = make_button("关闭", "secondary")
        self.start_button = make_button(
            "Dry-Run 解析" if self._dry_run else "开始下载并安装",
            "primary",
        )
        bottom.addWidget(self.cancel_button)
        bottom.addWidget(self.start_button)
        root.addLayout(bottom)

    def _wire_signals(self) -> None:
        self.start_button.clicked.connect(self._on_start_clicked)
        self.cancel_button.clicked.connect(self._on_cancel_clicked)
        self.open_button.clicked.connect(self._open_browser)
        self.local_button.clicked.connect(self._choose_local)

    # ------------------------------------------------------------------
    # 状态
    # ------------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def _task_active(self) -> bool:
        return self._running or self._thread is not None

    def _render_default_notice(self) -> None:
        if self._dry_run:
            self._set_notice(
                "Dry-Run：只解析版本、下载链接与将执行的安装命令，不会下载、解压或改动系统。",
                "info",
                "Dry-Run 预览",
            )
        else:
            self.notice.setVisible(False)

    def _set_notice(self, text: str, tone: str, title: str) -> None:
        self.notice.set_text(text, tone=tone, title=title)
        self.notice.setVisible(bool(text))

    def _append_log(self, message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_view.appendPlainText(f"[{stamp}] {message}")
        bar = self.log_view.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _remember_proxy(self) -> None:
        try:
            self._settings.setValue(
                PROXY_SETTINGS_KEY, self.proxy_edit.text().strip()
            )
            self._settings.sync()
        except Exception:
            # QSettings 写入失败不应影响安装流程。
            pass

    def _update_controls(self) -> None:
        active = self._task_active
        self.start_button.setEnabled(not active)
        self.proxy_edit.setEnabled(not active)
        self.open_button.setEnabled(not active)
        self.local_button.setEnabled(not active)

        if not active and self._stage == "dry_run":
            self.start_button.setText("再次 Dry-Run")
        elif self._installed_success and not active:
            self.start_button.setText("重新下载并安装")
        elif not active and self._stage in {"failed", "cancelled"}:
            self.start_button.setText("Dry-Run 解析" if self._dry_run else "重试")
        else:
            self.start_button.setText(
                "Dry-Run 解析" if self._dry_run else "开始下载并安装"
            )

        if active:
            if self._stage == "install":
                self.cancel_button.setText("安装中…")
                self.cancel_button.setEnabled(False)
            elif self._cancel_requested:
                self.cancel_button.setText("正在取消…")
                self.cancel_button.setEnabled(False)
            else:
                self.cancel_button.setText("取消")
                self.cancel_button.setEnabled(True)
        else:
            self.cancel_button.setText("关闭")
            self.cancel_button.setEnabled(True)

    # ------------------------------------------------------------------
    # 启动 / 取消
    # ------------------------------------------------------------------

    def _on_start_clicked(self) -> None:
        self.start()

    def start(self, local_path: Path | None = None) -> None:
        if self._task_active:
            return
        proxy = self.proxy_edit.text().strip()
        if proxy:
            try:
                apply_proxy("https://github.com/", proxy)
            except ValueError as exc:
                QMessageBox.warning(self, "代理地址无效", str(exc))
                return
        self._remember_proxy()
        self.log_view.clear()
        self._stage = "resolve"
        self._cancel_requested = False
        self._close_when_done = False
        self._manual_error = False
        self._installed_success = False
        self._release_page = (
            SUNSHINE_PAGE if self._package == "sunshine" else VDD_PAGE
        )
        self._running = True
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setProperty("result", "")
        repolish(self.progress_bar)
        self.percent_label.setText("0%")
        self.speed_label.setText("—")
        self.size_label.setText("—")
        self.stage_label.setText(
            "正在读取本地安装包…" if local_path else "正在连接 GitHub…"
        )
        self.status_pill.set_status("准备中", "info")
        self._render_default_notice()
        self._append_log(
            f"[开始] 使用本地安装包：{local_path}"
            if local_path
            else f"[开始] 自动安装 {self._package}"
        )
        self._update_controls()
        self._spawn_worker(local_path, proxy)

    def _spawn_worker(self, local_path: Path | None, proxy: str) -> None:
        worker = PackageWorker(
            self._package,
            dry_run=self._dry_run,
            proxy_prefix=proxy,
            local_path=local_path,
            vdd_install_dir=self._vdd_install_dir,
            download_dir=self._download_dir,
            transport=self._transport,
            require_confirmation=self._require_confirmation,
            runner_factory=self._runner_factory,
        )
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.update.connect(self._on_update)
        worker.log.connect(self._append_log)
        worker.confirm_required.connect(self._on_confirm_required)
        worker.manual_required.connect(self._on_manual_required)
        worker.finished.connect(self._on_finished)
        worker.failed.connect(self._on_failed)
        worker.cancelled.connect(self._on_cancelled)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.cancelled.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._on_thread_finished)
        self._thread = thread
        self._worker = worker
        thread.start()

    def _on_cancel_clicked(self) -> None:
        if not self._task_active:
            self.close()
            return
        if self._stage == "install":
            self._append_log("[取消] 安装已开始，无法中断当前安装程序")
            return
        if self._worker is not None:
            self._cancel_requested = True
            self._worker.request_cancel()
            self.status_pill.set_status("正在取消", "warning")
            self.stage_label.setText("正在取消后台任务…")
            self._append_log("[取消] 用户请求取消")
            self._update_controls()

    # ------------------------------------------------------------------
    # Worker 回调
    # ------------------------------------------------------------------

    def _on_update(self, update: PackageUpdate) -> None:
        self._stage = update.stage
        percent = max(0, min(100, int(update.percent)))
        self.progress_bar.setValue(percent)
        self.percent_label.setText(f"{percent}%")
        self.speed_label.setText(_format_speed(update.speed_bps))
        if update.total > 0:
            self.size_label.setText(
                f"{_format_size(update.received)} / {_format_size(update.total)}"
            )
        else:
            self.size_label.setText("—")
        if update.message:
            self.stage_label.setText(update.message)

        if update.stage == "download":
            self.status_pill.set_status("下载中", "info")
        elif update.stage == "verify":
            self.status_pill.set_status("校验完成", "info")
        elif update.stage == "install":
            self.status_pill.set_status("安装中", "info")
            self._update_controls()
        elif update.stage == "done":
            self.progress_bar.setProperty("result", "success")
            repolish(self.progress_bar)

    def _on_confirm_required(self, message: str) -> None:
        self.status_pill.set_status("等待确认", "info")
        self.stage_label.setText("等待确认安装")
        self._append_log("[确认] 等待用户确认安装")
        answer = QMessageBox.question(
            self,
            "确认安装",
            message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if self._worker is not None:
            self._worker.approve(answer == QMessageBox.StandardButton.Yes)

    def _on_manual_required(self, error: object) -> None:
        self._manual_error = True
        if isinstance(error, ManualDownloadRequired) and error.release_page:
            self._release_page = error.release_page
        self._set_notice(
            "自动获取或下载安装包失败。请点击「打开浏览器手动下载」下载后，"
            "再用「选择本地安装包继续安装」完成安装。",
            "warning",
            "需要手动处理",
        )

    def _on_finished(self, result: OperationResult) -> None:
        self._running = False
        for detail in result.details:
            if detail:
                self._append_log(f"[结果] {detail}")

        if result.ok:
            self._installed_success = not result.simulated
            self.progress_bar.setValue(100)
            self.progress_bar.setProperty("result", "success")
            self.percent_label.setText("100%")
            if result.simulated:
                self._stage = "dry_run"
                self.status_pill.set_status("Dry-Run 完成", "info")
                self._set_notice(result.message, "info", "Dry-Run 完成")
                self.stage_label.setText("Dry-Run 完成，未写入系统")
                self._append_log(f"[完成] {result.message}")
            else:
                self._stage = "done"
                self.status_pill.set_status("安装完成", "success")
                self._set_notice(result.message, "success", "安装完成")
                self.stage_label.setText(result.message)
                self._append_log(f"[完成] {result.message}")
                self.installed.emit(self._package)
        else:
            self._stage = "failed"
            self.progress_bar.setProperty("result", "failed")
            self.status_pill.set_status("安装失败", "danger")
            title = "自动下载失败" if self._manual_error else "安装失败"
            tone = "warning" if self._manual_error else "danger"
            self._set_notice(result.message, tone, title)
            self.stage_label.setText(result.message)
            self._append_log(f"[错误] {result.message}")
            if self._manual_error:
                self._append_log(
                    "[提示] 可使用「打开浏览器手动下载」或"
                    "「选择本地安装包继续安装」。"
                )
        repolish(self.progress_bar)
        self._update_controls()

    def _on_failed(self, message: str) -> None:
        self._running = False
        self._stage = "failed"
        self.progress_bar.setProperty("result", "failed")
        repolish(self.progress_bar)
        self.status_pill.set_status("操作失败", "danger")
        title = "自动下载失败" if self._manual_error else "操作失败"
        tone = "warning" if self._manual_error else "danger"
        text = (
            f"{message}\n请点击下方「打开浏览器手动下载」，"
            "或使用「选择本地安装包继续安装」。"
            if self._manual_error
            else message
        )
        self._set_notice(text, tone, title)
        self.stage_label.setText(message)
        self._append_log(f"[错误] {message}")
        if self._manual_error:
            self._append_log(
                "[提示] 可使用「打开浏览器手动下载」或"
                "「选择本地安装包继续安装」。"
            )
        self._update_controls()

    def _on_cancelled(self) -> None:
        self._running = False
        self._stage = "cancelled"
        self.progress_bar.setProperty("result", "")
        repolish(self.progress_bar)
        self.status_pill.set_status("已取消", "muted")
        self._set_notice("已取消本次下载/安装，没有改动系统。", "info", "已取消")
        self.stage_label.setText("操作已取消")
        self._append_log("[取消] 操作已取消")
        self._update_controls()

    def _on_thread_finished(self) -> None:
        self._thread = None
        self._worker = None
        if self._running:
            self._running = False
            self._on_failed("后台任务意外结束，请重试。")
        else:
            self._update_controls()
        if self._close_when_done:
            self._close_when_done = False
            if self._installed_success:
                self.accept()
            else:
                self._remember_proxy()
                super().reject()

    # ------------------------------------------------------------------
    # 兜底操作 / 关闭
    # ------------------------------------------------------------------

    def _open_browser(self) -> None:
        QDesktopServices.openUrl(QUrl(self._release_page))
        self._append_log(f"[浏览器] 已打开下载页：{self._release_page}")

    def _choose_local(self) -> None:
        if self._task_active:
            return
        start_dir = self._download_dir or default_download_dir()
        if self._package == "sunshine":
            file_filter = "Sunshine 安装包 (*.msi);;所有文件 (*.*)"
        else:
            file_filter = "VDD 压缩包 (*.zip);;所有文件 (*.*)"
        path, _selected = QFileDialog.getOpenFileName(
            self,
            "选择本地安装包",
            str(start_dir),
            file_filter,
            options=QFileDialog.Option.DontUseNativeDialog,
        )
        if path:
            self.start(Path(path))

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._task_active:
            if self._stage == "install":
                QMessageBox.information(
                    self,
                    "安装进行中",
                    "安装程序已经开始运行，请等待安装完成后再关闭窗口。",
                )
                event.ignore()
                return
            answer = QMessageBox.question(
                self,
                "任务进行中",
                "下载或校验仍在进行，确定取消并关闭吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self._close_when_done = True
            self._cancel_requested = True
            if self._worker is not None:
                self._worker.request_cancel()
            self.status_pill.set_status("正在取消", "warning")
            self.stage_label.setText("正在取消后台任务…")
            self._append_log("[取消] 窗口关闭请求已转为取消任务")
            self._update_controls()
            event.ignore()
            return
        self._remember_proxy()
        event.accept()

    def reject(self) -> None:
        if self._task_active:
            self.close()
            return
        self._remember_proxy()
        super().reject()
