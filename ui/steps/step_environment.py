"""Step 1：环境自检的逐项进度、四态徽章与修复入口。"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from core.detection import (
    DetectionItem,
    DetectionState,
    ITEM_LABELS,
    ITEM_ORDER,
    pending_items,
    summarize,
)
from core.privileges import is_admin
from ui.widgets import (
    Banner,
    Card,
    InfoRow,
    StatusBadge,
    StatusPill,
    heading,
    make_button,
    muted_label,
    repolish,
)
from ui.workers import EnvironmentData

STATE_TEXT = {
    DetectionState.PENDING: "⏳ 等待检测",
    DetectionState.RUNNING: "⏳ 检测中...",
    DetectionState.READY: "✅ 已就绪",
    DetectionState.WARNING: "⚠️ 未安装 / 需处理",
    DetectionState.FAILED: "❌ 检测失败",
}


def _badge_text(item: DetectionItem) -> str:
    if item.state != DetectionState.WARNING:
        return STATE_TEXT[item.state]
    needs_install = any(
        action.kind in {"download_sunshine", "download_vdd"}
        for action in item.actions
    )
    return "⚠️ 未安装 / 需处理" if needs_install else "⚠️ 需处理"


def _clear_layout(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()


class _DetectionCard(Card):
    """单个检测项：标题、状态徽章、明细和操作按钮。"""

    action_triggered = Signal(str, object)

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent=parent)
        self._rows: list[InfoRow] = []
        self._actions = ()

        header = QHBoxLayout()
        header.addWidget(heading(title))
        header.addStretch()
        self.badge = StatusBadge("⏳ 等待检测", "pending")
        header.addWidget(self.badge)
        self.add_layout(header)

        self.summary = QLabel("")
        self.summary.setObjectName("Muted")
        self.summary.setWordWrap(True)
        self.summary.setVisible(False)
        self.add(self.summary)

        self.detail_container = QWidget()
        self.detail_layout = QVBoxLayout(self.detail_container)
        self.detail_layout.setContentsMargins(0, 0, 0, 0)
        self.detail_layout.setSpacing(4)
        self.add(self.detail_container)

        self.action_container = QWidget()
        self.action_layout = QHBoxLayout(self.action_container)
        self.action_layout.setContentsMargins(0, 4, 0, 0)
        self.action_layout.setSpacing(8)
        self.action_container.setVisible(False)
        self.add(self.action_container)
        self.body.addStretch(1)

    def apply_item(self, item: DetectionItem) -> None:
        self.badge.set_state(item.state.value, _badge_text(item))
        self.summary.setText(item.summary)
        self.summary.setVisible(bool(item.summary))

        _clear_layout(self.detail_layout)
        self._rows = []
        for label, value in item.detail:
            row = InfoRow(label, value)
            self.detail_layout.addWidget(row)
            self._rows.append(row)

        _clear_layout(self.action_layout)
        self._actions = item.actions
        for action in item.actions:
            kind = (
                "primary"
                if action.kind in {"download_sunshine", "download_vdd"}
                else "secondary"
            )
            button = make_button(action.label, kind)
            button.clicked.connect(
                lambda _checked=False, current=action: self.action_triggered.emit(
                    current.kind, current.payload
                )
            )
            self.action_layout.addWidget(button)
        self.action_layout.addStretch()
        self.action_container.setVisible(bool(item.actions))

    def refresh_theme(self) -> None:
        for row in self._rows:
            row.refresh_theme()


class EnvironmentStep(QWidget):
    """展示五项检测结果，并发出修复 / 下载 / 重新检测请求。"""

    refresh_requested = Signal()
    fix_ipv6_requested = Signal(list)
    relaunch_requested = Signal()
    download_requested = Signal(str)
    restart_sunshine_requested = Signal()
    add_firewall_requested = Signal()
    device_manager_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._data: EnvironmentData | None = None
        self._dry_run = False
        self._detecting = False
        self._items: dict[str, DetectionItem] = {}
        self._cards: dict[str, _DetectionCard] = {}
        self._last_finished_at: datetime | None = None
        self._build_ui()
        self._reset_cards()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(14)

        summary_card = Card()
        summary_header = QHBoxLayout()
        self.summary_title = heading("系统环境检测")
        summary_header.addWidget(self.summary_title)
        summary_header.addStretch()
        self.summary_pill = StatusPill("等待检测", "muted")
        summary_header.addWidget(self.summary_pill)
        summary_card.add_layout(summary_header)

        self.progress = QProgressBar()
        self.progress.setRange(0, 5)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(10)
        summary_card.add(self.progress)

        summary_footer = QHBoxLayout()
        self.progress_text = muted_label("正在准备检测…")
        summary_footer.addWidget(self.progress_text, 1)
        self.timestamp_label = QLabel("上次检测：—")
        self.timestamp_label.setObjectName("Muted")
        summary_footer.addWidget(self.timestamp_label)
        self.refresh_button = make_button("重新检测", "primary")
        self.refresh_button.clicked.connect(self.refresh_requested.emit)
        summary_footer.addWidget(self.refresh_button)
        summary_card.add_layout(summary_footer)
        layout.addWidget(summary_card)

        self.mode_banner = Banner("", tone="info", title="运行模式")
        layout.addWidget(self.mode_banner)

        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(14)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        for key in ITEM_ORDER:
            card = _DetectionCard(ITEM_LABELS[key])
            card.action_triggered.connect(self._on_card_action)
            self._cards[key] = card
        grid.addWidget(self._cards["admin"], 0, 0)
        grid.addWidget(self._cards["gpu"], 0, 1)
        grid.addWidget(self._cards["sunshine"], 1, 0)
        grid.addWidget(self._cards["vdd"], 1, 1)
        grid.addWidget(self._cards["network"], 2, 0, 1, 2)
        layout.addLayout(grid)

        self.warning_banner = Banner("", tone="warning", title="网络提示")
        self.warning_banner.setVisible(False)
        layout.addWidget(self.warning_banner)
        layout.addStretch()

        scroll.setWidget(content)
        root.addWidget(scroll)

    def _reset_cards(self) -> None:
        self._items = {}
        for item in pending_items():
            self._items[item.key] = item
            self._cards[item.key].apply_item(item)

    # ------------------------------------------------------------------
    # 检测生命周期
    # ------------------------------------------------------------------

    @property
    def is_detecting(self) -> bool:
        return self._detecting

    def start_detection(self) -> None:
        self._detecting = True
        self._reset_cards()
        self.summary_title.setText("系统环境检测")
        self.summary_pill.set_status("检测中", "info")
        self.progress.setRange(0, 0)
        self.progress.setValue(0)
        self.progress.setProperty("result", "")
        repolish(self.progress)
        self.progress_text.setText("正在检测系统环境（已完成 0/5 项）…")
        self.timestamp_label.setText(
            f"上次检测：{self._last_finished_at.strftime('%H:%M:%S')}"
            if self._last_finished_at
            else "上次检测：—"
        )
        self.refresh_button.setText("检测中...")
        self.refresh_button.setEnabled(False)
        self.warning_banner.setVisible(False)
        self._render_mode()

    def apply_item(self, item: DetectionItem) -> None:
        self._items[item.key] = item
        self._cards[item.key].apply_item(item)

    def set_progress(self, completed: int, total: int) -> None:
        if not self._detecting:
            return
        self.progress_text.setText(
            f"正在检测系统环境（已完成 {completed}/{total} 项）…"
        )

    def finish(self, data: EnvironmentData) -> None:
        self._detecting = False
        if not data.items:
            self.set_error("检测未返回任何结果，请重新检测。")
            return
        self._data = data
        for item in data.items:
            self._items[item.key] = item
            self._cards[item.key].apply_item(item)

        text, result = summarize(data.items)
        self.summary_title.setText(text)
        self.progress.setRange(0, 5)
        self.progress.setValue(5)
        self.progress.setProperty("result", result)
        repolish(self.progress)
        self.progress_text.setText("已完成 5/5 项检测，可继续下一步。")
        self.refresh_button.setText("重新检测")
        self.refresh_button.setEnabled(True)

        states = [item.state for item in data.items]
        failed = states.count(DetectionState.FAILED)
        warning = states.count(DetectionState.WARNING)
        if failed:
            self.summary_pill.set_status(f"{failed} 项失败", "danger")
        elif warning:
            self.summary_pill.set_status(f"{warning} 项待处理", "warning")
        else:
            self.summary_pill.set_status("全部就绪", "success")

        finished_at = data.finished_at or datetime.now().astimezone()
        self._last_finished_at = finished_at
        self.timestamp_label.setText(
            f"上次检测：{finished_at.strftime('%H:%M:%S')}"
        )
        self._render_mode()

        warnings: list[str] = []
        if data.network is not None:
            warnings.extend(data.network.warnings)
        if data.error:
            warnings.append(data.error)
        if warnings:
            self.warning_banner.set_text(
                "\n".join(f"• {item}" for item in warnings),
                tone="warning",
                title="网络提示",
            )
            self.warning_banner.setVisible(True)
        else:
            self.warning_banner.setVisible(False)

    def set_error(self, message: str) -> None:
        self._detecting = False
        self.summary_title.setText("❌ 环境检测失败")
        self.summary_pill.set_status("检测失败", "danger")
        self.progress.setRange(0, 5)
        self.progress.setValue(0)
        self.progress.setProperty("result", "failed")
        repolish(self.progress)
        self.progress_text.setText(message)
        self.refresh_button.setText("重新检测")
        self.refresh_button.setEnabled(True)
        self.mode_banner.set_text(message, tone="danger", title="环境检测失败")

    # ------------------------------------------------------------------
    # 模式与主题
    # ------------------------------------------------------------------

    def set_mode(self, dry_run: bool) -> None:
        self._dry_run = dry_run
        self._render_mode()

    def _render_mode(self) -> None:
        if self._dry_run:
            self.mode_banner.set_text(
                "Dry-Run 模式已开启：检测照常执行，但所有配置写入只记录、不落盘。",
                tone="info",
                title="模拟运行",
            )
        elif is_admin():
            self.mode_banner.set_text(
                "管理员权限已就绪，可以执行真实的系统配置。",
                tone="success",
                title="权限正常",
            )
        else:
            self.mode_banner.set_text(
                "当前以普通权限运行。真实配置需要管理员权限；可重启提权，或开启 Dry-Run 先体验流程。",
                tone="danger",
                title="权限不足",
            )

    def refresh_theme(self) -> None:
        for card in self._cards.values():
            card.refresh_theme()

    # ------------------------------------------------------------------
    # 操作
    # ------------------------------------------------------------------

    def _on_card_action(self, kind: str, payload: object) -> None:
        values = tuple(payload) if isinstance(payload, (tuple, list)) else ()
        if kind == "download_sunshine":
            self.download_requested.emit("sunshine")
        elif kind == "download_vdd":
            self.download_requested.emit("vdd")
        elif kind == "fix_ipv6":
            self.fix_ipv6_requested.emit([str(value) for value in values])
        elif kind == "relaunch_admin":
            self.relaunch_requested.emit()
        elif kind == "restart_sunshine":
            self.restart_sunshine_requested.emit()
        elif kind == "add_firewall":
            self.add_firewall_requested.emit()
        elif kind == "open_device_manager":
            self.device_manager_requested.emit()
