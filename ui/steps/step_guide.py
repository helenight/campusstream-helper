"""Step 4：本机配对页、分平台双 Tab 教程与避坑指引。"""

from __future__ import annotations

import html
from urllib.parse import urlparse

from PySide6.QtCore import QTimer, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QScrollArea,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from core.plan import SetupSummary
from core.sunshine import PAIRING_PORT, is_local_port_open
from ui.theme import active_tokens
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
ARTEMIS_URL = "https://github.com/ClassicOldSong/moonlight-android/releases"
MOONLIGHT_ANDROID_URL = (
    "https://github.com/moonlight-stream/moonlight-android/releases"
)


class GuideStep(QWidget):
    """连接信息、执行结果、本机配对与分平台教程。"""

    restore_display_requested = Signal()
    pairing_service_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._summary: SetupSummary | None = None
        self._cert_notice_shown = False
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

        connect_card = Card("填进手机 / 平板的 Moonlight PC 地址")
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
            muted_label(
                "这是 Moonlight「添加电脑」要填的地址，不是浏览器地址。"
                "优先使用有线 Ethernet IPv4；若只有 IPv6，请在 Moonlight 中带方括号输入。"
            )
        )

        self.wired_row = InfoRow("有线 IPv4")
        self.wifi_row = InfoRow("无线 IPv4")
        self.ipv6_row = InfoRow("全局 IPv6")
        connect_card.add(self.wired_row)
        connect_card.add(self.wifi_row)
        connect_card.add(self.ipv6_row)

        connect_buttons = QHBoxLayout()
        connect_buttons.setSpacing(10)
        self.copy_button = make_button("复制 PC 地址", "secondary")
        self.copy_button.setEnabled(False)
        self.copy_button.clicked.connect(self._copy_ip)
        connect_buttons.addWidget(self.copy_button)
        connect_buttons.addStretch()
        connect_card.add_layout(connect_buttons)
        layout.addWidget(connect_card)

        pair_card = Card("本机浏览器打开，填 4 位 PIN 码")
        self.pairing_label = QLabel("—")
        self.pairing_label.setObjectName("HeroValue")
        self.pairing_label.setWordWrap(True)
        self.pairing_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        pair_card.add(self.pairing_label)
        pair_card.add(
            muted_label(
                "这个地址只在运行 Sunshine 的这台电脑上打开，不要填到手机里。"
                "固定使用 127.0.0.1 回环地址，避免 localhost 被解析到 IPv6 后打不开。"
                "首次打开如遇证书警告，点「高级 → 继续前往」。"
            )
        )
        pair_buttons = QHBoxLayout()
        pair_buttons.setSpacing(10)
        self.pair_button = make_button("打开本机配对页", "primary")
        self.pair_button.setEnabled(False)
        self.pair_button.clicked.connect(self._open_pairing)
        pair_buttons.addWidget(self.pair_button)
        pair_buttons.addStretch()
        pair_card.add_layout(pair_buttons)
        layout.addWidget(pair_card)

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

        general_card = Card("通用 3 步配对流程")
        self.general_tips = self._make_browser(190)
        general_card.add(self.general_tips)
        layout.addWidget(general_card)

        platform_card = Card("分平台保姆级教程")
        platform_card.add(
            muted_label(
                "在顶部切换设备类型：安卓平板 / 手机，或 iPad / iPhone。"
                "本次配置涉及的平台会自动切到对应标签。"
            )
        )
        self.platform_tabs = QTabWidget()
        self.platform_tabs.setMinimumHeight(430)

        android_page = QWidget()
        android_layout = QVBoxLayout(android_page)
        android_layout.setContentsMargins(0, 0, 0, 0)
        android_layout.setSpacing(8)
        self.android_tips = self._make_browser(360)
        android_layout.addWidget(self.android_tips)
        self.artemis_button = make_button(
            "打开 Artemis Releases（安卓增强版）",
            "secondary",
        )
        self.artemis_button.setVisible(False)
        self.artemis_button.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(ARTEMIS_URL))
        )
        android_layout.addWidget(self.artemis_button)
        self.platform_tabs.addTab(android_page, "安卓平板 / 手机")

        apple_page = QWidget()
        apple_layout = QVBoxLayout(apple_page)
        apple_layout.setContentsMargins(0, 0, 0, 0)
        apple_layout.setSpacing(8)
        self.apple_tips = self._make_browser(390)
        apple_layout.addWidget(self.apple_tips)
        self.platform_tabs.addTab(apple_page, "iPad / iPhone")
        platform_card.add(self.platform_tabs)
        layout.addWidget(platform_card)

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

    @staticmethod
    def _make_browser(minimum_height: int) -> QTextBrowser:
        browser = QTextBrowser()
        browser.setOpenExternalLinks(True)
        browser.setFrameShape(QFrame.Shape.NoFrame)
        browser.setMinimumHeight(minimum_height)
        return browser

    def refresh_theme(self) -> None:
        if self._summary is not None:
            self._render_tips(self._summary)

    def clear(self) -> None:
        self._summary = None
        self._cert_notice_shown = False
        self.ip_label.setText("—")
        self.pairing_label.setText("—")
        self.connect_pill.set_status("等待配置", "muted")
        self.result_pill.set_status("等待执行", "muted")
        self.copy_button.setEnabled(False)
        self.pair_button.setEnabled(False)
        self.result_banner.setVisible(False)
        self.wired_row.set_value("—")
        self.wifi_row.set_value("—")
        self.ipv6_row.set_value("—")
        self._clear_outcomes()
        self.general_tips.setHtml("")
        self.android_tips.setHtml("")
        self.apple_tips.setHtml("")
        self.artemis_button.setVisible(False)

    def set_summary(self, summary: SetupSummary) -> None:
        self._summary = summary
        self._cert_notice_shown = False
        network = summary.network
        ip = summary.recommended_ip

        self.ip_label.setText(ip or "未获取到可用地址")
        if ip:
            self.connect_pill.set_status("可以连接", "success")
            self.copy_button.setEnabled(True)
        else:
            self.connect_pill.set_status("无可用地址", "danger")
            self.copy_button.setEnabled(False)

        # 配对页始终使用本机 127.0.0.1，即使没有局域网 IP 也可以打开。
        self.pairing_label.setText(summary.pairing_url or "—")
        self.pair_button.setEnabled(bool(summary.pairing_url))

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
                "关键配置已写入，可以开始配对。若连接异常，请先查看下方结果与排雷区。",
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
        self._render_tips(summary)

    # ------------------------------------------------------------------
    # 配对页
    # ------------------------------------------------------------------

    def _pairing_port(self) -> int | None:
        if self._summary is None or not self._summary.pairing_url:
            return None
        try:
            return urlparse(self._summary.pairing_url).port or PAIRING_PORT
        except ValueError:
            return PAIRING_PORT

    def _open_pairing(self) -> None:
        if self._summary is None or not self._summary.pairing_url:
            return
        port = self._pairing_port()
        if port is None:
            return
        if is_local_port_open(port):
            self.open_pairing_page()
            return
        if self._confirm_start_service(port):
            self.pair_button.setEnabled(False)
            self.pairing_label.setText("正在启动 SunshineService…")
            self.pairing_service_requested.emit()

    def _confirm_start_service(self, port: int) -> bool:
        """配对页未监听时询问是否一键启动服务。"""
        box = QMessageBox(self)
        box.setWindowTitle("配对页尚未启动")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText(
            f"127.0.0.1:{port} 没有响应，Sunshine 的 Web UI 可能没有启动。"
        )
        box.setInformativeText(
            "常见原因：SunshineService 未运行、服务程序被杀软拦截，"
            "或端口被其他程序占用。\n\n"
            "可以尝试一键启动 SunshineService；如果仍然失败，"
            "请点击右上角「排障诊断」查看完整报告。"
        )
        start_button = box.addButton(
            "一键启动服务",
            QMessageBox.ButtonRole.AcceptRole,
        )
        box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(start_button)
        box.exec()
        return box.clickedButton() is start_button

    def open_pairing_page(self) -> None:
        """在系统浏览器打开配对页，并首次提示自签名证书处理方式。"""
        if self._summary is None or not self._summary.pairing_url:
            return
        QDesktopServices.openUrl(QUrl(self._summary.pairing_url))
        if self._cert_notice_shown:
            return
        self._cert_notice_shown = True
        QMessageBox.information(
            self,
            "配对页已打开",
            "首次打开 127.0.0.1 配对页时，浏览器会提示"
            "「你的连接不是专用连接」或「证书无效」。\n\n"
            "这是 Sunshine 本机自签名证书，仅在 127.0.0.1 回环地址使用。"
            "请点击「高级 → 继续前往 127.0.0.1（不安全）」，"
            "然后输入 Moonlight 显示的 4 位 PIN 码。",
        )

    def resume_pairing(self) -> None:
        """服务启动成功后恢复按钮并打开配对页。"""
        self.reset_pairing_state()
        self.open_pairing_page()

    def pairing_failed(self, message: str) -> None:
        self.reset_pairing_state()
        QMessageBox.warning(
            self,
            "启动 Sunshine 服务失败",
            f"{message}\n\n"
            "可点击右上角「排障诊断」查看服务、端口与日志详情；"
            "若提示 sunshinesvc.exe 缺失，请回到 Step 1 点击「重新修复安装」。",
        )

    def reset_pairing_state(self) -> None:
        if self._summary is None:
            self.pairing_label.setText("—")
            self.pair_button.setEnabled(False)
            return
        self.pairing_label.setText(self._summary.pairing_url or "—")
        self.pair_button.setEnabled(bool(self._summary.pairing_url))

    # ------------------------------------------------------------------
    # 结果与文案
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
        QTimer.singleShot(1500, lambda: self.copy_button.setText("复制 PC 地址"))

    def _render_tips(self, summary: SetupSummary) -> None:
        platforms = set(summary.plan.platforms)
        android = bool(platforms & {"android_tablet", "android_phone"})
        apple = bool(platforms & {"ipados", "ios"})
        self.artemis_button.setVisible(android)
        self.general_tips.setHtml(self._build_general_tips(summary))
        self.android_tips.setHtml(self._build_android_tips(summary))
        self.apple_tips.setHtml(self._build_apple_tips(summary))
        if apple and not android:
            self.platform_tabs.setCurrentIndex(1)
        else:
            self.platform_tabs.setCurrentIndex(0)

    @staticmethod
    def _style() -> str:
        tokens = active_tokens()
        return (
            "<style>"
            "body {"
            " font-family: 'Microsoft YaHei UI';"
            " font-size: 12px;"
            f" color: {tokens.text};"
            "}"
            "h3 { margin: 10px 0 6px 0; font-size: 14px; }"
            "li { margin-bottom: 5px; }"
            ".muted {"
            f" color: {tokens.muted};"
            "}"
            "a {"
            f" color: {tokens.primary};"
            "}"
            "ul { margin-top: 4px; }"
            "</style>"
        )

    def _build_general_tips(self, summary: SetupSummary) -> str:
        device_items = "".join(
            f"<li>{html.escape(item.label or '设备')}："
            f"{item.width}×{item.height} @ {item.refresh_hz}Hz，"
            f"Windows 缩放建议 <b>{item.windows_scaling_percent}%</b></li>"
            for item in summary.plan.monitors
        )
        pairing = html.escape(
            summary.pairing_url or "https://127.0.0.1:47990/pin"
        )
        return "".join(
            [
                self._style(),
                "<h3>① 在 Moonlight 添加电脑</h3>",
                "<ol>",
                "<li>打开手机 / 平板上的 Moonlight，点 <b>+</b>，"
                "输入上方「PC 推荐连接地址」。</li>",
                "<li>在电脑上点「打开本机配对页」。如果提示 127.0.0.1 没有响应，"
                "在弹窗中点击 <b>「一键启动服务」</b>。</li>",
                "<li>把 Moonlight 显示的 4 位 PIN 输入到 "
                f"<b>{pairing}</b>。首次证书警告点"
                "「高级 → 继续前往」。</li>",
                "<li>在 Moonlight 点击 <b>Desktop</b> 进入桌面。首次觉得字体小，"
                "在 Windows 桌面右键 → <b>显示设置</b> → 缩放，调成 "
                "<b>150%</b> 即可永久记住。</li>",
                "</ol>",
                "<h3>本次设备与缩放</h3>",
                f"<ul>{device_items}</ul>",
                "<h3>自动分辨率生效条件</h3>",
                "<ul>",
                "<li>客户端 Moonlight 的 <b>优化游戏设置 / Optimize Game Settings</b> "
                "必须保持开启，Sunshine 的固定分辨率或自动切换才会生效。</li>",
                "<li>单设备默认写入 <b>dd_resolution_option=manual</b>，"
                "首次进入虚拟屏即使用 Step 2 选择的分辨率；多设备为 "
                "<b>auto</b>。</li>",
                "</ul>",
            ]
        )

    def _build_android_tips(self, summary: SetupSummary) -> str:
        platforms = set(summary.plan.platforms)
        selected = bool(platforms & {"android_tablet", "android_phone"})
        parts = [self._style()]
        if not selected:
            parts.append(
                "<p class='muted'>本次没有选择安卓设备；以下教程仍可查阅。</p>"
            )
        parts.extend(
            [
                "<h3>移动端客户端推荐（双选项）</h3>",
                "<p><b>1. 首选推荐（尤其适合国产安卓平板）："
                "Artemis（Moonlight 开源增强版）</b></p>",
                "<ul>",
                "<li>下载地址：<a href='"
                + ARTEMIS_URL
                + "'>"
                + ARTEMIS_URL
                + "</a></li>",
                "<li>兼容 Sunshine，支持<b>自定义非 16:9 分辨率</b>，"
                "解码器兼容选项更丰富，能有效避开原版 Moonlight 在国产平板上"
                "选 <b>1920×1200</b> 等分辨率时报「视频解码器初始化失败 / "
                "error -5」的问题。</li>",
                "<li>提供双指缩放、悬浮控制球等更适合平板办公的功能。</li>",
                "</ul>",
                "<p><b>2. 官方原版：Moonlight Android</b></p>",
                "<ul>",
                "<li>下载地址：<a href='"
                + MOONLIGHT_ANDROID_URL
                + "'>"
                + MOONLIGHT_ANDROID_URL
                + "</a></li>",
                "<li>若使用原版并遇到选「本地 / 原生分辨率 / 90 FPS」报 "
                "error -5，且切 HEVC 仍无效，建议直接换用 Artemis。</li>",
                "<li>如果继续使用原版：把分辨率保持 <b>1080p</b>，同时将"
                "电脑虚拟屏设为 <b>1920×1080</b>；或勾选 "
                "<b>「将画面拉伸至全屏」</b>。</li>",
                "</ul>",
                "<h3>① 安装后打开中文菜单设置</h3>",
                "<p>打开 Moonlight 后，点击左上角 <b>齿轮图标（设置）</b>。</p>",
                "<ul>",
                "<li><b>视频分辨率</b>：选 <b>本地（你的屏幕分辨率，例如 "
                "1920×1200）</b>；部分版本显示为 <b>原生</b>，效果相同。"
                "也可选 <b>1080p</b>。</li>",
                "<li><b>视频帧数</b>：先选 <b>60 FPS</b>。</li>",
                "<li><b>视频解码器</b>：选 <b>首选 HEVC</b>"
                "（旧版可能显示 <b>优先使用 HEVC</b>）。</li>",
                "<li><b>视频帧速调节</b>：选 <b>优先视频流畅度</b>"
                "（旧版可能显示 <b>最流畅</b>）。</li>",
                "<li><b>启用 HDR (实验性)</b>：关闭。</li>",
                "<li><b>优化游戏设置</b>：开启。</li>",
                "<li>勾选 <b>「将触控屏作为触控板使用」</b>。</li>",
                "<li>外接键鼠时，可在输入设置中开启 "
                "<b>「捕获系统快捷键」</b>（不同版本名称可能略有差异）。</li>",
                "<li>退出桌面：使用系统 <b>侧滑返回手势</b>。</li>",
                "</ul>",
                "<p class='muted'>帧数不要一上来选 90 FPS；部分安卓芯片的 "
                "H.264 硬解会初始化失败。</p>",
                "<h3>② 16:10 入门平板 / 骁龙 680 兼容模式</h3>",
                "<ul>",
                "<li>在 Step 2 勾选 <b>「安卓 16:10 兼容模式」</b>："
                "<b>1680×1050 @ 60Hz</b>，Windows 缩放建议 "
                "<b>125%～150%</b>。</li>",
                "<li>该分辨率低于骁龙 680 等入门芯片的 1080p 硬解上限，"
                "通常不会再出现 <b>error -5</b>。</li>",
                "<li>客户端也选 16:10 自定义分辨率（Artemis 可直接输入），"
                "画面与虚拟屏比例一致，<b>不会出现四面黑边</b>。</li>",
                "</ul>",
                "<h3>③ 报错排雷</h3>",
                "<ul>",
                "<li><b>视频解码器初始化失败 / error -5</b>：先把 "
                "<b>视频帧数</b>设为 <b>60 FPS</b>，再把 <b>视频解码器</b>"
                "切为 <b>首选 HEVC</b>（旧版显示 <b>优先使用 HEVC</b>）。"
                "仍失败时换 Artemis，或使用上面的 16:10 兼容模式。</li>",
                "<li><b>四面黑边 / 切 HEVC 后仍不支持本地分辨率</b>："
                "若客户端选 1080p（16:9）而电脑虚拟屏是 16:10，会出现四面黑边。"
                "方案一：客户端保持 <b>1080p</b>，同时把 PC 虚拟屏设为 "
                "<b>1920×1080</b>；方案二：勾选安卓 Moonlight 的 "
                "<b>「将画面拉伸至全屏」</b>"
                "（部分版本写「拉伸视频以铺满全屏」）。</li>",
                "<li><b>Moonlight 显示 offline / 找不到电脑</b>：确认 PC 与平板在"
                "同一个局域网或同一 Tailscale / 星空组网；确认 Windows 服务 "
                "<b>SunshineService</b> 正在运行、防火墙已放行 Sunshine；"
                "PC 地址优先填有线 IPv4，<b>不要填 localhost</b>。</li>",
                "</ul>",
            ]
        )
        return "".join(parts)

    def _build_apple_tips(self, summary: SetupSummary) -> str:
        platforms = set(summary.plan.platforms)
        selected = bool(platforms & {"ipados", "ios"})
        parts = [self._style()]
        if not selected:
            parts.append(
                "<p class='muted'>本次没有选择 iPad / iPhone；以下教程仍可查阅。</p>"
            )
        parts.extend(
            [
                "<h3>① 从 App Store 安装 Moonlight</h3>",
                "<p>在 App Store 搜索 <b>Moonlight</b>"
                "（Moonlight Game Streaming Project 发布）。"
                "iPhone 端 Moonlight 不支持 120Hz，帧率选 60 FPS 即可。</p>",
                "<h3>② 英文菜单设置</h3>",
                "<p>打开 Moonlight 后，点击左上角 <b>Settings</b>。</p>",
                "<ul>",
                "<li><b>Resolution</b>：选 <b>Full</b> 或 <b>Custom</b>"
                "（Custom 填本机预设宽高）。</li>",
                "<li><b>Frame Rate</b>：选 <b>60 FPS</b>。</li>",
                "<li><b>Frame Pacing Preference</b>：选 "
                "<b>Smoothest Video</b>。</li>",
                "<li><b>On-Screen Controls</b>：选 <b>Off</b>。</li>",
                "<li><b>HDR</b>：选 <b>No</b>。</li>",
                "<li><b>Optimize Game Settings</b>：保持 <b>Yes</b>。</li>",
                "</ul>",
                "<h3>③ iPad 外接蓝牙键鼠必做</h3>",
                "<ul>",
                "<li>关闭 iPad <b>「台前调度」</b>，避免鼠标移动受限或误触左侧小窗。</li>",
                "<li>打开 iPad「设置 → 通用 → 触控板与鼠标」，关闭 "
                "<b>「自然滚动」</b>。</li>",
                "<li>关闭 <b>「辅助触控」</b>，避免出现双光标。</li>",
                "<li>退出桌面：从屏幕 <b>最左边缘向右滑</b>。</li>",
                "</ul>",
                "<h3>④ 常见问题</h3>",
                "<ul>",
                "<li><b>首次打开配对页证书警告</b>：这是 127.0.0.1 本机"
                "自签名证书，点「高级 → 继续前往」即可。</li>",
                "<li><b>Moonlight 显示 offline</b>：确认 PC 与 iPad 在同一"
                "局域网或同一组网；确认 SunshineService 正在运行；"
                "PC 地址填有线 IPv4，不要填 localhost。</li>",
                "<li><b>能连上但黑屏</b>：回到 Step 3 日志确认虚拟屏 "
                "device_id 已写入；必要时重新执行配置。</li>",
                "<li><b>画面卡顿</b>：优先有线网络或 5GHz Wi-Fi，帧数先保持 "
                "60 FPS，再尝试 HEVC。</li>",
                "</ul>",
            ]
        )
        if summary.device_id:
            parts.append(
                f"<p class='muted'>已写入虚拟屏 device_id："
                f"{html.escape(summary.device_id)}</p>"
            )
        return "".join(parts)
