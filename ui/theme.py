"""浅色 / 深色主题：QSS、调色板、系统探测与运行时切换。"""

from __future__ import annotations

import sys
from dataclasses import asdict, dataclass
from string import Template
from typing import Callable, Literal

from PySide6.QtCore import QObject, QSettings, Qt, Signal
from PySide6.QtGui import QAction, QActionGroup, QColor, QPalette
from PySide6.QtWidgets import QApplication, QMenu, QToolButton, QWidget

from core.common import resource_path

ThemeName = Literal["light", "dark"]
ThemeMode = Literal["system", "light", "dark"]

_MODES: tuple[ThemeMode, ...] = ("system", "light", "dark")
_SETTINGS_KEY = "ui/theme_mode"


@dataclass(frozen=True, slots=True)
class ThemeTokens:
    """一套主题用到的全部颜色；除本模块外不允许出现颜色字面量。"""

    bg: str
    surface: str
    surface_alt: str
    sidebar: str
    sidebar_active: str
    sidebar_index: str
    sidebar_text: str
    sidebar_muted: str
    sidebar_title: str
    text: str
    muted: str
    border: str
    primary: str
    primary_hover: str
    primary_soft: str
    on_primary: str
    success: str
    success_bg: str
    success_fg: str
    warning: str
    warning_bg: str
    warning_fg: str
    danger: str
    danger_bg: str
    danger_fg: str
    info: str
    info_bg: str
    info_fg: str
    log_bg: str
    log_text: str
    scrollbar: str
    scrollbar_hover: str
    disabled_bg: str
    disabled_text: str
    focus: str


LIGHT_TOKENS = ThemeTokens(
    bg="#F4F7FB",
    surface="#FFFFFF",
    surface_alt="#F8FAFC",
    sidebar="#0F172A",
    sidebar_active="#172554",
    sidebar_index="#1E293B",
    sidebar_text="#E2E8F0",
    sidebar_muted="#94A3B8",
    sidebar_title="#FFFFFF",
    text="#0F172A",
    muted="#5B6B80",
    border="#E2E8F0",
    primary="#2563EB",
    primary_hover="#1D4ED8",
    primary_soft="#DBEAFE",
    on_primary="#FFFFFF",
    success="#16A34A",
    success_bg="#DCFCE7",
    success_fg="#166534",
    warning="#D97706",
    warning_bg="#FEF3C7",
    warning_fg="#92400E",
    danger="#DC2626",
    danger_bg="#FEE2E2",
    danger_fg="#991B1B",
    info="#2563EB",
    info_bg="#DBEAFE",
    info_fg="#1E40AF",
    log_bg="#0B1220",
    log_text="#D7E2F0",
    scrollbar="#CBD5E1",
    scrollbar_hover="#94A3B8",
    disabled_bg="#F1F5F9",
    disabled_text="#94A3B8",
    focus="#93C5FD",
)

DARK_TOKENS = ThemeTokens(
    bg="#0B1220",
    surface="#111C2E",
    surface_alt="#16233A",
    sidebar="#060C17",
    sidebar_active="#172554",
    sidebar_index="#1E293B",
    sidebar_text="#E5EDF7",
    sidebar_muted="#94A3B8",
    sidebar_title="#FFFFFF",
    text="#E5EDF7",
    muted="#A8B6C9",
    border="#24344D",
    primary="#2563EB",
    primary_hover="#1D4ED8",
    primary_soft="#172554",
    on_primary="#FFFFFF",
    success="#22C55E",
    success_bg="#052E16",
    success_fg="#86EFAC",
    warning="#F59E0B",
    warning_bg="#3A2404",
    warning_fg="#FCD34D",
    danger="#EF4444",
    danger_bg="#3B0A0A",
    danger_fg="#FCA5A5",
    info="#60A5FA",
    info_bg="#102A4C",
    info_fg="#BFDBFE",
    log_bg="#050A14",
    log_text="#D7E2F0",
    scrollbar="#334155",
    scrollbar_hover="#475569",
    disabled_bg="#0F1A2B",
    disabled_text="#6B7C93",
    focus="#60A5FA",
)


