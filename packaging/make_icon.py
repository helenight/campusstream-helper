r"""生成 assets/icon.png 与多尺寸 assets/icon.ico。

一次性资源脚本，仅在需要调整品牌图标时运行：
    .\.venv\Scripts\python.exe packaging\make_icon.py

不依赖 Pillow；ICO 使用 PNG 压缩条目（Windows Vista 及以上支持）。
"""

from __future__ import annotations

import os
import struct
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QGuiApplication,
    QImage,
    QLinearGradient,
    QPainter,
    QPen,
    QPolygonF,
)

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
SIZES = (16, 24, 32, 48, 64, 128, 256)


def render(size: int) -> QImage:
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    margin = size * 0.04
    gradient = QLinearGradient(0, 0, size, size)
    gradient.setColorAt(0.0, QColor("#2563EB"))
    gradient.setColorAt(1.0, QColor("#0F172A"))
    painter.setBrush(gradient)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(
        QRectF(margin, margin, size - margin * 2, size - margin * 2),
        size * 0.22,
        size * 0.22,
    )

    pen = QPen(QColor(255, 255, 255, 235))
    pen.setWidthF(max(1.4, size * 0.045))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRoundedRect(
        QRectF(size * 0.22, size * 0.25, size * 0.56, size * 0.38),
        size * 0.07,
        size * 0.07,
    )
    painter.drawLine(
        QPointF(size * 0.5, size * 0.63),
        QPointF(size * 0.5, size * 0.73),
    )
    painter.drawLine(
        QPointF(size * 0.37, size * 0.76),
        QPointF(size * 0.63, size * 0.76),
    )

    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#67E8F9"))
    painter.drawPolygon(
        QPolygonF(
            [
                QPointF(size * 0.45, size * 0.33),
                QPointF(size * 0.45, size * 0.55),
                QPointF(size * 0.62, size * 0.44),
            ]
        )
    )
    painter.end()
    return image


def png_bytes(image: QImage) -> bytes:
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    buffer.close()
    return bytes(data)


def write_ico(path: Path, images: list[QImage]) -> None:
    payloads = [png_bytes(image) for image in images]
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries = bytearray()
    for image, payload in zip(images, payloads):
        width = 0 if image.width() >= 256 else image.width()
        height = 0 if image.height() >= 256 else image.height()
        entries += struct.pack(
            "<BBBBHHII",
            width,
            height,
            0,
            0,
            1,
            32,
            len(payload),
            offset,
        )
        offset += len(payload)
    path.write_bytes(header + bytes(entries) + b"".join(payloads))


def main() -> int:
    QGuiApplication([])
    ASSETS.mkdir(parents=True, exist_ok=True)
    master = render(256)
    master.save(str(ASSETS / "icon.png"), "PNG")
    images = [
        master if size == 256 else master.scaled(
            size,
            size,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        for size in SIZES
    ]
    write_ico(ASSETS / "icon.ico", images)
    print(f"wrote {ASSETS / 'icon.png'}")
    print(f"wrote {ASSETS / 'icon.ico'} ({', '.join(str(s) for s in SIZES)} px)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
