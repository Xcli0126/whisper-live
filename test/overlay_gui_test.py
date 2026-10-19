
import json, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PySide6.QtWidgets import QApplication
import app
qa = QApplication([])
w = app.App()
w.cb_mask.setCurrentIndex(w.cb_mask.findData("none"))
w.sl_alpha.setValue(70)
w.sl_scale.setValue(130)
w.sp_dwell.setValue(20)
print("mask ->", w.cb_mask.currentData(), "alpha", w.sl_alpha.value(), "scale", w.sl_scale.value(), "dwell", w.sp_dwell.value())
print("file ->", app.OVERLAY.read_text())