_QSS_TEMPLATE = Template(
    """
* {
    font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
    font-size: 13px;
    color: $text;
}
QMainWindow, QWidget#Root, QDialog, QMessageBox, QFileDialog {
    background: $bg;
    color: $text;
}
QDialog QLabel, QMessageBox QLabel, QFileDialog QLabel {
    color: $text;
}
QStatusBar {
    background: $surface;
    border-top: 1px solid $border;
}
QStatusBar QLabel {
    color: $muted;
}

/* ---------- 侧栏 ---------- */
QWidget#Sidebar {
    background: $sidebar;
}
QWidget#Sidebar QLabel {
    color: $sidebar_text;
}
QLabel#AppTitle {
    font-size: 19px;
    font-weight: 700;
    color: $sidebar_title;
}
QLabel#AppSubtitle, QLabel#SidebarFootnote, QLabel#AdminPill {
    font-size: 11px;
    color: $sidebar_muted;
}
QWidget#SidebarStep {
    background: transparent;
    border-radius: 10px;
}
QWidget#SidebarStep[state="current"] {
    background: $sidebar_active;
}
QLabel#StepIndex {
    min-width: 24px;
    max-width: 24px;
    min-height: 24px;
    max-height: 24px;
    border-radius: 12px;
    background: $sidebar_index;
    color: $sidebar_muted;
    font-size: 11px;
    font-weight: 700;
    qproperty-alignment: AlignCenter;
}
QWidget#SidebarStep[state="current"] QLabel#StepIndex {
    background: $primary;
    color: $on_primary;
}
QWidget#SidebarStep[state="done"] QLabel#StepIndex {
    background: $success_bg;
    color: $success_fg;
}
QLabel#StepTitle {
    font-size: 13px;
    font-weight: 600;
    color: $sidebar_text;
}
QLabel#StepSubtitle {
    font-size: 11px;
    color: $sidebar_muted;
}
QWidget#SidebarStep[state="current"] QLabel#StepTitle {
    color: $sidebar_title;
}
QCheckBox#DryRunToggle {
    color: $sidebar_text;
    font-size: 12px;
}

/* ---------- 卡片与文字 ---------- */
QFrame#Card {
    background: $surface;
    border: 1px solid $border;
    border-radius: 12px;
}
QLabel#CardTitle {
    font-size: 15px;
    font-weight: 700;
    color: $text;
}
QLabel#SectionTitle {
    font-size: 13px;
    font-weight: 700;
    color: $text;
}
QLabel#Muted, QLabel#Hint {
    color: $muted;
    font-size: 12px;
}
QLabel#HeroValue {
    font-size: 30px;
    font-weight: 700;
    color: $text;
}
QLabel#PageTitle {
    font-size: 24px;
    font-weight: 700;
    color: $text;
}
QLabel#PageSubtitle {
    font-size: 13px;
    color: $muted;
}

/* ---------- 状态胶囊 / 徽章 ---------- */
QLabel#StatusPill, QLabel#StatusBadge {
    padding: 3px 10px;
    border-radius: 9px;
    font-size: 11px;
    font-weight: 700;
}
QLabel#StatusBadge {
    font-size: 12px;
}
QLabel#StatusPill[tone="success"], QLabel#StatusBadge[state="ready"] {
    background: $success_bg;
    color: $success_fg;
}
QLabel#StatusPill[tone="warning"], QLabel#StatusBadge[state="warning"] {
    background: $warning_bg;
    color: $warning_fg;
}
QLabel#StatusPill[tone="danger"], QLabel#StatusBadge[state="failed"] {
    background: $danger_bg;
    color: $danger_fg;
}
QLabel#StatusPill[tone="info"], QLabel#StatusBadge[state="running"] {
    background: $info_bg;
    color: $info_fg;
}
QLabel#StatusPill[tone="muted"], QLabel#StatusBadge[state="pending"] {
    background: $surface_alt;
    color: $muted;
    border: 1px solid $border;
}

/* ---------- 按钮 ---------- */
QPushButton {
    background: $surface_alt;
    color: $text;
    border: 1px solid $border;
    border-radius: 8px;
    padding: 8px 16px;
}
QPushButton:hover {
    border-color: $primary;
}
QPushButton:pressed {
    background: $surface;
}
QPushButton:disabled {
    background: $disabled_bg;
    color: $disabled_text;
    border-color: $border;
}
QPushButton:default {
    border-color: $primary;
}
QPushButton#PrimaryButton {
    background: $primary;
    color: $on_primary;
    border: none;
    border-radius: 8px;
    padding: 9px 18px;
    font-weight: 600;
}
QPushButton#PrimaryButton:hover {
    background: $primary_hover;
}
QPushButton#PrimaryButton:pressed {
    background: $primary_hover;
}
QPushButton#PrimaryButton:disabled {
    background: $disabled_bg;
    color: $disabled_text;
}
QPushButton#SecondaryButton {
    background: $surface;
    color: $text;
    border: 1px solid $border;
    border-radius: 8px;
    padding: 8px 16px;
}
QPushButton#SecondaryButton:hover {
    background: $surface_alt;
    border-color: $primary;
}
QPushButton#SecondaryButton:disabled {
    color: $disabled_text;
    border-color: $border;
}
QPushButton#DangerButton {
    background: $surface;
    color: $danger_fg;
    border: 1px solid $danger;
    border-radius: 8px;
    padding: 8px 16px;
}
QPushButton#DangerButton:hover {
    background: $danger_bg;
}
QPushButton#LinkButton {
    background: transparent;
    color: $primary;
    border: none;
    padding: 4px 2px;
    text-align: left;
}
QPushButton#LinkButton:hover {
    color: $primary_hover;
    text-decoration: underline;
}
QDialogButtonBox QPushButton {
    min-width: 76px;
}
QToolButton {
    background: transparent;
    color: $text;
    border: none;
    border-radius: 6px;
    padding: 4px;
}
QToolButton:hover {
    background: $surface_alt;
}
QToolButton#ThemeSwitcher {
    background: $surface;
    color: $text;
    border: 1px solid $border;
    border-radius: 8px;
    padding: 6px 10px;
    font-size: 12px;
}
QToolButton#ThemeSwitcher:hover {
    background: $surface_alt;
    border-color: $primary;
}
QToolButton#ThemeSwitcher::menu-indicator {
    image: none;
}

/* ---------- 输入控件 ---------- */
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateEdit, QTimeEdit {
    background: $surface;
    color: $text;
    border: 1px solid $border;
    border-radius: 8px;
    padding: 6px 10px;
    min-height: 20px;
    selection-background-color: $primary;
    selection-color: $on_primary;
}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus,
QDateEdit:focus, QTimeEdit:focus {
    border-color: $focus;
}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled,
QDoubleSpinBox:disabled, QDateEdit:disabled, QTimeEdit:disabled {
    background: $disabled_bg;
    color: $disabled_text;
    border-color: $border;
}
QComboBox QAbstractItemView {
    background: $surface;
    color: $text;
    border: 1px solid $border;
    selection-background-color: $primary;
    selection-color: $on_primary;
    outline: none;
}
QCheckBox, QRadioButton {
    spacing: 8px;
    color: $text;
}
QCheckBox:disabled, QRadioButton:disabled {
    color: $disabled_text;
}
QCheckBox::indicator, QRadioButton::indicator {
    width: 16px;
    height: 16px;
    background: $surface;
    border: 1px solid $border;
}
QCheckBox::indicator {
    border-radius: 4px;
}
QRadioButton::indicator {
    border-radius: 8px;
}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {
    border-color: $primary;
}
QCheckBox::indicator:checked {
    background: $primary;
    border-color: $primary;
    image: url("$check_icon");
}
QRadioButton::indicator:checked {
    background: $surface;
    border: 4px solid $primary;
}
QCheckBox::indicator:checked:disabled, QRadioButton::indicator:checked:disabled {
    background: $disabled_bg;
    border-color: $border;
    image: none;
}

/* ---------- 标签页 / 列表 ---------- */
QTabWidget::pane {
    border: none;
    background: transparent;
}
QTabBar::tab {
    background: transparent;
    color: $muted;
    padding: 8px 14px;
    margin-right: 4px;
    border-bottom: 2px solid transparent;
    font-weight: 600;
}
QTabBar::tab:selected {
    color: $primary;
    border-bottom: 2px solid $primary;
}
QTabBar::tab:hover {
    color: $text;
}
QListWidget, QListView, QTreeView, QTableView {
    background: $surface;
    color: $text;
    border: 1px solid $border;
    outline: none;
}
QListWidget#DeviceList {
    background: transparent;
    border: none;
}
QListWidget::item {
    background: $surface;
    color: $text;
    border: 1px solid $border;
    border-radius: 9px;
    padding: 10px 12px;
    margin-bottom: 8px;
}
QListWidget::item:hover {
    border-color: $primary;
    background: $surface_alt;
}
QListWidget::item:selected {
    border-color: $primary;
    background: $primary_soft;
    color: $text;
}
QListView::indicator {
    width: 16px;
    height: 16px;
    background: $surface;
    border: 1px solid $border;
    border-radius: 4px;
}
QListView::indicator:hover {
    border-color: $primary;
}
QListView::indicator:checked {
    background: $primary;
    border-color: $primary;
    image: url("$check_icon");
}
QHeaderView::section {
    background: $surface_alt;
    color: $text;
    border: none;
    border-bottom: 1px solid $border;
    padding: 6px 8px;
}

/* ---------- 进度 / 日志 ---------- */
QProgressBar {
    background: $surface_alt;
    border: 1px solid $border;
    border-radius: 5px;
    height: 10px;
    text-align: center;
    color: transparent;
}
QProgressBar::chunk {
    background: $primary;
    border-radius: 4px;
}
QProgressBar[result="success"]::chunk {
    background: $success;
}
QProgressBar[result="warning"]::chunk {
    background: $warning;
}
QProgressBar[result="failed"]::chunk {
    background: $danger;
}
QPlainTextEdit, QTextEdit {
    background: $surface;
    color: $text;
    border: 1px solid $border;
    border-radius: 8px;
    selection-background-color: $primary;
    selection-color: $on_primary;
}
QPlainTextEdit#LogView {
    background: $log_bg;
    color: $log_text;
    border: 1px solid $border;
    border-radius: 10px;
    padding: 12px;
    selection-background-color: $primary;
    selection-color: $on_primary;
}
QTextBrowser {
    background: transparent;
    color: $text;
    border: none;
}

/* ---------- 滚动区 / 滚动条 ---------- */
QScrollArea {
    border: none;
    background: transparent;
}
QScrollArea > QWidget > QWidget {
    background: transparent;
}
QScrollBar:vertical {
    background: transparent;
    width: 10px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background: $scrollbar;
    border-radius: 4px;
    min-height: 24px;
}
QScrollBar::handle:vertical:hover {
    background: $scrollbar_hover;
}
QScrollBar:horizontal {
    background: transparent;
    height: 10px;
    margin: 0;
}
QScrollBar::handle:horizontal {
    background: $scrollbar;
    border-radius: 4px;
    min-width: 24px;
}
QScrollBar::handle:horizontal:hover {
    background: $scrollbar_hover;
}
QScrollBar::add-line, QScrollBar::sub-line {
    width: 0;
    height: 0;
    background: transparent;
    border: none;
}
QScrollBar::add-page, QScrollBar::sub-page {
    background: transparent;
}

/* ---------- 菜单 / 提示 ---------- */
QMenu {
    background: $surface;
    color: $text;
    border: 1px solid $border;
    border-radius: 8px;
    padding: 6px;
}
QMenu::item {
    padding: 6px 18px;
    border-radius: 6px;
}
QMenu::item:selected {
    background: $primary;
    color: $on_primary;
}
QMenu::item:disabled {
    color: $disabled_text;
}
QMenu::separator {
    height: 1px;
    background: $border;
    margin: 4px 8px;
}
QToolTip {
    background: $surface;
    color: $text;
    border: 1px solid $border;
    padding: 6px 8px;
    border-radius: 6px;
}

/* ---------- 提示横幅 ---------- */
QFrame#BannerInfo {
    background: $info_bg;
    border: 1px solid $info;
    border-radius: 10px;
}
QFrame#BannerWarning {
    background: $warning_bg;
    border: 1px solid $warning;
    border-radius: 10px;
}
QFrame#BannerDanger {
    background: $danger_bg;
    border: 1px solid $danger;
    border-radius: 10px;
}
QFrame#BannerSuccess {
    background: $success_bg;
    border: 1px solid $success;
    border-radius: 10px;
}
QFrame#BannerInfo QLabel, QFrame#BannerWarning QLabel,
QFrame#BannerDanger QLabel, QFrame#BannerSuccess QLabel {
    background: transparent;
    border: none;
    color: $text;
}
"""
)


