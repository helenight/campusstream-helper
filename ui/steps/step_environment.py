"""Step 1：环境自检。"""

from __future__ import annotations

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from core.privileges import is_admin
from ui.widgets import (
    Banner,
    Card,
    InfoRow,
    StatusPill,
    heading,
    make_button,
    muted_label,
)
from ui.workers import EnvironmentData

SUNSHINE_URL = "https://github.com/LizardByte/Sunshine"
VDD_URL = "https://github.com/VirtualDrivers/Virtual-Display-Driver"


class EnvironmentStep(QWidget):
    """展示环境检测结果，并发出修复 / 重新检测请求。"""

    refresh_requested = Signal()
    fix_ipv6_requested = Signal(list)
    relaunch_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._data: EnvironmentData | None = None
        self._dry_run = False
        self._disabled_ipv6: list[str] = []
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(14)

        self.mode_banner = Banner(
            "正在检测系统环境，请稍候…",
            tone="info",
            title="环境状态",
        )
        layout.addWidget(self.mode_banner)

        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(14)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)

        self.system_card = Card()
        self.system_pill = StatusPill("检测中", "muted")
        self.system_rows = self._build_card(
            self.system_card,
            "权限与运行模式",
            self.system_pill,
            ("当前权限", "运行模式", "电源方案", "交流电源"),
        )

        self.sunshine_card = Card()
        self.sunshine_pill = StatusPill("检测中", "muted")
        self.sunshine_rows = self._build_card(
            self.sunshine_card,
            "Sunshine 服务端",
            self.sunshine_pill,
            ("安装目录", "版本", "服务状态", "配置文件"),
        )

        self.vdd_card = Card()
        self.vdd_pill = StatusPill("检测中", "muted")
        self.vdd_rows = self._build_card(
            self.vdd_card,
            "虚拟显示器驱动",
            self.vdd_pill,
            ("配置文件", "XML 结构", "虚拟屏数量", "分辨率条目", "驱动版本"),
        )

        self.network_card = Card()
        self.network_pill = StatusPill("检测中", "muted")
        self.network_rows = self._build_card(
            self.network_card,
            "网络与防火墙",
            self.network_pill,
            ("网络场景", "有线 IPv4", "无线 IPv4", "全局 IPv6", "推荐地址", "防火墙"),
        )

        grid.addWidget(self.system_card, 0, 0)
        grid.addWidget(self.sunshine_card, 0, 1)
        grid.addWidget(self.vdd_card, 1, 0)
        grid.addWidget(self.network_card, 1, 1)
        layout.addLayout(grid)

        self.warning_banner = Banner("", tone="warning", title="需要注意")
        self.warning_banner.setVisible(False)
        layout.addWidget(self.warning_banner)

        actions = QHBoxLayout()
        actions.setSpacing(10)
        self.refresh_button = make_button("重新检测", "primary")
        self.refresh_button.clicked.connect(self.refresh_requested.emit)
        self.fix_button = make_button("修复 IPv6 绑定", "secondary")
        self.fix_button.setVisible(False)
        self.fix_button.clicked.connect(self._emit_fix)
        self.relaunch_button = make_button("以管理员身份重启", "secondary")
        self.relaunch_button.setVisible(not is_admin())
        self.relaunch_button.clicked.connect(self.relaunch_requested.emit)

        actions.addWidget(self.refresh_button)
        actions.addWidget(self.fix_button)
        actions.addWidget(self.relaunch_button)
        actions.addStretch()
        actions.addWidget(self._link_button("Sunshine 官网", SUNSHINE_URL))
        actions.addWidget(self._link_button("VDD 项目页", VDD_URL))
        layout.addLayout(actions)

        self.status_label = muted_label("首次检测会自动开始。")
        layout.addWidget(self.status_label)
        layout.addStretch()
        scroll.setWidget(content)
        root.addWidget(scroll)

    def _build_card(
        self,
        card: Card,
        title: str,
        pill: StatusPill,
        row_names: tuple[str, ...],
    ) -> dict[str, InfoRow]:
        header = QHBoxLayout()
        header.addWidget(heading(title))
        header.addStretch()
        header.addWidget(pill)
        card.add_layout(header)
        rows: dict[str, InfoRow] = {}
        for name in row_names:
            row = InfoRow(name)
            rows[name] = row
            card.add(row)
        return rows

    def _link_button(self, text: str, url: str):
        button = make_button(text, "link")
        button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(url)))
        return button

    def set_loading(self, loading: bool) -> None:
        self.refresh_button.setEnabled(not loading)
        if loading:
            self.status_label.setText("正在读取网卡、Sunshine、VDD 与电源状态…")
            self.mode_banner.set_text(
                "正在检测系统环境，请稍候…",
                tone="info",
                title="环境状态",
            )

    def set_mode(self, dry_run: bool) -> None:
        self._dry_run = dry_run
        if self._data is not None:
            self.set_data(self._data)
        else:
            self._render_mode("检测中", "muted")

    def refresh_theme(self) -> None:
        """主题切换后重新应用 InfoRow 的动态 tone 颜色。"""
        for rows in (
            self.system_rows,
            self.sunshine_rows,
            self.vdd_rows,
            self.network_rows,
        ):
            for row in rows.values():
                row.refresh_theme()

    def set_error(self, message: str) -> None:
        self.refresh_button.setEnabled(True)
        self.status_label.setText("检测失败，请查看下方信息后重试。")
        self.mode_banner.set_text(message, tone="danger", title="环境检测失败")

    def set_data(self, data: EnvironmentData) -> None:
        self._data = data
        self.refresh_button.setEnabled(True)
        self.status_label.setText("环境检测完成。修改系统后点击「重新检测」。")

        self._render_mode("管理员已就绪", "success")
        self.system_rows["当前权限"].set_value("管理员" if is_admin() else "普通用户")
        self.system_rows["当前权限"].set_tone("success" if is_admin() else "warning")
        self.system_rows["运行模式"].set_value(
            "Dry-Run（只检测 / 不写入）" if self._dry_run else "真实配置"
        )
        self.system_rows["运行模式"].set_tone("info" if self._dry_run else "muted")
        power = data.power
        if power is None:
            self.system_rows["电源方案"].set_value("读取失败")
            self.system_rows["交流电源"].set_value("未知")
        else:
            self.system_rows["电源方案"].set_value(power.scheme_name)
            self.system_rows["交流电源"].set_value(
                f"睡眠 {self._format_seconds(power.ac_sleep_seconds)} · "
                f"关屏 {self._format_seconds(power.ac_display_off_seconds)}"
            )

        sunshine = data.sunshine
        if not sunshine.installed:
            self.sunshine_pill.set_status("未安装", "danger")
        elif sunshine.service_state == "running":
            self.sunshine_pill.set_status("运行中", "success")
        else:
            self.sunshine_pill.set_status("服务未运行", "warning")
        self.sunshine_rows["安装目录"].set_value(str(sunshine.install_dir or "未检测到"))
        self.sunshine_rows["版本"].set_value(sunshine.version or "未知")
        self.sunshine_rows["服务状态"].set_value(
            f"{sunshine.service_name} · {sunshine.service_state}"
        )
        self.sunshine_rows["配置文件"].set_value(
            str(sunshine.config_path or "未找到 sunshine.conf")
        )

        vdd = data.vdd
        if not vdd.installed:
            self.vdd_pill.set_status("未安装", "danger")
        elif vdd.settings_path is None:
            self.vdd_pill.set_status("缺少配置", "warning")
        else:
            self.vdd_pill.set_status("就绪", "success")
        self.vdd_rows["配置文件"].set_value(
            str(vdd.settings_path or "未找到 vdd_settings.xml")
        )
        self.vdd_rows["XML 结构"].set_value(vdd.schema)
        self.vdd_rows["虚拟屏数量"].set_value(str(vdd.configured_monitor_count))
        self.vdd_rows["分辨率条目"].set_value(str(vdd.configured_resolution_count))
        self.vdd_rows["驱动版本"].set_value(vdd.driver_version or "未知")

        network = data.network
        scenario_map = {
            "ethernet_ready": ("有线网络就绪", "success"),
            "wifi_only": ("仅无线网络", "warning"),
            "offline": ("未联网", "danger"),
        }
        scenario_text, scenario_tone = scenario_map.get(
            network.scenario, ("未知", "muted")
        )
        self.network_pill.set_status(scenario_text, scenario_tone)
        self.network_rows["网络场景"].set_value(scenario_text)
        self.network_rows["有线 IPv4"].set_value(network.ethernet_ipv4 or "未检测到")
        self.network_rows["无线 IPv4"].set_value(network.wlan_ipv4 or "未检测到")
        self.network_rows["全局 IPv6"].set_value(network.preferred_ipv6 or "未检测到")
        self.network_rows["推荐地址"].set_value(
            network.recommended_client_ip or "未获取到可用地址"
        )
        self.network_rows["推荐地址"].set_tone(
            "success" if network.recommended_client_ip else "warning"
        )
        self.network_rows["防火墙"].set_value(
            "已存在 Sunshine 放行规则"
            if network.firewall_rule_present
            else "尚未添加入站规则"
        )
        self.network_rows["防火墙"].set_tone(
            "success" if network.firewall_rule_present else "warning"
        )

        warnings = list(network.warnings)
        if not is_admin() and not self._dry_run:
            warnings.append(
                "当前不是管理员。真实配置会被系统拒绝；建议点击「以管理员身份重启」或先启用 Dry-Run。"
            )
        if not sunshine.installed:
            warnings.append("未检测到 Sunshine，请先从官网安装并启动服务。")
        if not vdd.installed:
            warnings.append("未检测到 Virtual Display Driver，请先安装驱动。")
        if data.error:
            warnings.append(data.error)
        if warnings:
            self.warning_banner.set_text(
                "\n".join(f"• {item}" for item in warnings),
                tone="warning",
                title="需要注意",
            )
            self.warning_banner.setVisible(True)
        else:
            self.warning_banner.setVisible(False)

        disabled = [
            adapter.name
            for adapter in network.adapters
            if adapter.kind in {"ethernet", "wifi"}
            and adapter.is_up
            and not adapter.ipv6_binding_enabled
        ]
        self._disabled_ipv6 = disabled
        if disabled:
            self.fix_button.setText("修复 IPv6 绑定（" + "、".join(disabled) + "）")
            self.fix_button.setVisible(True)
        else:
            self.fix_button.setVisible(False)

    def _render_mode(self, ready_text: str = "管理员已就绪", tone: str = "success") -> None:
        if self._dry_run:
            self.mode_banner.set_text(
                "Dry-Run 模式已开启：检测照常执行，但所有配置写入只记录、不落盘。",
                tone="info",
                title="模拟运行",
            )
            self.system_pill.set_status("Dry-Run", "info")
        elif is_admin():
            self.mode_banner.set_text(
                "管理员权限已就绪，可以执行真实的系统配置。",
                tone="success",
                title="权限正常",
            )
            self.system_pill.set_status(ready_text, tone)
        else:
            self.mode_banner.set_text(
                "当前以普通权限运行。真实配置需要管理员权限；可重启提权，或开启 Dry-Run 先体验流程。",
                tone="danger",
                title="权限不足",
            )
            self.system_pill.set_status("普通权限", "warning")

    def _emit_fix(self) -> None:
        if self._disabled_ipv6:
            self.fix_ipv6_requested.emit(list(self._disabled_ipv6))

    @staticmethod
    def _format_seconds(seconds: int) -> str:
        if seconds == 0:
            return "永不"
        if seconds < 0:
            return "未知"
        return f"{max(1, (seconds + 30) // 60)} 分钟"
