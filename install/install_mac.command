#!/bin/bash
# Install or update RFP Responder for the current Mac user (no admin rights needed).
#   - copies the app to ~/Library/Application Support/RFPResponder/app
#   - creates a private Python environment next to it
#   - creates "RFP Responder" in ~/Applications (start it from Launchpad, Spotlight or Finder)
# Run again to update. Settings, projects and knowledge caches are kept.
#
# Files synced from SharePoint lose their "executable" flag, so run it from Terminal:
#   bash "<drag this file into the Terminal window>"
set -e
SRC="$(cd "$(dirname "$0")/.." && pwd)"
BASE="$HOME/Library/Application Support/RFPResponder"
APP="$BASE/app"
VENV="$BASE/venv"
BUNDLE="$HOME/Applications/RFP Responder.app"

echo "RFP Responder installer"
echo "From: $SRC"
echo "To:   $APP"
echo

# 1. Python 3.11 or newer
PY=""
for c in python3.13 python3.12 python3.11 python3 /opt/homebrew/bin/python3 /usr/local/bin/python3 \
         /Library/Frameworks/Python.framework/Versions/Current/bin/python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
    PY="$(command -v "$c")"; break
  fi
done
if [ -z "$PY" ]; then
  echo "Python 3.11 or newer was not found."
  echo "Install it from https://www.python.org/downloads/macos/ (or: brew install python@3.12), then run this again."
  exit 1
fi
echo "Python: $("$PY" --version)"

# 2. Copy the app (without caches and git data)
mkdir -p "$BASE"
rm -rf "$APP"
mkdir -p "$APP"
for item in responder templates RFPResponder.pyw requirements.txt README.md; do
  [ -e "$SRC/$item" ] && cp -R "$SRC/$item" "$APP/"
done
find "$APP" -name "__pycache__" -type d -prune -exec rm -rf {} +

# 3. Private Python environment
if [ ! -x "$VENV/bin/python" ]; then
  echo "Creating the Python environment..."
  "$PY" -m venv "$VENV"
fi
echo "Installing packages (a minute or two the first time)..."
"$VENV/bin/python" -m pip install --quiet --disable-pip-version-check --upgrade pip
"$VENV/bin/python" -m pip install --quiet --disable-pip-version-check -r "$APP/requirements.txt"

# 4. The app in ~/Applications: a small bundle that starts the app in the background
mkdir -p "$BUNDLE/Contents/MacOS"
cat > "$BUNDLE/Contents/MacOS/RFP Responder" <<'EOF'
#!/bin/bash
D="$HOME/Library/Application Support/RFPResponder"
export PYTHONDONTWRITEBYTECODE=1
exec "$D/venv/bin/python" "$D/app/RFPResponder.pyw" >>"$D/app.log" 2>&1
EOF
chmod +x "$BUNDLE/Contents/MacOS/RFP Responder"
cat > "$BUNDLE/Contents/Info.plist" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>RFP Responder</string>
  <key>CFBundleDisplayName</key><string>RFP Responder</string>
  <key>CFBundleIdentifier</key><string>com.milestonesys.rfpresponder</string>
  <key>CFBundleExecutable</key><string>RFP Responder</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleVersion</key><string>0.3.0</string>
  <key>LSUIElement</key><true/>
</dict></plist>
EOF

# 5. Browser and Claude Code
if [ ! -d "/Applications/Microsoft Edge.app" ] && [ ! -d "/Applications/Google Chrome.app" ]; then
  echo
  echo "Tip: install Microsoft Edge or Google Chrome for the app window. Without them it opens in your default browser."
fi
CLAUDE=""
for c in claude "$HOME/.local/bin/claude" "$HOME/.claude/local/claude" /opt/homebrew/bin/claude /usr/local/bin/claude; do
  if command -v "$c" >/dev/null 2>&1; then CLAUDE="$c"; break; fi
done
if [ -n "$CLAUDE" ]; then
  echo "Claude Code: $("$CLAUDE" --version 2>/dev/null)"
else
  echo
  echo "Claude Code was not found. Install it and sign in with your own account"
  echo "(see https://docs.claude.com/en/docs/claude-code), then start RFP Responder."
fi
echo
echo "Done. Start 'RFP Responder' from Launchpad, Spotlight or ~/Applications."