def _render(tokens: ThemeTokens) -> str:
    values = asdict(tokens)
    values["check_icon"] = str(resource_path("assets/check.svg")).replace("\\", "/")
    return _QSS_TEMPLATE.substitute(values)


LIGHT_QSS = _render(LIGHT_TOKENS)
DARK_QSS = _render(DARK_TOKENS)


def read_windows_apps_theme() -> ThemeName | None:
    """读取 Windows「应用使用浅色主题」设置；失败时返回 ``None``。"""
    if sys.platform != "win32":
        return None
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        return "light" if int(value) else "dark"
    except (OSError, ValueError, ImportError):
        return None


def detect_system_theme(
    color_scheme: Qt.ColorScheme | None = None,
    *,
    registry_reader: Callable[[], ThemeName | None] = read_windows_apps_theme,
) -> ThemeName:
    """优先使用 Qt 的 colorScheme，未知时回退注册表。"""
    if color_scheme is None:
        app = QApplication.instance()
        if app is not None:
            color_scheme = app.styleHints().colorScheme()
    scheme = getattr(Qt, "ColorScheme", None)
    if scheme is not None:
        if color_scheme == scheme.Dark:
            return "dark"
        if color_scheme == scheme.Light:
            return "light"
    return registry_reader() or "light"


def build_palette(tokens: ThemeTokens) -> QPalette:
    """构建与 QSS 配套的 QPalette，覆盖弹窗与原生绘制细节。"""
    palette = QPalette()
    roles = {
        QPalette.ColorRole.Window: tokens.bg,
        QPalette.ColorRole.WindowText: tokens.text,
        QPalette.ColorRole.Base: tokens.surface,
        QPalette.ColorRole.AlternateBase: tokens.surface_alt,
        QPalette.ColorRole.ToolTipBase: tokens.surface,
        QPalette.ColorRole.ToolTipText: tokens.text,
        QPalette.ColorRole.Text: tokens.text,
        QPalette.ColorRole.Button: tokens.surface_alt,
        QPalette.ColorRole.ButtonText: tokens.text,
        QPalette.ColorRole.BrightText: tokens.danger,
        QPalette.ColorRole.Link: tokens.primary,
        QPalette.ColorRole.Highlight: tokens.primary,
        QPalette.ColorRole.HighlightedText: tokens.on_primary,
        QPalette.ColorRole.PlaceholderText: tokens.muted,
        QPalette.ColorRole.Light: tokens.surface,
        QPalette.ColorRole.Midlight: tokens.border,
        QPalette.ColorRole.Mid: tokens.border,
        QPalette.ColorRole.Dark: tokens.border,
        QPalette.ColorRole.Shadow: tokens.border,
    }
    for role, color in roles.items():
        palette.setColor(role, QColor(color))
    disabled = QPalette.ColorGroup.Disabled
    for role in (
        QPalette.ColorRole.WindowText,
        QPalette.ColorRole.Text,
        QPalette.ColorRole.ButtonText,
        QPalette.ColorRole.PlaceholderText,
    ):
        palette.setColor(disabled, role, QColor(tokens.disabled_text))
    for role in (QPalette.ColorRole.Base, QPalette.ColorRole.Button):
        palette.setColor(disabled, role, QColor(tokens.disabled_bg))
    return palette


