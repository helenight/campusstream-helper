"""Step 4：分平台配对与避坑指引。"""

from __future__ import annotations

import html

from PySide6.QtCore import QTimer, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from core.plan import SetupSummary
from ui.widgets import (
    Banner,
    Card,
    InfoRow,
    StatusPill,
    heading,
    make_button,
    muted_label,
)

MOONLIGHT_URL = "https://moonlight-stream.org/"


class GuideStep(QWidget):
    """连接信息、执行结果与分平台提示。"""

    restore_display_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._summary: SetupSummary | None = None
        self._build_ui()

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

        self.result_banner = Banner("", tone="info")
        self.result_banner.setVisible(False)
        layout.addWidget(self.result_banner)

        connect_card = Card()
        connect_header = QHBoxLayout()
        connect_header.addWidget(heading("PC 推荐连接地址"))
        connect_header.addStretch()
        self.connect_pill = StatusPill("等待配置", "muted")
        connect_header.addWidget(self.connect_pill)
        connect_card.add_layout(connect_header)

        self.ip_label = QLabel("—")
        self.ip_label.setObjectName("HeroValue")
        self.ip_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        connect_card.add(self.ip_label)
        connect_card.add(
            muted_label("优先使用有线 Ethernet IPv4；若只有 IPv6，请在 Moonlight 中带方括号输入。")
        )

        self.wired_row = InfoRow("有线 IPv4")
        self.wifi_row = InfoRow("无线 IPv4")
        self.ipv6_row = InfoRow("全局 IPv6")
        connect_card.add(self.wired_row)
        connect_card.add(self.wifi_row)
        connect_card.add(self.ipv6_row)

        connect_buttons = QHBoxLayout()
        connect_buttons.setSpacing(10)
        self.copy_button = make_button("复制地址", "secondary")
        self.pair_button = make_button("打开 Sunshine 配对页", "primary")
        self.copy_button.setEnabled(False)
        self.pair_button.setEnabled(False)
        self.copy_button.clicked.connect(self._copy_ip)
        self.pair_button.clicked.connect(self._open_pairing)
        connect_buttons.addWidget(self.copy_button)
        connect_buttons.addWidget(self.pair_button)
        connect_buttons.addStretch()
        connect_card.add_layout(connect_buttons)
        layout.addWidget(connect_card)

        result_card = Card()
        result_header = QHBoxLayout()
        result_header.addWidget(heading("本次配置结果"))
        result_header.addStretch()
        self.result_pill = StatusPill("等待执行", "muted")
        result_header.addWidget(self.result_pill)
        result_card.add_layout(result_header)
        self.outcome_container = QWidget()
        self.outcome_layout = QVBoxLayout(self.outcome_container)
        self.outcome_layout.setContentsMargins(0, 0, 0, 0)
        self.outcome_layout.setSpacing(6)
        result_card.add(self.outcome_container)
        layout.addWidget(result_card)

        tips_card = Card("分平台配对与避坑指引")
        self.tips = QTextBrowser()
        self.tips.setOpenExternalLinks(True)
        self.tips.setMinimumHeight(360)
        tips_card.add(self.tips)
        layout.addWidget(tips_card)

        actions = QHBoxLayout()
        actions.setSpacing(10)
        self.restore_button = make_button("切回仅电脑屏幕", "secondary")
        self.moonlight_button = make_button("打开 Moonlight 官网", "link")
        self.restore_button.clicked.connect(self.restore_display_requested.emit)
        self.moonlight_button.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(MOONLIGHT_URL))
        )
        actions.addWidget(self.restore_button)
        actions.addWidget(self.moonlight_button)
        actions.addStretch()
        layout.addLayout(actions)
        layout.addWidget(
            muted_label(
                "如果配置后常用软件窗口跑到看不见的屏幕，点击「切回仅电脑屏幕」，"
                "或按 Win + P 选择「仅电脑屏幕」。串流开始时 Sunshine 会重新激活虚拟屏。"
            )
        )
        layout.addStretch()
        scroll.setWidget(content)
        root.addWidget(scroll)

    def clear(self) -> None:
        self._summary = None
        self.ip_label.setText("—")
        self.connect_pill.set_status("等待配置", "muted")
        self.result_pill.set_status("等待执行", "muted")
        self.copy_button.setEnabled(False)
        self.pair_button.setEnabled(False)
        self.result_banner.setVisible(False)
        self.wired_row.set_value("—")
        self.wifi_row.set_value("—")
        self.ipv6_row.set_value("—")
        self._clear_outcomes()
        self.tips.setHtml("")

    def set_summary(self, summary: SetupSummary) -> None:
        self._summary = summary
        network = summary.network
        ip = summary.recommended_ip

        self.ip_label.setText(ip or "未获取到可用地址")
        if ip:
            self.connect_pill.set_status("可以连接", "success")
            self.copy_button.setEnabled(True)
            self.pair_button.setEnabled(bool(summary.pairing_url))
        else:
            self.connect_pill.set_status("无可用地址", "danger")
            self.copy_button.setEnabled(False)
            self.pair_button.setEnabled(False)

        if network is not None:
            self.wired_row.set_value(network.ethernet_ipv4 or "未检测到")
            self.wifi_row.set_value(network.wlan_ipv4 or "未检测到")
            self.ipv6_row.set_value(network.preferred_ipv6 or "未检测到")

        if summary.success:
            self.result_pill.set_status("成功", "success")
        elif summary.plan.dry_run:
            self.result_pill.set_status("模拟完成", "info")
        else:
            self.result_pill.set_status("存在失败项", "danger")

        if summary.plan.dry_run:
            self.result_banner.set_text(
                "Dry-Run 已完成，所有写入都只记录在日志中，未修改系统。",
                tone="info",
                title="模拟运行结束",
            )
        elif summary.success:
            self.result_banner.set_text(
                "关键配置已写入，可以开始配对。若连接异常，请先查看下方结果与 FAQ。",
                tone="success",
                title="配置完成",
            )
        else:
            self.result_banner.set_text(
                "部分步骤失败，请回到执行页查看日志；修复后可重新执行。",
                tone="danger",
                title="配置未完全成功",
            )
        self.result_banner.setVisible(True)

        self._render_outcomes(summary)
        self.tips.setHtml(self._build_tips(summary))

    # ------------------------------------------------------------------

    def _render_outcomes(self, summary: SetupSummary) -> None:
        self._clear_outcomes()
        if not summary.outcomes:
            self.outcome_layout.addWidget(muted_label("本次没有执行步骤。"))
            return
        for outcome in summary.outcomes:
            row = QWidget()
            layout = QHBoxLayout(row)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(10)
            if outcome.simulated:
                pill = StatusPill("模拟", "info")
            elif outcome.ok:
                pill = StatusPill("成功", "success")
            else:
                pill = StatusPill("失败", "danger")
            label = QLabel(outcome.label)
            label.setStyleSheet("font-weight: 600;")
            message = muted_label(outcome.message)
            layout.addWidget(pill)
            layout.addWidget(label)
            layout.addWidget(message, 1)
            if outcome.details:
                row.setToolTip("\n".join(outcome.details))
            self.outcome_layout.addWidget(row)

    def _clear_outcomes(self) -> None:
        while self.outcome_layout.count():
            item = self.outcome_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _copy_ip(self) -> None:
        if self._summary is None or not self._summary.recommended_ip:
            return
        QGuiApplication.clipboard().setText(self._summary.recommended_ip)
        self.copy_button.setText("已复制")
        QTimer.singleShot(1500, lambda: self.copy_button.setText("复制地址"))

    def _open_pairing(self) -> None:
        if self._summary is None or not self._summary.pairing_url:
            return
        QDesktopServices.openUrl(QUrl(self._summary.pairing_url))

    def _build_tips(self, summary: SetupSummary) -> str:
        device_items = "".join(
            f"<li>{html.escape(item.label or '设备')}："
            f"{item.width}×{item.height} @ {item.refresh_hz}Hz，"
            f"Windows 缩放建议 <b>{item.windows_scaling_percent}%</b></li>"
            for item in summary.plan.monitors
        )
        sections = [
            "<style>"
            "body { font-family: 'Microsoft YaHei UI'; font-size: 12px; color: #0F172A; }"
            "h3 { margin: 10px 0 6px 0; font-size: 14px; }"
            "li { margin-bottom: 5px; }"
            ".muted { color: #64748B; }"
            "</style>",
            f"<h3>本次设备与缩放</h3><ul>{device_items}</ul>",
        ]

        platforms = set(summary.plan.platforms)
        if "ipados" in platforms:
            sections.append(
                "<h3>iPadOS 指引</h3><ul>"
                "<li>在 App Store 搜索并安装 <b>Moonlight</b>（Moonlight Game Streaming Project）。</li>"
                "<li>Resolution 选择 <b>Custom</b>，输入上表对应分辨率。</li>"
                "<li>Frame pacing 选择 <b>Smoothest Video</b>。</li>"
                "<li><b>关闭「台前调度」</b>，并关闭 Moonlight 的「自然滚动」。</li>"
                "<li>跨区组网推荐国区星空组网 / 蒲公英。</li>"
                "</ul>"
            )
        if "android_tablet" in platforms:
            sections.append(
                "<h3>Android 平板指引</h3><ul>"
                "<li>从 moonlight-stream.org 下载官方 APK，或在应用商店安装。</li>"
                "<li>在 Moonlight 设置中把帧率上限调到 <b>120 FPS</b> 并解除帧率限制。</li>"
                "<li>跨区组网可直接安装 <b>Tailscale</b> 或星空组网。</li>"
                "<li>三星 DeX、小米/华为桌面模式可能改变分辨率，串流时建议退出。</li>"
                "</ul>"
            )
        if "android_phone" in platforms:
            sections.append(
                "<h3>Android 手机指引</h3><ul>"
                "<li>从 moonlight-stream.org 下载官方 APK；高刷机型开启 <b>120 FPS</b>。</li>"
                "<li>手机屏幕小，建议把 Windows 缩放调到 <b>175%～200%</b>。</li>"
                "<li>外接鼠标时，需要在 Moonlight 中授予指针捕获权限。</li>"
                "<li>跨区组网可直接使用 Tailscale 或星空组网。</li>"
                "</ul>"
            )
        if "ios" in platforms:
            sections.append(
                "<h3>iPhone / iOS 指引</h3><ul>"
                "<li>在 App Store 搜索并安装 Moonlight。</li>"
                "<li>iPhone 端通常为 60Hz，建议 Windows 缩放调到 <b>175% 以上</b>。</li>"
                "<li>开启低电量模式可能限制网络性能，串流时建议关闭。</li>"
                "</ul>"
            )
        if not platforms:
            sections.append(
                "<h3>自定义设备指引</h3><ul>"
                "<li>在 Moonlight 中选择 Custom，填入上表的分辨率与刷新率。</li>"
                "<li>若出现黑屏，先确认 Sunshine 服务的 output_name 与虚拟屏 device_id 一致。</li>"
                "</ul>"
            )

        sections.append(
            "<h3>通用检查</h3><ul>"
            "<li>PC 与客户端需要在同一局域网，或处于同一 Tailscale / 星空组网虚拟网络。</li>"
            "<li>若 Moonlight 显示 offline，确认 Sunshine 服务运行且防火墙已放行。</li>"
            "<li>串流结束后若窗口跑偏，按 <b>Win + P</b> 选择「仅电脑屏幕」。</li>"
            "</ul>"
        )
        if summary.device_id:
            sections.append(
                f"<p class='muted'>已写入虚拟屏 device_id：{html.escape(summary.device_id)}</p>"
            )
        return "".join(sections)
