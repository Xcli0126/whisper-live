import sys, os
os.environ["QT_QPA_PLATFORM"]="xcb"
from PySide6.QtWidgets import QApplication, QWidget, QLabel, QVBoxLayout
from PySide6.QtCore import Qt

bypass = "--bypass" in sys.argv
app = QApplication(sys.argv)
w = QWidget()
flags = Qt.FramelessWindowHint | Qt.Tool | Qt.WindowTransparentForInput | Qt.WindowStaysOnTopHint
if bypass:
    flags |= Qt.X11BypassWindowManagerHint
w.setWindowFlags(flags)
w.setAttribute(Qt.WA_TranslucentBackground, True)
w.setAttribute(Qt.WA_ShowWithoutActivating, True)
lay = QVBoxLayout(w)
lbl = QLabel("Qt 测试字幕 一句中文 / English subtitle line")
lbl.setStyleSheet("color: white; font-size: 34px; background: rgba(0,0,0,160); padding: 6px;")
lbl.setAlignment(Qt.AlignCenter)
lay.addStretch(1)
lay.addWidget(lbl)
w.resize(1400, 300)
w.move(300, 1100)
w.show()
sys.exit(app.exec())
