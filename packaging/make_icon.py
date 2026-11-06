"""Draw the CytoGate icon (a gate around a dot cloud) as PNG and Windows ICO.

    python packaging/make_icon.py
"""

import math
import os
import random
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def draw(size: int) -> QtGui.QImage:
    img = QtGui.QImage(size, size, QtGui.QImage.Format.Format_ARGB32)
    img.fill(QtCore.Qt.GlobalColor.transparent)
    p = QtGui.QPainter(img)
    p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
    s = size / 256
    p.setBrush(QtGui.QColor(24, 46, 92))
    p.setPen(QtCore.Qt.PenStyle.NoPen)
    p.drawRoundedRect(QtCore.QRectF(8 * s, 8 * s, 240 * s, 240 * s), 48 * s, 48 * s)
    rnd = random.Random(3)
    lut = [(0, 120, 255), (0, 210, 220), (40, 220, 60), (250, 230, 0), (255, 120, 0), (230, 20, 20)]
    for cx, cy, sx, sy, n in ((100, 150, 30, 22, 260), (170, 90, 22, 26, 160)):
        for _ in range(n):
            x, y = rnd.gauss(cx, sx), rnd.gauss(cy, sy)
            d = math.exp(-(((x - cx) / sx) ** 2 + ((y - cy) / sy) ** 2) / 2)
            c = lut[min(int(d * len(lut)), len(lut) - 1)]
            p.setBrush(QtGui.QColor(*c))
            r = max(3.2 * s, 0.8)
            p.drawEllipse(QtCore.QPointF(x * s, y * s), r, r)
    p.setBrush(QtCore.Qt.BrushStyle.NoBrush)
    p.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255), max(9 * s, 1.5)))
    p.drawEllipse(QtCore.QRectF(50 * s, 104 * s, 104 * s, 90 * s))
    p.end()
    return img


def main() -> None:
    QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv[:1])
    res = ROOT / "src" / "cytogate" / "resources"
    res.mkdir(parents=True, exist_ok=True)
    draw(256).save(str(res / "cytogate.png"))
    # Windows .ico with several sizes, written by hand (PNG-compressed entries)
    sizes = [16, 24, 32, 48, 64, 128, 256]
    blobs = []
    for n in sizes:
        buf = QtCore.QBuffer()
        buf.open(QtCore.QIODevice.OpenModeFlag.WriteOnly)
        draw(n).save(buf, "PNG")
        blobs.append(bytes(buf.data()))
    import struct
    head = struct.pack("<HHH", 0, 1, len(sizes))
    offset = 6 + 16 * len(sizes)
    entries = b""
    for n, b in zip(sizes, blobs):
        entries += struct.pack("<BBBBHHII", n % 256, n % 256, 0, 0, 1, 32, len(b), offset)
        offset += len(b)
    (ROOT / "packaging" / "cytogate.ico").write_bytes(head + entries + b"".join(blobs))
    print("wrote", res / "cytogate.png", "and packaging/cytogate.ico")


if __name__ == "__main__":
    main()
