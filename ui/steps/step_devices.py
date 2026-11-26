"""Step 2：客户端平台与设备分辨率定制。"""

from __future__ import annotations

import json

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QRadioButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from core.plan import SetupPlan
from core.vdd import (
    DEFAULT_PRESETS_PATH,
    DevicePreset,
    MonitorSpec,
    Platform,
    load_device_presets,
)
from ui.widgets import (
    Banner,
    Card,
    StatusPill,
    heading,
    muted_label,
)
from ui.workers import EnvironmentData

PLATFORM_TABS: tuple[tuple[Platform, str], ...] = (
    ("ipados", "iPadOS"),
    ("android_tablet", "Android 平板"),
    ("android_phone", "Android 手机"),
    ("ios", "iPhone"),
)


class DevicesStep(QWidget):
    """勾选一个或多个设备，并配置安装选项。"""

    selection_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._presets = load_device_presets()
        self._items: list[tuple[QListWidgetItem, DevicePreset]] = []
        self._lists: dict[str, QListWidget] = {}
        self._mode_touched = False
        self._custom = self._load_custom_config()
        self._build_ui()
        self._populate_devices()
        self._on_selection_changed()

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

        self.ready_banner = Banner("", tone="warning", title="安装状态提醒")
        self.ready_banner.setVisible(False)
        layout.addWidget(self.ready_banner)

        device_card = Card()
        device_header = QHBoxLayout()
        device_header.addWidget(heading("选择客户端设备"))
        device_header.addStretch()
        self.count_pill = StatusPill("未选择", "muted")
        device_header.addWidget(self.count_pill)
        device_card.add_layout(device_header)
        device_card.add(
            muted_label(
                "可跨标签页多选，例如同时选择 iPad 与 Android 手机。"
                "高刷机型默认使用其最高刷新率。"
            )
        )
        self.tabs = QTabWidget()
        self.tabs.setMinimumHeight(330)
        device_card.add(self.tabs)
        layout.addWidget(device_card)

        custom_card = Card()
        custom_header = QHBoxLayout()
        custom_header.addWidget(heading("自定义设备"))
        custom_header.addStretch()
        self.custom_check = QCheckBox("添加自定义分辨率")
        custom_header.addWidget(self.custom_check)
        custom_card.add_layout(custom_header)

        custom_row = QHBoxLayout()
        custom_row.setSpacing(8)
        self.custom_width = QSpinBox()
        self.custom_width.setRange(
            int(self._custom.get("width_range", [640, 7680])[0]),
            int(self._custom.get("width_range", [640, 7680])[1]),
        )
        self.custom_width.setValue(1920)
        self.custom_height = QSpinBox()
        self.custom_height.setRange(
            int(self._custom.get("height_range", [480, 4320])[0]),
            int(self._custom.get("height_range", [480, 4320])[1]),
        )
        self.custom_height.setValue(1080)
        self.custom_refresh = QComboBox()
        for refresh in self._custom.get("refresh_options", [60, 90, 120, 144]):
            self.custom_refresh.addItem(f"{refresh} Hz", int(refresh))
        default_refresh = int(self._custom.get("default_refresh_hz", 60))
        index = self.custom_refresh.findData(default_refresh)
        if index >= 0:
            self.custom_refresh.setCurrentIndex(index)

        custom_row.addWidget(QLabel("宽"))
        custom_row.addWidget(self.custom_width)
        custom_row.addWidget(QLabel("× 高"))
        custom_row.addWidget(self.custom_height)
        custom_row.addWidget(QLabel("刷新率"))
        custom_row.addWidget(self.custom_refresh)
        custom_row.addStretch()
        custom_card.add_layout(custom_row)
        custom_card.add(
            muted_label(
                f"自定义分辨率建议 Windows 缩放 {int(self._custom.get('windows_scaling_percent', 150))}%。"
                "不确定时优先使用 1920×1080。"
            )
        )
        self.custom_fields = (self.custom_width, self.custom_height, self.custom_refresh)
        self._on_custom_toggled(False)
        self.custom_check.toggled.connect(self._on_custom_toggled)
        self.custom_check.toggled.connect(self._on_selection_changed)
        for widget in self.custom_fields:
            if isinstance(widget, QSpinBox):
                widget.valueChanged.connect(self._on_selection_changed)
            else:
                widget.currentIndexChanged.connect(self._on_selection_changed)
        layout.addWidget(custom_card)

        options_card = Card()
        options_card.add(heading("连接与安装选项"))
        options_grid = QGridLayout()
        options_grid.setHorizontalSpacing(16)
        options_grid.setVerticalSpacing(10)
        options_grid.addWidget(QLabel("地址族"), 0, 0)
        self.address_combo = QComboBox()
        self.address_combo.addItem("IPv4（推荐，最稳定）", "ipv4")
        self.address_combo.addItem("IPv4 + IPv6（双栈）", "both")
        self.address_combo.addItem("IPv6（仅 IPv6）", "ipv6")
        self.address_combo.currentIndexChanged.connect(
            lambda *_: self.selection_changed.emit()
        )
        options_grid.addWidget(self.address_combo, 0, 1)

        options_grid.addWidget(QLabel("分辨率策略"), 1, 0)
        mode_row = QHBoxLayout()
        self.mode_group = QButtonGroup(self)
        self.radio_manual = QRadioButton("固定为所选分辨率（单设备推荐）")
        self.radio_auto = QRadioButton("跟随客户端自动切换（多设备推荐）")
        self.mode_group.addButton(self.radio_manual)
        self.mode_group.addButton(self.radio_auto)
        self.radio_manual.setChecked(True)
        self.mode_group.buttonClicked.connect(self._on_mode_clicked)
        mode_row.addWidget(self.radio_manual)
        mode_row.addWidget(self.radio_auto)
        mode_row.addStretch()
        options_grid.addLayout(mode_row, 1, 1)
        options_grid.addWidget(
            muted_label(
                "单设备会写入 dd_resolution_option=manual 和所选分辨率；"
                "多设备建议 auto，由 Moonlight 客户端请求决定。"
            ),
            2,
            0,
            1,
            2,
        )

        self.firewall_check = QCheckBox("添加 Windows 防火墙入站放行规则")
        self.firewall_check.setChecked(True)
        self.power_check = QCheckBox("设置电源保活（插电永不休眠 / 10 分钟后关屏）")
        self.power_check.setChecked(True)
        self.reload_check = QCheckBox("配置后重载虚拟显示器驱动")
        self.reload_check.setChecked(True)
        self.reload_check.setToolTip("重载时屏幕可能短暂闪烁，这是正常现象。")
        options_grid.addWidget(self.firewall_check, 3, 0, 1, 2)
        options_grid.addWidget(self.power_check, 4, 0, 1, 2)
        options_grid.addWidget(self.reload_check, 5, 0, 1, 2)
        options_card.add_layout(options_grid)
        layout.addWidget(options_card)

        summary_card = Card()
        summary_header = QHBoxLayout()
        summary_header.addWidget(heading("本次配置"))
        summary_header.addStretch()
        self.summary_pill = StatusPill("未选择", "muted")
        summary_header.addWidget(self.summary_pill)
        summary_card.add_layout(summary_header)
        self.summary_label = muted_label("请至少勾选一个设备分辨率。")
        summary_card.add(self.summary_label)
        layout.addWidget(summary_card)
        layout.addStretch()

        scroll.setWidget(content)
        root.addWidget(scroll)

    def _populate_devices(self) -> None:
        for platform, title in PLATFORM_TABS:
            list_widget = QListWidget()
            list_widget.setObjectName("DeviceList")
            list_widget.setSpacing(2)
            list_widget.itemChanged.connect(self._on_selection_changed)
            self._lists[platform] = list_widget

            for preset in self._presets:
                if preset.platform != platform:
                    continue
                item = QListWidgetItem(self._preset_text(preset))
                item.setFlags(
                    Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable
                )
                item.setCheckState(Qt.CheckState.Unchecked)
                item.setData(Qt.ItemDataRole.UserRole, preset.id)
                item.setSizeHint(QSize(0, 84))
                item.setToolTip(preset.notes)
                list_widget.addItem(item)
                self._items.append((item, preset))

            self.tabs.addTab(list_widget, title)

    @staticmethod
    def _preset_text(preset: DevicePreset) -> str:
        refreshes = "/".join(str(value) for value in preset.refresh_options)
        return (
            f"{preset.label}\n"
            f"{preset.width}×{preset.height} · {refreshes}Hz · "
            f"推荐缩放 {preset.windows_scaling_percent}%\n"
            f"{preset.notes}"
        )

    @staticmethod
    def _load_custom_config() -> dict:
        try:
            payload = json.loads(DEFAULT_PRESETS_PATH.read_text(encoding="utf-8"))
            custom = payload.get("custom_device")
            return custom if isinstance(custom, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    # ------------------------------------------------------------------
    # 选择状态
    # ------------------------------------------------------------------

    def set_environment(self, data: EnvironmentData) -> None:
        issues: list[str] = []
        sunshine = data.sunshine
        vdd = data.vdd
        if sunshine is None or not sunshine.installed:
            issues.append("未检测到 Sunshine，Step 3 将无法写入服务端配置。")
        if vdd is None or not vdd.installed or vdd.settings_path is None:
            issues.append("未检测到 Virtual Display Driver 或 vdd_settings.xml。")
        if issues:
            self.ready_banner.set_text(
                "\n".join(f"• {item}" for item in issues),
                tone="warning",
                title="安装状态提醒",
            )
            self.ready_banner.setVisible(True)
        else:
            self.ready_banner.setVisible(False)

    def selected_monitors(self) -> tuple[MonitorSpec, ...]:
        monitors: list[MonitorSpec] = []
        for item, preset in self._items:
            if item.checkState() == Qt.CheckState.Checked:
                monitors.append(preset.to_monitor())
        if self.custom_check.isChecked():
            monitors.append(
                MonitorSpec(
                    width=self.custom_width.value(),
                    height=self.custom_height.value(),
                    refresh_hz=int(self.custom_refresh.currentData() or 60),
                    label="自定义设备",
                    source="custom",
                    windows_scaling_percent=int(
                        self._custom.get("windows_scaling_percent", 150)
                    ),
                )
            )
        return tuple(monitors)

    def selected_platforms(self) -> tuple[Platform, ...]:
        platforms: list[Platform] = []
        for item, preset in self._items:
            if item.checkState() == Qt.CheckState.Checked and preset.platform not in platforms:
                platforms.append(preset.platform)
        return tuple(platforms)

    def build_plan(self, dry_run: bool) -> SetupPlan:
        monitors = self.selected_monitors()
        if not monitors:
            raise ValueError("请至少勾选一个设备分辨率")
        return SetupPlan(
            platforms=self.selected_platforms(),
            monitors=monitors,
            address_family=self.address_combo.currentData(),
            resolution_mode=(
                "auto" if self.radio_auto.isChecked() else "manual"
            ),
            enable_firewall=self.firewall_check.isChecked(),
            apply_power_profile=self.power_check.isChecked(),
            reload_driver=self.reload_check.isChecked(),
            dry_run=dry_run,
        )

    def is_valid(self) -> bool:
        return bool(self.selected_monitors())

    def validation_message(self) -> str:
        if not self.selected_monitors():
            return "请至少勾选一个设备分辨率，或添加自定义分辨率。"
        return ""

    def _on_custom_toggled(self, enabled: bool) -> None:
        for widget in self.custom_fields:
            widget.setEnabled(enabled)

    def _on_mode_clicked(self, _button) -> None:
        self._mode_touched = True

    def _on_selection_changed(self, *_args) -> None:
        if not self._mode_touched:
            count = len(self.selected_monitors())
            if count > 1:
                self.radio_auto.setChecked(True)
            else:
                self.radio_manual.setChecked(True)
        self._update_summary()
        self.selection_changed.emit()

    def _update_summary(self) -> None:
        monitors = self.selected_monitors()
        if not monitors:
            self.count_pill.set_status("未选择", "muted")
            self.summary_pill.set_status("待选择", "warning")
            self.summary_label.setText("请至少勾选一个设备分辨率。")
            return

        self.count_pill.set_status(f"{len(monitors)} 个分辨率", "success")
        self.summary_pill.set_status("已就绪", "success")
        lines = [
            f"• {item.label or '设备'} — {item.width}×{item.height}@{item.refresh_hz}Hz，"
            f"Windows 缩放建议 {item.windows_scaling_percent}%"
            for item in monitors
        ]
        self.summary_label.setText("\n".join(lines))
