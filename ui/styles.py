"""PySide6 扁平化主题。"""

from __future__ import annotations

from PySide6.QtWidgets import QApplication

COLORS = {
    "bg": "#F4F7FB",
    "surface": "#FFFFFF",
    "sidebar": "#0F172A",
    "sidebar_muted": "#94A3B8",
    "primary": "#2563EB",
    "primary_hover": "#1D4ED8",
    "text": "#0F172A",
    "muted": "#64748B",
    "border": "#E2E8F0",
    "success": "#16A34A",
    "warning": "#D97706",
    "danger": "#DC2626",
}

STYLESHEET = """
* {
    font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
    font-size: 13px;
    color: #0F172A;
}
QMainWindow, QWidget#Root {
    background: #F4F7FB;
}
QWidget#Sidebar {
    background: #0F172A;
}
QWidget#Sidebar QLabel {
    color: #E2E8F0;
}
QLabel#AppTitle {
    font-size: 19px;
    font-weight: 700;
    color: #FFFFFF;
}
QLabel#AppSubtitle {
    font-size: 11px;
    color: #94A3B8;
}
QLabel#SidebarFootnote {
    font-size: 11px;
    color: #94A3B8;
}
QWidget#SidebarStep {
    background: transparent;
    border-radius: 10px;
}
QWidget#SidebarStep[state="current"] {
    background: #172554;
}
QLabel#StepIndex {
    min-width: 24px;
    max-width: 24px;
    min-height: 24px;
    max-height: 24px;
    border-radius: 12px;
    background: #1E293B;
    color: #94A3B8;
    font-size: 11px;
    font-weight: 700;
    qproperty-alignment: AlignCenter;
}
QWidget#SidebarStep[state="current"] QLabel#StepIndex {
    background: #2563EB;
    color: #FFFFFF;
}
QWidget#SidebarStep[state="done"] QLabel#StepIndex {
    background: #14532D;
    color: #86EFAC;
}
QLabel#StepTitle {
    font-size: 13px;
    font-weight: 600;
    color: #E2E8F0;
}
QLabel#StepSubtitle {
    font-size: 11px;
    color: #94A3B8;
}
QWidget#SidebarStep[state="current"] QLabel#StepTitle {
    color: #FFFFFF;
}
QCheckBox#DryRunToggle {
    color: #E2E8F0;
    font-size: 12px;
}
QLabel#AdminPill {
    color: #94A3B8;
    font-size: 11px;
}
QFrame#Card {
    background: #FFFFFF;
    border: 1px solid #E2E8F0;
    border-radius: 12px;
}
QLabel#CardTitle {
    font-size: 15px;
    font-weight: 700;
    color: #0F172A;
}
QLabel#SectionTitle {
    font-size: 13px;
    font-weight: 700;
    color: #0F172A;
}
QLabel#Muted {
    color: #64748B;
    font-size: 12px;
}
QLabel#Hint {
    color: #64748B;
    font-size: 12px;
}
QLabel#HeroValue {
    font-size: 30px;
    font-weight: 700;
    color: #0F172A;
}
QLabel#PageTitle {
    font-size: 24px;
    font-weight: 700;
    color: #0F172A;
}
QLabel#PageSubtitle {
    font-size: 13px;
    color: #64748B;
}
QLabel#StatusPill {
    padding: 3px 10px;
    border-radius: 9px;
    font-size: 11px;
    font-weight: 700;
}
QLabel#StatusPill[tone="success"] {
    background: #DCFCE7;
    color: #166534;
}
QLabel#StatusPill[tone="warning"] {
    background: #FEF3C7;
    color: #92400E;
}
QLabel#StatusPill[tone="danger"] {
    background: #FEE2E2;
    color: #991B1B;
}
QLabel#StatusPill[tone="info"] {
    background: #DBEAFE;
    color: #1E40AF;
}
QLabel#StatusPill[tone="muted"] {
    background: #E2E8F0;
    color: #475569;
}
QPushButton#PrimaryButton {
    background: #2563EB;
    color: #FFFFFF;
    border: none;
    border-radius: 8px;
    padding: 9px 18px;
    font-weight: 600;
}
QPushButton#PrimaryButton:hover {
    background: #1D4ED8;
}
QPushButton#PrimaryButton:pressed {
    background: #1E40AF;
}
QPushButton#PrimaryButton:disabled {
    background: #BFDBFE;
    color: #EFF6FF;
}
QPushButton#SecondaryButton {
    background: #FFFFFF;
    color: #0F172A;
    border: 1px solid #CBD5E1;
    border-radius: 8px;
    padding: 8px 16px;
}
QPushButton#SecondaryButton:hover {
    background: #F1F5F9;
}
QPushButton#SecondaryButton:disabled {
    color: #94A3B8;
    border-color: #E2E8F0;
}
QPushButton#DangerButton {
    background: #FFFFFF;
    color: #B91C1C;
    border: 1px solid #FECACA;
    border-radius: 8px;
    padding: 8px 16px;
}
QPushButton#DangerButton:hover {
    background: #FEF2F2;
}
QPushButton#LinkButton {
    background: transparent;
    color: #2563EB;
    border: none;
    padding: 4px 2px;
    text-align: left;
}
QPushButton#LinkButton:hover {
    color: #1D4ED8;
    text-decoration: underline;
}
QComboBox, QSpinBox, QLineEdit {
    background: #FFFFFF;
    border: 1px solid #CBD5E1;
    border-radius: 8px;
    padding: 6px 10px;
    min-height: 20px;
}
QComboBox:focus, QSpinBox:focus, QLineEdit:focus {
    border-color: #2563EB;
}
QComboBox:disabled, QSpinBox:disabled, QLineEdit:disabled {
    background: #F1F5F9;
    color: #94A3B8;
    border-color: #E2E8F0;
}
QComboBox::drop-down {
    border: none;
    width: 24px;
}
QRadioButton, QCheckBox {
    spacing: 8px;
}
QTabWidget::pane {
    border: none;
    background: transparent;
}
QTabBar::tab {
    background: transparent;
    color: #64748B;
    padding: 8px 14px;
    margin-right: 4px;
    border-bottom: 2px solid transparent;
    font-weight: 600;
}
QTabBar::tab:selected {
    color: #2563EB;
    border-bottom: 2px solid #2563EB;
}
QTabBar::tab:hover {
    color: #0F172A;
}
QListWidget {
    background: transparent;
    border: none;
    outline: none;
}
QListWidget::item {
    background: #FFFFFF;
    border: 1px solid #E2E8F0;
    border-radius: 9px;
    padding: 10px 12px;
    margin-bottom: 8px;
    color: #0F172A;
}
QListWidget::item:hover {
    border-color: #BFDBFE;
    background: #F8FBFF;
}
QListWidget::item:selected {
    border-color: #93C5FD;
    background: #EFF6FF;
}
QProgressBar {
    background: #E2E8F0;
    border: none;
    border-radius: 5px;
    height: 10px;
    text-align: center;
    color: transparent;
}
QProgressBar::chunk {
    background: #2563EB;
    border-radius: 5px;
}
QPlainTextEdit#LogView {
    background: #0B1220;
    color: #D7E2F0;
    border: 1px solid #1E293B;
    border-radius: 10px;
    padding: 12px;
    selection-background-color: #1D4ED8;
}
QScrollArea {
    border: none;
    background: transparent;
}
QScrollArea > QWidget > QWidget {
    background: transparent;
}
QTextBrowser {
    background: transparent;
    border: none;
}
QFrame#BannerInfo {
    background: #EFF6FF;
    border: 1px solid #BFDBFE;
    border-radius: 10px;
}
QFrame#BannerWarning {
    background: #FFFBEB;
    border: 1px solid #FDE68A;
    border-radius: 10px;
}
QFrame#BannerDanger {
    background: #FEF2F2;
    border: 1px solid #FECACA;
    border-radius: 10px;
}
QFrame#BannerSuccess {
    background: #F0FDF4;
    border: 1px solid #BBF7D0;
    border-radius: 10px;
}
QFrame#BannerInfo QLabel, QFrame#BannerWarning QLabel, QFrame#BannerDanger QLabel, QFrame#BannerSuccess QLabel {
    background: transparent;
    border: none;
}
QToolTip {
    background: #0F172A;
    color: #FFFFFF;
    border: none;
    padding: 6px 8px;
    border-radius: 6px;
}
"""


def apply_stylesheet(app: QApplication) -> None:
    app.setStyle("Fusion")
    app.setStyleSheet(STYLESHEET)