_ACTIVE_MANAGER: ThemeManager | None = None


def active_manager() -> ThemeManager | None:
    return _ACTIVE_MANAGER


def active_tokens() -> ThemeTokens:
    """供非 QSS 场景（HTML、动态 tone）读取当前主题颜色。"""
    manager = _ACTIVE_MANAGER
    return manager.tokens if manager is not None else LIGHT_TOKENS


class ThemeManager(QObject):
    """统一应用 Fusion 样式、QSS 与调色板。"""

    changed = Signal(str)  # 实际生效的 "light" / "dark"

    def __init__(
        self,
        app: QApplication,
        *,
        mode: ThemeMode | None = None,
        settings: QSettings | None = None,
    ) -> None:
        super().__init__()
        self._app = app
        app.setStyle("Fusion")
        self._settings = settings or QSettings("CampusStreamHelper", "CampusStreamHelper")
        if mode is None:
            stored = str(self._settings.value(_SETTINGS_KEY, "system"))
            mode = stored if stored in _MODES else "system"  # type: ignore[assignment]
        if mode not in _MODES:
            raise ValueError(f"未知主题模式：{mode}")
        self._mode: ThemeMode = mode
        self._effective: ThemeName = "light"
        hints = app.styleHints()
        scheme_changed = getattr(hints, "colorSchemeChanged", None)
        if scheme_changed is not None:
            scheme_changed.connect(self._on_color_scheme_changed)
        self.apply(emit=False)

    @property
    def mode(self) -> ThemeMode:
        return self._mode

    @property
    def effective(self) -> ThemeName:
        return self._effective

    @property
    def is_dark(self) -> bool:
        return self._effective == "dark"

    @property
    def tokens(self) -> ThemeTokens:
        return DARK_TOKENS if self.is_dark else LIGHT_TOKENS

    def set_mode(self, mode: ThemeMode) -> None:
        if mode not in _MODES:
            raise ValueError(f"未知主题模式：{mode}")
        self._mode = mode
        self._settings.setValue(_SETTINGS_KEY, mode)
        self._effective = self._resolve()
        self.apply()

    def apply(self, *, emit: bool = True) -> None:
        global _ACTIVE_MANAGER
        self._effective = self._resolve()
        _ACTIVE_MANAGER = self
        self._app.setStyleSheet(DARK_QSS if self.is_dark else LIGHT_QSS)
        self._app.setPalette(build_palette(self.tokens))
        if emit:
            self.changed.emit(self._effective)

    def refresh_system(self) -> None:
        if self._mode != "system":
            return
        resolved = detect_system_theme()
        if resolved != self._effective:
            self._effective = resolved
            self.apply()

    def _resolve(self) -> ThemeName:
        if self._mode == "system":
            return detect_system_theme()
        return self._mode  # type: ignore[return-value]

    def _on_color_scheme_changed(self, _scheme: Qt.ColorScheme | None = None) -> None:
        if self._mode == "system":
            self.refresh_system()


