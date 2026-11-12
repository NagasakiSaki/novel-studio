"""Reproduce the tiny native UI checkbox glyphs using the existing Qt dependency."""
from pathlib import Path
from PySide6.QtCore import Qt, QPointF
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen

root=Path(__file__).resolve().parents[1]/'assets'
for theme,color in [('light','#fffefa'),('dark','#242e30')]:
    for shape in ['check','partial']:
        image=QImage(48,48,QImage.Format_ARGB32)
        image.fill(Qt.transparent)
        painter=QPainter(image)
        painter.setRenderHint(QPainter.Antialiasing)
        pen=QPen(QColor(color),6,Qt.SolidLine,Qt.RoundCap,Qt.RoundJoin)
        painter.setPen(pen)
        path=QPainterPath()
        if shape=='check':
            path.moveTo(9,24);path.lineTo(18,33);path.lineTo(39,12)
        else:
            path.moveTo(12,24);path.lineTo(36,24)
        painter.drawPath(path)
        painter.end()
        assert image.save(str(root/f'{shape}-{theme}.png'))
print('Built four local checkbox glyphs; no new runtime dependency')
