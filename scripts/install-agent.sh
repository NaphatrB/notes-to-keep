#!/bin/sh
# Install the adaptive drip LaunchAgent for the current user.
#
# The plist is generated locally with the checkout path and $HOME expanded, so
# no username or absolute path is stored in the repository.
set -eu

LABEL="com.example.keep-import"
DIR=$(cd "$(dirname "$0")" && pwd)
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

if [ "$(uname -s)" != "Darwin" ]; then
    echo "error: macOS only (launchd)" >&2
    exit 1
fi
if [ ! -x "$DIR/drip-guard.sh" ]; then
    echo "error: $DIR/drip-guard.sh is not executable" >&2
    exit 1
fi

mkdir -p "$HOME/Library/LaunchAgents"

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/caffeinate</string>
        <string>-is</string>
        <string>$DIR/drip-guard.sh</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$DIR</string>
    <key>StartInterval</key>
    <integer>120</integer>
    <key>RunAtLoad</key>
    <true/>
    <key>ThrottleInterval</key>
    <integer>60</integer>
    <key>EnvironmentVariables</key>
    <dict>
        <key>KEEP_RUNS_PER_DAY</key>
        <string>200</string>
    </dict>
    <key>ProcessType</key>
    <string>Interactive</string>
    <key>StandardOutPath</key>
    <string>$DIR/drip.log</string>
    <key>StandardErrorPath</key>
    <string>$DIR/drip.log</string>
</dict>
</plist>
EOF

plutil -lint "$PLIST"
launchctl unload "$PLIST" 2>/dev/null || true
launchctl load -w "$PLIST"
echo "installed and loaded $PLIST"
