#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0:A")" && pwd)"
TIDYMAC_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
WORKSPACE_DIR="$(cd "$TIDYMAC_DIR/.." && pwd)"
APP_PATH="$WORKSPACE_DIR/TidyMac.app"

rm -rf "$APP_PATH"
mkdir -p "$APP_PATH/Contents/MacOS" "$APP_PATH/Contents/Resources"

if [[ ! -f "$SCRIPT_DIR/TidyMac.icns" && -f "$SCRIPT_DIR/MakeIcon.swift" ]]; then
    ICON_TMP="$(mktemp -d)"
    swiftc -O "$SCRIPT_DIR/MakeIcon.swift" -o "$ICON_TMP/make_icon"
    "$ICON_TMP/make_icon" "$ICON_TMP/TidyMac.iconset" >/dev/null
    iconutil -c icns "$ICON_TMP/TidyMac.iconset" -o "$SCRIPT_DIR/TidyMac.icns"
    rm -rf "$ICON_TMP"
fi

if [[ -f "$SCRIPT_DIR/TidyMac.icns" ]]; then
    cp "$SCRIPT_DIR/TidyMac.icns" "$APP_PATH/Contents/Resources/TidyMac.icns"
fi

swiftc -O "$SCRIPT_DIR/TidyMacApp.swift" -o "$APP_PATH/Contents/MacOS/TidyMac"

cat > "$APP_PATH/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleDevelopmentRegion</key>
    <string>zh_CN</string>
    <key>CFBundleExecutable</key>
    <string>TidyMac</string>
    <key>CFBundleIconFile</key>
    <string>TidyMac</string>
    <key>CFBundleIdentifier</key>
    <string>local.tidymac.workbench</string>
    <key>CFBundleInfoDictionaryVersion</key>
    <string>6.0</string>
    <key>CFBundleName</key>
    <string>TidyMac</string>
    <key>CFBundleDisplayName</key>
    <string>TidyMac</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleShortVersionString</key>
    <string>0.2.0</string>
    <key>CFBundleVersion</key>
    <string>2</string>
    <key>LSMinimumSystemVersion</key>
    <string>13.0</string>
    <key>NSHighResolutionCapable</key>
    <true/>
</dict>
</plist>
PLIST

codesign --force --deep --sign - "$APP_PATH" >/dev/null 2>&1 || true
touch "$APP_PATH"
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$APP_PATH" >/dev/null 2>&1 || true
echo "Built: $APP_PATH"
