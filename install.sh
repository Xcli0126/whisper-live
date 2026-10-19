#!/usr/bin/env bash
# 装进桌面：应用菜单项 + 图标 + ~/.local/bin 启动器。不需要 sudo。
# 卸载：./install.sh --uninstall
set -euo pipefail
cd "$(dirname "$0")"
ROOT=$(pwd)
DESKTOP_DIR="$HOME/.local/share/applications"
ICON_DIR="$HOME/.local/share/icons/hicolor/scalable/apps"
BIN="$HOME/.local/bin/whisper-live"
DESKTOP="$DESKTOP_DIR/whisper-live.desktop"

if [ "${1:-}" = "--uninstall" ]; then
  rm -f "$DESKTOP" "$ICON_DIR/whisper-live.svg" "$BIN"
  update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true
  echo "已卸载（项目目录和模型没动：$ROOT）"
  exit 0
fi

[ -x .venv-qt/bin/python ] || { echo "缺少 .venv-qt" >&2; exit 1; }
chmod +x app.py run.sh stop.sh status.sh

mkdir -p "$DESKTOP_DIR" "$ICON_DIR" "$(dirname "$BIN")"
cp icon.svg "$ICON_DIR/whisper-live.svg"

cat > "$BIN" <<BIN_EOF
#!/usr/bin/env bash
exec "$ROOT/.venv-qt/bin/python" "$ROOT/app.py" "$@"
BIN_EOF
chmod +x "$BIN"

cat > "$DESKTOP" <<DESKTOP_EOF
[Desktop Entry]
Type=Application
Name=实时字幕
Name[en]=Live Subtitles
Comment=把系统正在播放的声音转成字幕和中文翻译
Exec=$ROOT/.venv-qt/bin/python $ROOT/app.py
Icon=whisper-live
Terminal=false
Categories=AudioVideo;Utility;
Keywords=subtitle;caption;translate;字幕;
StartupNotify=true
DESKTOP_EOF

update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true
gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" 2>/dev/null || true

echo "装好了："
echo "  应用菜单里搜「实时字幕」，或者终端跑 whisper-live"
echo "  桌面项 $DESKTOP"
echo "  卸载： ./install.sh --uninstall"
