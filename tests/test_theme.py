"""主题系统测试：对比度、QSS 覆盖、系统探测与运行时切换。"""

from __future__ import annotations

import os
import re
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from ui.theme import (
    DARK_QSS,
    DARK_TOKENS,
    LIGHT_QSS,
    LIGHT_TOKENS,
    ThemeManager,
    ThemeSwitcher,
    active_tokens,
    detect_system_theme,
)

ROOT = Path(__file__).resolve().parents[1]


def _channel(value: int) -> float:
    ratio = value / 255
    return ratio / 12.92 if ratio <= 0.03928 else ((ratio + 0.055) / 1.055) ** 2.4


def luminance(color: str) -> float:
    color = color.lstrip("#")
    r, g, b = (int(color[i : i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def contrast(foreground: str, background: str) -> float:
    first, second = luminance(foreground), luminance(background)
    lighter, darker = max(first, second), min(first, second)
    return (lighter + 0.05) / (darker + 0.05)


class ContrastTest(unittest.TestCase):
    def test_text_pairs_meet_wcag_aa(self) -> None:
        for name, tokens in (("light", LIGHT_TOKENS), ("dark", DARK_TOKENS)):
            pairs = {
                "正文/背景": (tokens.text, tokens.bg),
                "正文/卡片": (tokens.text, tokens.surface),
                "次要文字/背景": (tokens.muted, tokens.bg),
                "次要文字/卡片": (tokens.muted, tokens.surface),
                "侧栏正文": (tokens.sidebar_text, tokens.sidebar),
                "侧栏次要": (tokens.sidebar_muted, tokens.sidebar),
                "侧栏当前步骤": (tokens.sidebar_title, tokens.sidebar_active),
                "侧栏步骤序号": (tokens.sidebar_muted, tokens.sidebar_index),
                "主按钮文字": (tokens.on_primary, tokens.primary),
                "主按钮悬停": (tokens.on_primary, tokens.primary_hover),
                "成功徽章": (tokens.success_fg, tokens.success_bg),
                "警告徽章": (tokens.warning_fg, tokens.warning_bg),
                "错误徽章": (tokens.danger_fg, tokens.danger_bg),
                "信息徽章": (tokens.info_fg, tokens.info_bg),
                "日志文字": (tokens.log_text, tokens.log_bg),
            }
            for label, (fg, bg) in pairs.items():
                self.assertGreaterEqual(
                    contrast(fg, bg),
                    4.5,
                    f"{name} 主题 {label} 对比度不足：{fg} / {bg}",
                )


class QssCoverageTest(unittest.TestCase):
    def test_both_sheets_cover_dialogs_and_controls(self) -> None:
        required = (
            "QMainWindow",
            "QDialog",
            "QMessageBox",
            "QFileDialog",
            "QLineEdit",
            "QComboBox",
            "QSpinBox",
            "QListWidget",
            "QPlainTextEdit#LogView",
            "QProgressBar",
            "QScrollBar:vertical",
            "QToolTip",
            "QMenu",
            "QTabBar::tab",
            'QLabel#StatusBadge[state="ready"]',
            'QLabel#StatusBadge[state="warning"]',
            'QLabel#StatusBadge[state="failed"]',
            "QPushButton#PrimaryButton",
            "QToolButton#ThemeSwitcher",
        )
        for name, qss in (("light", LIGHT_QSS), ("dark", DARK_QSS)):
            for selector in required:
                self.assertIn(selector, qss, f"{name} 主题缺少选择器 {selector}")

    def test_light_and_dark_are_different(self) -> None:
        self.assertNotEqual(LIGHT_QSS, DARK_QSS)
        self.assertIn(LIGHT_TOKENS.bg, LIGHT_QSS)
        self.assertIn(DARK_TOKENS.bg, DARK_QSS)

    def test_no_hardcoded_colors_outside_theme_module(self) -> None:
        pattern = re.compile(r"#[0-9A-Fa-f]{6}\b")
        offenders: list[str] = []
        for path in (ROOT / "ui").rglob("*.py"):
            if path.name == "theme.py":
                continue
            for number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                if pattern.search(line):
                    offenders.append(f"{path.relative_to(ROOT)}:{number}")
        self.assertEqual(offenders, [], f"发现散落的颜色值：{offenders}")


class DetectTest(unittest.TestCase):
    def test_qt_color_scheme_wins(self) -> None:
        self.assertEqual(
            detect_system_theme(Qt.ColorScheme.Dark, registry_reader=lambda: "light"),
            "dark",
        )
        self.assertEqual(
            detect_system_theme(Qt.ColorScheme.Light, registry_reader=lambda: "dark"),
            "light",
        )

    def test_unknown_falls_back_to_registry(self) -> None:
        self.assertEqual(
            detect_system_theme(Qt.ColorScheme.Unknown, registry_reader=lambda: "dark"),
            "dark",
        )
        self.assertEqual(
            detect_system_theme(Qt.ColorScheme.Unknown, registry_reader=lambda: None),
            "light",
        )


class ThemeManagerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.settings = QSettings(
            str(Path(self._tmp.name) / "settings.ini"),
            QSettings.Format.IniFormat,
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _manager(self, mode: str = "light") -> ThemeManager:
        return ThemeManager(self.app, mode=mode, settings=self.settings)

    def test_apply_sets_stylesheet_and_palette(self) -> None:
        manager = self._manager("light")
        self.assertEqual(self.app.styleSheet(), LIGHT_QSS)
        self.assertEqual(
            self.app.palette().color(QPalette.ColorRole.Window).name().lower(),
            LIGHT_TOKENS.bg.lower(),
        )

        events: list[str] = []
        manager.changed.connect(events.append)
        manager.set_mode("dark")

        self.assertEqual(self.app.styleSheet(), DARK_QSS)
        self.assertEqual(
            self.app.palette().color(QPalette.ColorRole.Window).name().lower(),
            DARK_TOKENS.bg.lower(),
        )
        self.assertEqual(events, ["dark"])
        self.assertEqual(self.settings.value("ui/theme_mode"), "dark")
        self.assertEqual(active_tokens().bg, DARK_TOKENS.bg)

    def test_mode_is_persisted(self) -> None:
        manager = self._manager("light")
        manager.set_mode("dark")
        restored = ThemeManager(self.app, mode=None, settings=self.settings)
        self.assertEqual(restored.mode, "dark")

    def test_invalid_mode_rejected(self) -> None:
        manager = self._manager("light")
        with self.assertRaises(ValueError):
            manager.set_mode("sepia")  # type: ignore[arg-type]

    def test_switcher_reflects_mode(self) -> None:
        manager = self._manager("dark")
        switcher = ThemeSwitcher(manager)
        self.assertIn("深色", switcher.text())
        switcher._actions["light"].trigger()
        self.assertEqual(manager.mode, "light")
        self.assertIn("浅色", switcher.text())

    def test_dialogs_inherit_dark_palette(self) -> None:
        self._manager("dark")
        for widget in (QMessageBox(), QDialog()):
            self.assertEqual(
                widget.palette().color(QPalette.ColorRole.Window).name().lower(),
                DARK_TOKENS.bg.lower(),
            )
            widget.deleteLater()


if __name__ == "__main__":
    unittest.main()
