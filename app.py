#!/usr/bin/env python3
"""实时字幕 · 配置窗口。

选语言、选延迟，点「开始」就把系统音频的实时字幕跑起来；
这些选择存在 ~/.config/whisper-live/config.json，下次打开照旧。
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QComboBox, QFrame, QGridLayout, QHBoxLayout,
    QCheckBox, QLabel, QPushButton, QRadioButton, QSlider, QSpinBox, QVBoxLayout, QWidget,
)

ROOT = Path(__file__).resolve().parent
CONF = Path(os.path.expanduser("~/.config/whisper-live/config.json"))

LANGS = [
    ("en", "英语"), ("zh", "中文"), ("ja", "日语"), ("ko", "韩语"),
    ("fr", "法语"), ("de", "德语"), ("es", "西班牙语"), ("ru", "俄语"),
]
LATENCY = [
    ("stable", "稳定", "等这句的中文润色完再上屏，最准，比说话晚 2-3 秒"),
    ("balanced", "均衡", "整句上屏，云端 2.4 秒不回就用本地译文，晚一句"),
    ("fast", "快速", "正在说的半句也上屏，字会边听边改，最快"),
]
LATENCY_LOCAL = {
    "stable": "整句上屏，等这句翻完再放（本地约 0.3 秒），最准",
    "balanced": "整句上屏，晚一句（本地约 0.3 秒）",
    "fast": "正在说的半句也上屏，字会边听边改，最快",
}
OVERLAY = Path(os.path.expanduser("~/.config/whisper-live/overlay.json"))
OVERLAY_DEFAULTS = {"x": None, "y": None, "scale": 1.0, "mask": "gray",
                    "mask_alpha": 0.55, "dwell": 9.0, "edit": False}
MASK_ITEMS = [("gray", "灰色蒙版"), ("black", "黑色蒙版"), ("none", "不要蒙版")]

DEFAULTS = {"source": "en", "target": "zh", "latency": "balanced", "cloud": False}


def load_conf() -> dict:
    try:
        return {**DEFAULTS, **json.loads(CONF.read_text("utf-8"))}
    except Exception:
        return dict(DEFAULTS)


def save_conf(conf: dict) -> None:
    CONF.parent.mkdir(parents=True, exist_ok=True)
    CONF.write_text(json.dumps(conf, ensure_ascii=False, indent=2), "utf-8")


def load_overlay() -> dict:
    try:
        raw = json.loads(OVERLAY.read_text("utf-8"))
    except Exception:
        raw = {}
    return {**OVERLAY_DEFAULTS, **{k: v for k, v in raw.items() if k in OVERLAY_DEFAULTS}}


def save_overlay(data: dict) -> None:
    OVERLAY.parent.mkdir(parents=True, exist_ok=True)
    OVERLAY.write_text(json.dumps({**OVERLAY_DEFAULTS, **data}, ensure_ascii=False, indent=2), "utf-8")


def port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


class App(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.conf = load_conf()
        self.proc: subprocess.Popen | None = None
        self.setWindowTitle("实时字幕")
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)   # 配置窗口别被浏览器压到后面去
        self.setMinimumWidth(460)
        self._build()
        self._restore()
        self.timer = QTimer(self, timeout=self._tick)
        self.timer.start(1500)
        self._tick()

    # ---------- UI ----------
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 20, 22, 18)
        outer.setSpacing(14)

        title = QLabel("实时字幕")
        f = title.font()
        f.setPointSize(f.pointSize() + 6)
        f.setWeight(QFont.DemiBold)
        title.setFont(f)
        outer.addWidget(title)
        sub = QLabel("把电脑正在放的声音转成字幕和中文翻译，浮在所有窗口的上面")
        sub.setStyleSheet("color:#888;")
        outer.addWidget(sub)

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(10)
        grid.addWidget(QLabel("识别语言"), 0, 0)
        self.cb_src = QComboBox()
        self.cb_dst = QComboBox()
        for code, name in LANGS:
            self.cb_src.addItem(f"{name}（{code}）", code)
            self.cb_dst.addItem(f"{name}（{code}）", code)
        grid.addWidget(self.cb_src, 0, 1)
        grid.addWidget(QLabel("翻译成"), 1, 0)
        grid.addWidget(self.cb_dst, 1, 1)
        outer.addLayout(grid)

        self.hint = QLabel("")
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color:#a06a00;")
        outer.addWidget(self.hint)

        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("color:#333;")
        outer.addWidget(line)

        outer.addWidget(QLabel("延迟"))
        self.group = QButtonGroup(self)
        self.notes: dict[str, QLabel] = {}
        for i, (key, name, desc) in enumerate(LATENCY):
            row = QVBoxLayout()
            rb = QRadioButton(name)
            rb.setProperty("key", key)
            self.group.addButton(rb, i)
            note = QLabel(desc)
            note.setStyleSheet("color:#888; margin-left:22px;")
            note.setWordWrap(True)
            self.notes[key] = note
            row.addWidget(rb)
            row.addWidget(note)
            outer.addLayout(row)

        line2 = QFrame()
        line2.setFrameShape(QFrame.HLine)
        line2.setStyleSheet("color:#333;")
        outer.addWidget(line2)
        outer.addWidget(QLabel("字幕外观"))

        grid2 = QGridLayout()
        grid2.setHorizontalSpacing(12)
        grid2.setVerticalSpacing(8)
        self.cb_mask = QComboBox()
        for code, name in MASK_ITEMS:
            self.cb_mask.addItem(name, code)
        grid2.addWidget(QLabel("背景蒙版"), 0, 0)
        grid2.addWidget(self.cb_mask, 0, 1)

        self.sl_alpha = QSlider(Qt.Horizontal)
        self.sl_alpha.setRange(20, 90)
        self.lb_alpha = QLabel("")
        row_a = QHBoxLayout()
        row_a.addWidget(self.sl_alpha, 1)
        row_a.addWidget(self.lb_alpha)
        grid2.addWidget(QLabel("蒙版浓度"), 1, 0)
        grid2.addLayout(row_a, 1, 1)

        self.sl_scale = QSlider(Qt.Horizontal)
        self.sl_scale.setRange(70, 180)
        self.lb_scale = QLabel("")
        row_s = QHBoxLayout()
        row_s.addWidget(self.sl_scale, 1)
        row_s.addWidget(self.lb_scale)
        grid2.addWidget(QLabel("字号"), 2, 0)
        grid2.addLayout(row_s, 2, 1)

        self.sp_dwell = QSpinBox()
        self.sp_dwell.setRange(0, 60)
        self.sp_dwell.setSuffix(" 秒（0＝不自动隐藏）")
        grid2.addWidget(QLabel("最长驻留"), 3, 0)
        grid2.addWidget(self.sp_dwell, 3, 1)
        outer.addLayout(grid2)

        self.b_pos = QPushButton("调整字幕位置（拖动浮层）")
        self.b_pos.setMinimumHeight(30)
        self.b_pos.clicked.connect(self._toggle_edit)
        outer.addWidget(self.b_pos)

        self.cb_cloud = QCheckBox("用云端润色中文（更通顺，中文会晚 1–2 秒；不开就纯本地）")
        self.cb_cloud.setStyleSheet("color:#ccc;")
        outer.addWidget(self.cb_cloud)

        outer.addSpacing(6)
        btns = QHBoxLayout()
        self.b_start = QPushButton("开始")
        self.b_start.setMinimumHeight(34)
        self.b_start.clicked.connect(self.start)
        self.b_stop = QPushButton("停止")
        self.b_stop.setMinimumHeight(34)
        self.b_stop.clicked.connect(self.stop)
        self.b_page = QPushButton("字幕页")
        self.b_page.setMinimumHeight(34)
        self.b_page.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl("http://127.0.0.1:8300")))
        btns.addWidget(self.b_start, 2)
        btns.addWidget(self.b_stop, 1)
        btns.addWidget(self.b_page, 1)
        outer.addLayout(btns)

        self.status = QLabel("")
        self.status.setStyleSheet("color:#888;")
        self.status.setWordWrap(True)
        outer.addWidget(self.status)

        self.cb_src.currentIndexChanged.connect(self._update_hint)
        self.cb_dst.currentIndexChanged.connect(self._update_hint)
        self.cb_cloud.toggled.connect(self._update_notes)
        self.cb_mask.currentIndexChanged.connect(self._apply_overlay)
        self.sl_alpha.valueChanged.connect(self._apply_overlay)
        self.sl_scale.valueChanged.connect(self._apply_overlay)
        self.sp_dwell.valueChanged.connect(self._apply_overlay)

    # ---------- 字幕外观（写 overlay.json，浮层 0.4 秒内自己改） ----------
    def _apply_overlay(self) -> None:
        data = load_overlay()          # 先读：x/y/edit 是浮层自己写的，别覆盖掉
        data.update({"mask": self.cb_mask.currentData(),
                     "mask_alpha": self.sl_alpha.value() / 100.0,
                     "scale": self.sl_scale.value() / 100.0,
                     "dwell": float(self.sp_dwell.value())})
        save_overlay(data)
        self.lb_alpha.setText(f"{self.sl_alpha.value()}%")
        self.lb_scale.setText(f"{self.sl_scale.value()}%")

    def _toggle_edit(self) -> None:
        data = load_overlay()
        data["edit"] = not data["edit"]
        save_overlay(data)
        self._sync_edit_button()

    def _sync_edit_button(self) -> None:
        editing = bool(load_overlay()["edit"])
        self.b_pos.setText("完成调整（或按 Esc）" if editing else "调整字幕位置（拖动浮层）")

    def _update_notes(self) -> None:
        """纯本地模式下把「等云端 2.4 秒」这类说法换掉，免得配置说明和实际行为对不上。"""
        local = not self.cb_cloud.isChecked()
        for key, name, desc in LATENCY:
            self.notes[key].setText(LATENCY_LOCAL[key] if local else desc)

    def _restore(self) -> None:
        for cb, key in ((self.cb_src, "source"), (self.cb_dst, "target")):
            i = cb.findData(self.conf[key])
            cb.setCurrentIndex(i if i >= 0 else 0)
        for b in self.group.buttons():
            b.setChecked(b.property("key") == self.conf["latency"])
        self.cb_cloud.setChecked(bool(self.conf.get("cloud", False)))
        ov = load_overlay()
        self.cb_mask.setCurrentIndex(max(0, self.cb_mask.findData(ov["mask"])))
        self.sl_alpha.setValue(int(round(float(ov["mask_alpha"]) * 100)))
        self.sl_scale.setValue(int(round(float(ov["scale"]) * 100)))
        self.sp_dwell.setValue(int(round(float(ov["dwell"]))))
        self.lb_alpha.setText(f"{self.sl_alpha.value()}%")
        self.lb_scale.setText(f"{self.sl_scale.value()}%")
        self._sync_edit_button()
        self._update_notes()
        self._update_hint()

    def _update_hint(self) -> None:
        src, dst = self.cb_src.currentData(), self.cb_dst.currentData()
        if src == dst:
            self.hint.setText("源语言和目标语言一样：只显示一行字幕，不翻译。")
        elif (src, dst) == ("en", "zh"):
            self.hint.setText("")
        else:
            self.hint.setText("这个方向没有本地翻译模型；勾选上面的云端润色才有译文，断网就没有。")

    def _values(self) -> dict:
        return {
            "source": self.cb_src.currentData(),
            "target": self.cb_dst.currentData(),
            "latency": next((b.property("key") for b in self.group.buttons() if b.isChecked()),
                            "balanced"),
            "cloud": self.cb_cloud.isChecked(),
        }

    # ---------- 运行 ----------
    def start(self) -> None:
        self.conf = self._values()
        save_conf(self.conf)
        if port_open(8100) or port_open(8300):
            self._reload()
            return
        env = os.environ.copy()
        src, dst = self.conf["source"], self.conf["target"]
        env.update({"LAN": src, "LANGS": f"{src},{dst}",
                    "LATENCY": self.conf["latency"],
                    "MT": "auto" if self.conf.get("cloud") else "local"})
        log = open(ROOT / "logs" / "launcher.log", "ab")
        self.proc = subprocess.Popen(
            [str(ROOT / "run.sh")], cwd=str(ROOT), env=env,
            stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        self.status.setText("正在启动…（首次要加载模型，等 5-10 秒）")

    def stop(self) -> None:
        subprocess.Popen([str(ROOT / "stop.sh")], cwd=str(ROOT),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
        self.status.setText("正在停止…")

    def _reload(self) -> None:
        """设置变了就先停，等端口真的空出来再起。"""
        subprocess.Popen([str(ROOT / "stop.sh")], cwd=str(ROOT),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
        self.status.setText("配置变了，正在重启…")
        QTimer.singleShot(1500, self._restart_when_free)

    def _restart_when_free(self) -> None:
        if port_open(8100) or port_open(8300):
            QTimer.singleShot(1000, self._restart_when_free)
            return
        self.start()

    def _tick(self) -> None:
        self._sync_edit_button()   # 浮层里按 Esc 退出拖动，按钮要跟着变回来
        asr, ui = port_open(8100), port_open(8300)
        overlay = subprocess.run(["pgrep", "-f", "overlay[.]py"],
                                 stdout=subprocess.DEVNULL).returncode == 0
        if asr and ui:
            self.status.setText("运行中：" + ("识别 ✓ " if asr else "识别 ✗ ")
                                + ("字幕页 ✓ " if ui else "字幕页 ✗ ")
                                + ("浮层 ✓" if overlay else "浮层 ✗"))
        elif asr or ui:
            self.status.setText("正在启动…")
        else:
            self.status.setText("已停止")


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("实时字幕")
    w = App()
    w.show()
    w.raise_()
    w.activateWindow()
    if "--start-now" in sys.argv:          # 自测用：跳过点击，直接按配置启动
        QTimer.singleShot(600, w.start)
    if "--grab" in sys.argv:               # 文档配图：抓一张配置窗口
        out = sys.argv[sys.argv.index("--grab") + 1]

        def snap() -> None:
            w.grab().save(out)
            print("saved", out, flush=True)
            app.quit()

        QTimer.singleShot(1200, snap)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
