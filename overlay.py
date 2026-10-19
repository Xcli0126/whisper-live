#!/usr/bin/env python3
"""桌面字幕浮层（Qt6 / PySide6，走 XWayland），盖在电影画面上。

设计要点：
  * Qt.FramelessWindowHint + WindowStaysOnTopHint + Tool —— 无边框、置顶、不进任务栏
  * Qt.WindowTransparentForInput                          —— 平时鼠标穿透，片子照样能暂停
  * 自绘文字（QPainterPath + 黑色描边 + 白色填充）        —— 字幕那种「白字黑边」，亮画面/白底都能看清
  * 背后可选一块半透明灰/黑蒙版                            —— 画面花的地方垫一层，字更清楚
  * 编辑模式（配置窗口里点「调整字幕位置」）               —— 临时接收鼠标，拖到哪存到哪

外观与位置都存在 ~/.config/whisper-live/overlay.json，改完 0.4 秒内生效，不用重启。
字幕内容来自 subtitler.py 的 GET /snap（JSON），每 0.15 s 轮询一次。
"""
import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "xcb")
os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")

from PySide6.QtCore import QPoint, QRectF, Qt, QTimer  # noqa: E402
from PySide6.QtGui import (  # noqa: E402
    QColor,
    QFont,
    QFontMetricsF,
    QGuiApplication,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

SNAP = os.environ.get("SNAP_URL", "http://127.0.0.1:8300/snap")
# 屏幕上到底出现了什么，由浮层自己记：哪一行、什么时候换的、上一版露了多久。
SCREEN_LOG = os.environ.get(
    "SCREEN_LOG", os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "screen.log")
)
CONF = Path(os.environ.get("OVERLAY_CONF", os.path.expanduser("~/.config/whisper-live/overlay.json")))
INTERVAL_MS = 150
CONF_MS = 400
PAD = 20
GAP = 0.18            # 两行之间的空隙，按字号比例算
BOTTOM_MARGIN = int(os.environ.get("BOTTOM_MARGIN", "60"))
WIDTH_RATIO = 0.86

DEFAULTS = {
    "x": None,          # None = 底部居中
    "y": None,
    "scale": 1.0,       # 字号倍数
    "mask": "gray",     # none | gray | black
    "mask_alpha": 0.55,
    "dwell": 9.0,       # 最长驻留秒数，0 = 不自动隐藏
    "edit": False,      # 拖动模式
}
MASK_COLORS = {"gray": (58, 58, 62), "black": (0, 0, 0)}


def s_show(sec: float) -> str:
    return f"{sec:5.2f}s"


def load_conf() -> dict:
    try:
        raw = json.loads(CONF.read_text("utf-8"))
    except Exception:
        raw = {}
    return {**DEFAULTS, **{k: v for k, v in raw.items() if k in DEFAULTS}}


def save_conf(conf: dict) -> None:
    CONF.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONF.with_suffix(".tmp")
    tmp.write_text(json.dumps(conf, ensure_ascii=False, indent=2), "utf-8")
    tmp.replace(CONF)


TOKEN = re.compile(r"[\u3000-\u303f\u4e00-\u9fff\uff00-\uffef]|[^\s\u3000-\u303f\u4e00-\u9fff\uff00-\uffef]+|\s+")


def wrap(text: str, fm: QFontMetricsF, width: float) -> list:
    """按像素宽度折行：CJK 逐字可断，拉丁单词整体走。"""
    lines, cur = [], ""
    for tok in TOKEN.findall(text):
        trial = cur + tok
        if fm.horizontalAdvance(trial) <= width or not cur.strip():
            cur = trial
        else:
            lines.append(cur.rstrip())
            cur = tok.lstrip()
    if cur.strip():
        lines.append(cur.rstrip())
    return lines or [""]


