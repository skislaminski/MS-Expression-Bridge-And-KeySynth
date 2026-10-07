#!/bin/zsh
# Double-click in Finder: starts the bridge and opens the interface in the browser.
# On the first start it also makes "Expression Bridge.app" in this folder, a clickable app with
# its own icon that does nothing but open this script in Terminal. Drag it into the Dock if you
# like. (The app is made here because macOS blocks unsigned apps that come out of a download.)
#   "Start Bridge.command" --app-only    makes the app and does not start the bridge
cd "$(dirname "$0")" || exit 1
if ! .venv/bin/python -c "import mido, rtmidi, yaml" 2>/dev/null; then
  echo "Setting up the Python environment…"
  python3 -m venv .venv && .venv/bin/python -m pip install --quiet -r requirements.txt || exit 1
fi

APP="Expression Bridge.app"
make_app() {
  local iconset size
  iconset="$(mktemp -d)/icon.iconset" || return 1
  mkdir -p "$iconset" "$APP/Contents/MacOS" "$APP/Contents/Resources" || return 1
  for size in 16 32 128 256 512; do
    sips -z $size $size icon.png --out "$iconset/icon_${size}x${size}.png" >/dev/null || return 1
    sips -z $((size * 2)) $((size * 2)) icon.png --out "$iconset/icon_${size}x${size}@2x.png" >/dev/null || return 1
  done
  iconutil -c icns "$iconset" -o "$APP/Contents/Resources/icon.icns" || return 1
  print -r -- "$PWD" > "$APP/Contents/Resources/bridge-folder.txt"      # where this folder is
  cat > "$APP/Contents/MacOS/launcher" <<'LAUNCHER' || return 1
#!/bin/zsh
# Opens "Start Bridge.command" in a Terminal window; the folder is the one the app was made in.
folder="$(cat "$(dirname "$0")/../Resources/bridge-folder.txt")"
open -a Terminal "$folder/Start Bridge.command"
LAUNCHER
  chmod +x "$APP/Contents/MacOS/launcher" || return 1
  cat > "$APP/Contents/Info.plist" <<'PLIST' || return 1
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Expression Bridge</string>
  <key>CFBundleDisplayName</key><string>Expression Bridge</string>
  <key>CFBundleIdentifier</key><string>local.expression-bridge.launcher</string>
  <key>CFBundleExecutable</key><string>launcher</string>
  <key>CFBundleIconFile</key><string>icon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>LSUIElement</key><true/>
</dict>
</plist>
PLIST
}
# Made again if this folder was moved: the app remembers where it was made.
if [ -f icon.png ] && [ "$(cat "$APP/Contents/Resources/bridge-folder.txt" 2>/dev/null)" != "$PWD" ]; then
  rm -rf "$APP"
  if make_app; then
    echo "Made “$APP”: from now on you can start the bridge by double-clicking it."
  else
    rm -rf "$APP"
    echo "Could not make “$APP” – keep starting the bridge with this file."
  fi
fi
[ "$1" = "--app-only" ] && exit 0

exec .venv/bin/python bridge.py --open