class ThemeSwitcher(QToolButton):
    """右上角的轻量主题切换按钮：跟随系统 / 浅色 / 深色。"""

    def __init__(self, manager: ThemeManager, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._manager = manager
        self.setObjectName("ThemeSwitcher")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.setMinimumWidth(104)

        self._menu = QMenu(self)
        self._group = QActionGroup(self)
        self._group.setExclusive(True)
        self._actions: dict[str, QAction] = {}
        labels = {"system": "跟随系统", "light": "浅色", "dark": "深色"}
        for mode, label in labels.items():
            action = self._menu.addAction(label)
            action.setCheckable(True)
            action.setData(mode)
            self._group.addAction(action)
            action.triggered.connect(
                lambda _checked=False, chosen=mode: self._manager.set_mode(chosen)
            )
            self._actions[mode] = action
        self.setMenu(self._menu)
        self._sync()
        manager.changed.connect(self._sync)

    def _sync(self, *_args) -> None:
        labels = {"system": "跟随系统", "light": "浅色", "dark": "深色"}
        mode = self._manager.mode
        self.setText(f"◐ {labels[mode]}")
        system_hint = ""
        if mode == "system":
            system_hint = "（深色）" if self._manager.is_dark else "（浅色）"
        self.setToolTip(f"当前主题：{labels[mode]}{system_hint}，点击切换")
        for key, action in self._actions.items():
            action.setChecked(key == mode)