class Overlay(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.conf = load_conf()
        self._conf_mtime = self._stat()
        self._edit = bool(self.conf["edit"])
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_AlwaysStackOnTop, True)
        self._apply_flags()

        self._text = {"zh": "", "en": ""}
        self._since = {"zh": time.monotonic(), "en": time.monotonic()}
        self._t0 = time.monotonic()
        self._log_f = open(SCREEN_LOG, "a", buffering=1, encoding="utf-8")
        self._log(f"--- 浮层启动 pid={os.getpid()} ---")
        self._last_v = -1
        self._last_change = time.monotonic()
        self._hidden = False
        self._drag_from = None
        self._blocks = []   # [(font, color, [line,...])]

        self._relayout()
        self.show()

        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll)
        self._poll_timer.start(INTERVAL_MS)

        self._conf_timer = QTimer(self)
        self._conf_timer.timeout.connect(self._reload_conf)
        self._conf_timer.start(CONF_MS)

        self._fade_timer = QTimer(self)
        self._fade_timer.timeout.connect(self._maybe_fade)
        self._fade_timer.start(500)

    # ---------- 窗口标志 ----------
    def _apply_flags(self) -> None:
        flags = Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint
        if not self._edit:                      # 编辑时不再穿透，否则拖不动
            flags |= Qt.WindowTransparentForInput
        self.setWindowFlags(flags)

    # ---------- 配置 ----------
    def _stat(self) -> float:
        try:
            return CONF.stat().st_mtime
        except OSError:
            return 0.0

    def _reload_conf(self) -> None:
        mtime = self._stat()
        if mtime == self._conf_mtime:
            return
        self._conf_mtime = mtime
        old_edit = self._edit
        self.conf = load_conf()
        self._edit = bool(self.conf["edit"])
        if self._edit != old_edit:
            self._apply_flags()
            self.show()
            self.raise_()
            QTimer.singleShot(0, self._reapply_geometry)   # 换窗口标志后 WM 会重摆一次
            self._log("--- 进入拖动模式 ---" if self._edit else "--- 退出拖动模式 ---")
        self._relayout()

    def _reapply_geometry(self) -> None:
        if self.conf["x"] is not None and self.conf["y"] is not None:
            self.move(int(self.conf["x"]), int(self.conf["y"]))
        self.raise_()
        self._log(f"按配置摆到 {self.x()},{self.y()}")

    # ---------- 排版 ----------
    def _fonts(self) -> tuple:
        base = max(30.0, min(68.0, QGuiApplication.primaryScreen().geometry().height() * 0.030))
        base *= float(self.conf["scale"])
        f_zh = QFont("Noto Sans CJK SC")
        f_zh.setPixelSize(int(base))
        f_zh.setWeight(QFont.Weight(500))
        f_en = QFont("Noto Sans CJK SC")
        f_en.setPixelSize(max(16, int(base * 0.58)))
        return f_zh, f_en, base

    def _relayout(self) -> None:
        screen = QGuiApplication.primaryScreen().geometry()
        width = int(screen.width() * WIDTH_RATIO)
        f_zh, f_en, base = self._fonts()
        avail = width - PAD * 2
        blocks = []
        for which, font, color in (("zh", f_zh, QColor(255, 255, 255, 255)),
                                   ("en", f_en, QColor(228, 232, 238, 240))):
            text = self._text[which]
            if not text:
                continue
            blocks.append((font, color, wrap(text, QFontMetricsF(font), avail)))
        height = PAD * 2
        for font, _, lines in blocks:
            height += QFontMetricsF(font).height() * len(lines)
        if len(blocks) > 1:
            height += int(base * GAP)
        if not blocks:                      # 没字幕时给拖动模式留一小块抓手
            height = max(int(base * 1.4), 64)
        self._blocks = blocks
        self.resize(width, int(height))
        if self.conf["x"] is None or self.conf["y"] is None:
            self.move(screen.x() + (screen.width() - width) // 2,
                      screen.y() + screen.height() - int(height) - BOTTOM_MARGIN)
        else:
            self.move(int(self.conf["x"]), int(self.conf["y"]))
        self._log(f"布局 {self.width()}x{self.height()} @ {self.x()},{self.y()} "
                  f"mask={self.conf['mask']} scale={self.conf['scale']} "
                  f"screen={screen.width()}x{screen.height()} dpr={self.devicePixelRatioF():g} "
                  f"geom={self.geometry().x()},{self.geometry().y()}")
        self.update()

    # ---------- 绘制 ----------
    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.TextAntialiasing, True)
        rect = QRectF(0, 0, self.width(), self.height())
        if self._blocks and self.conf["mask"] in MASK_COLORS:
            r, g, b = MASK_COLORS[self.conf["mask"]]
            alpha = int(255 * float(self.conf["mask_alpha"]))
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(r, g, b, max(0, min(255, alpha))))
            rr = rect.adjusted(2, 2, -2, -2)
            p.drawRoundedRect(rr, 14, 14)

        top = PAD
        for font, color, lines in self._blocks:
            fm = QFontMetricsF(font)
            px = font.pixelSize()
            outline = max(3.0, px * 0.11)
            for line in lines:
                if not line:
                    continue
                x = (self.width() - fm.horizontalAdvance(line)) / 2
                path = QPainterPath()
                path.addText(x, top + fm.ascent(), font, line)
                # 先垫一层下沉的硬阴影，白底/亮画面上也有轮廓
                shadow = QPainterPath(path)
                shadow.translate(0, max(2.0, px * 0.05))
                p.fillPath(shadow, QColor(0, 0, 0, 120))
                pen = QPen(QColor(0, 0, 0, 255))
                pen.setWidthF(outline * 2)      # 描边压在字外侧，宽度翻倍才够一圈
                pen.setJoinStyle(Qt.RoundJoin)
                pen.setCapStyle(Qt.RoundCap)
                p.strokePath(path, pen)
                p.fillPath(path, color)
                top += fm.height()

        if self._edit:
            pen = QPen(QColor(0, 180, 255, 220))
            pen.setWidth(2)
            pen.setStyle(Qt.DashLine)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 12, 12)
            if not self._blocks:
                p.setPen(QColor(200, 220, 255))
                p.drawText(rect, Qt.AlignCenter, "拖到想要的位置，按 Esc 结束")

    # ---------- 拖动 ----------
    def mousePressEvent(self, ev) -> None:
        if self._edit and ev.button() == Qt.LeftButton:
            self._drag_from = ev.globalPosition().toPoint() - self.frameGeometry().topLeft()
            ev.accept()

    def mouseMoveEvent(self, ev) -> None:
        if self._edit and self._drag_from is not None:
            self.move(ev.globalPosition().toPoint() - self._drag_from)
            ev.accept()

    def mouseReleaseEvent(self, ev) -> None:
        if self._edit and self._drag_from is not None:
            self._drag_from = None
            self.conf["x"], self.conf["y"] = self.x(), self.y()
            save_conf(self.conf)
            self._conf_mtime = self._stat()
            self._log(f"位置已保存 x={self.x()} y={self.y()}")
            ev.accept()

    def keyPressEvent(self, ev) -> None:
        if self._edit and ev.key() in (Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down):
            # 鼠标穿透在个别合成环境里不灵，方向键也能挪（按住 Shift 快一点）
            step = 40 if ev.modifiers() & Qt.ShiftModifier else 10
            dx = -step if ev.key() == Qt.Key_Left else step if ev.key() == Qt.Key_Right else 0
            dy = -step if ev.key() == Qt.Key_Up else step if ev.key() == Qt.Key_Down else 0
            self.move(self.x() + dx, self.y() + dy)
            self.conf["x"], self.conf["y"] = self.x(), self.y()
            save_conf(self.conf)
            self._conf_mtime = self._stat()
            ev.accept()
            return
        if ev.key() == Qt.Key_Escape and self._edit:
            self.conf["edit"] = False
            save_conf(self.conf)
            self._conf_mtime = self._stat()
            self._edit = False
            self._apply_flags()
            self.show()
            self._log("--- 退出拖动模式 ---")
            ev.accept()
        else:
            super().keyPressEvent(ev)

    # ---------- 屏幕日志 ----------
    def _log(self, msg: str) -> None:
        stamp = time.strftime("%H:%M:%S", time.localtime())
        self._log_f.write(f"{stamp} [{time.monotonic() - self._t0:7.2f}] {msg}\n")

    def _set(self, which: str, text: str) -> None:
        """换掉某一行，并记下上一版在屏幕上停留了多久。"""
        if text == self._text[which]:
            return
        now = time.monotonic()
        shown = now - self._since[which]
        prev = self._text[which]
        self._text[which] = text
        self._since[which] = now
        self._log(f"{which}  上一版{s_show(shown)}  {prev!r} -> {text!r}")
        self._relayout()

    # ---------- 数据 ----------
    def _poll(self) -> None:
        try:
            with urllib.request.urlopen(SNAP, timeout=1.5) as resp:
                data = json.loads(resp.read().decode())
        except Exception as exc:  # 服务没起来就让窗口空着
            if self._last_v != -2:
                self._last_v = -2
                self._set("zh", "等待字幕服务…")
                self._set("en", str(exc)[:60])
            return
        if data.get("v", 0) == self._last_v:
            return
        self._last_v = data.get("v", 0)
        self._render(data)

    def _render(self, data: dict) -> None:
        # 两行各管各的：中文行只被新的中文替换，英文行只被新的英文替换。
        src = (data.get("cap_src") or "").strip()
        zh = (data.get("cap_zh") or "").strip()
        same = bool(data.get("same_lang"))
        if same:              # 源语言就是中文：只有一行
            zh, src = src, ""
        if src:
            self._set("en", src)
        if zh:
            self._set("zh", zh)
        elif src:             # 一句译文都还没有时先用源语言顶上，不留空行
            self._set("zh", src)
        self._last_change = time.monotonic()
        if self._hidden:
            self.show()
            self._hidden = False
            self._log("--- 浮层重新显示 ---")

    def _maybe_fade(self) -> None:
        dwell = float(self.conf["dwell"])
        if dwell <= 0 or self._edit:
            return
        if not self._hidden and time.monotonic() - self._last_change > dwell:
            self.hide()
            self._hidden = True
            self._log(f"--- 超过 {dwell:g} 秒没有新字幕，浮层隐藏 ---")


def main() -> int:
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    ov = Overlay()
    ov.show()
    if "--grab" in sys.argv:                       # 文档配图：抓一张浮层自己的渲染结果
        path = sys.argv[sys.argv.index("--grab") + 1]

        def snap() -> None:
            pm = ov.grab()
            if "--backdrop" in sys.argv:
                bg = QPixmap(pm.size())
                p = QPainter(bg)
                grad = QLinearGradient(0, 0, 0, bg.height())
                grad.setColorAt(0.0, QColor(30, 37, 48))
                grad.setColorAt(1.0, QColor(12, 14, 18))
                p.fillRect(bg.rect(), grad)
                p.drawPixmap(0, 0, pm)
                p.end()
                pm = bg
            pm.save(path)
            print("saved", path, pm.width(), pm.height(), flush=True)
            app.quit()

        QTimer.singleShot(1500, snap)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
