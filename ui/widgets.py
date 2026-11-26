"""可复用的扁平化界面组件。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


def repolish(widget: QWidget) -> None:
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()


class Card(QFrame):
    """带标题的白色卡片。"""

    def __init__(self, title: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 18)
        outer.setSpacing(12)
        if title:
            label = QLabel(title)
            label.setObjectName("CardTitle")
            outer.addWidget(label)
        self.body = QVBoxLayout()
        self.body.setSpacing(8)
        outer.addLayout(self.body)

    def add(self, widget: QWidget) -> None:
        self.body.addWidget(widget)

    def add_layout(self, layout) -> None:
        self.body.addLayout(layout)


class StatusPill(QLabel):
    """成功 / 警告 / 危险 / 信息状态胶囊。"""

    def __init__(self, text: str = "", tone: str = "muted") -> None:
        super().__init__(text)
        self.setObjectName("StatusPill")
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.set_tone(tone)

    def set_tone(self, tone: str) -> None:
        self.setProperty("tone", tone)
        repolish(self)

    def set_status(self, text: str, tone: str) -> None:
        self.setText(text)
        self.set_tone(tone)


class InfoRow(QWidget):
    """左侧标签 + 右侧值的两列信息行。"""

    def __init__(self, label: str, value: str = "—", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 1, 0, 1)
        layout.setSpacing(12)
        self._label = QLabel(label)
        self._label.setObjectName("Muted")
        self._label.setFixedWidth(96)
        self._label.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
        )
        self._value = QLabel(value)
        self._value.setWordWrap(True)
        self._value.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
        )
        self._value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self._label)
        layout.addWidget(self._value, 1)

    def set_value(self, value: str) -> None:
        self._value.setText(value or "—")

    def set_tone(self, tone: str) -> None:
        colors = {
            "success": "#15803D",
            "warning": "#B45309",
            "danger": "#B91C1C",
            "info": "#1D4ED8",
            "muted": "#0F172A",
        }
        self._value.setStyleSheet(f"color: {colors.get(tone, '#0F172A')};")


class Banner(QFrame):
    """提示横幅，用于环境警告、失败信息或成功摘要。"""

    def __init__(
        self,
        text: str = "",
        tone: str = "info",
        title: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._tone = tone
        self.set_tone(tone)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(4)
        self._title = QLabel(title)
        self._title.setStyleSheet("font-weight: 700;")
        self._title.setVisible(bool(title))
        self._text = QLabel(text)
        self._text.setWordWrap(True)
        self._text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self._title)
        layout.addWidget(self._text)

    def set_tone(self, tone: str) -> None:
        self._tone = tone
        self.setObjectName(
            {
                "info": "BannerInfo",
                "warning": "BannerWarning",
                "danger": "BannerDanger",
                "success": "BannerSuccess",
            }.get(tone, "BannerInfo")
        )
        repolish(self)

    def set_text(self, text: str, *, tone: str | None = None, title: str | None = None) -> None:
        self._text.setText(text)
        if title is not None:
            self._title.setText(title)
            self._title.setVisible(bool(title))
        if tone is not None:
            self.set_tone(tone)


def make_button(text: str, kind: str = "secondary") -> QPushButton:
    button = QPushButton(text)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setObjectName(
        {
            "primary": "PrimaryButton",
            "secondary": "SecondaryButton",
            "danger": "DangerButton",
            "link": "LinkButton",
        }.get(kind, "SecondaryButton")
    )
    return button


def muted_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("Muted")
    label.setWordWrap(True)
    return label


def heading(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("SectionTitle")
    return label
