#!/bin/bash
#
# Stremio Local Library — installer
#
# Serves media files from this Mac to remote Stremio clients (Android TV, phone) over HTTPS
# with HTTP Range support, so playback has full seek. Reuses the Let's Encrypt certificate
# Stremio's own streaming server maintains, so the TV trusts it with no extra setup.
#
#   ./install.sh
#
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="$HOME/.stremio-local-addon"
LABEL="com.local.stremio-library"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
CERT="$HOME/Library/Application Support/stremio-server/httpsCert.json"
PORT=12471

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
die()  { printf '  \033[31m✗\033[0m %s\n' "$*" >&2; exit 1; }

bold "Stremio Local Library — installer"
echo

# ---------------------------------------------------------------- preflight
bold "Checking prerequisites"

[ "$(uname -s)" = "Darwin" ] || die "This installer is macOS only."
ok "macOS $(sw_vers -productVersion)"

if ! command -v node >/dev/null 2>&1; then
  warn "Node.js is not installed."
  if command -v brew >/dev/null 2>&1; then
    read -r -p "  Install it now with Homebrew? [y/N] " reply
    case "$reply" in
      [yY]*) brew install node ;;
      *) die "Node.js is required. Install it, then re-run this script." ;;
    esac
  else
    die "Node.js is required. Install Homebrew (https://brew.sh) then 'brew install node', or get Node from https://nodejs.org — then re-run."
  fi
fi

NODE="$(command -v node)"
NODE_VER="$(node -p 'process.versions.node')"
NODE_MAJOR="${NODE_VER%%.*}"
NODE_MINOR="$(echo "$NODE_VER" | cut -d. -f2)"
if [ "$NODE_MAJOR" -lt 22 ] || { [ "$NODE_MAJOR" -eq 22 ] && [ "$NODE_MINOR" -lt 5 ]; }; then
  die "Node 22.5 or newer is required (found $NODE_VER) — it provides the built-in sqlite module."
fi
ok "Node $NODE_VER at $NODE"

[ -d "/Applications/Stremio.app" ] || die "Stremio desktop is not installed in /Applications. Get it from https://www.stremio.com then re-run."
ok "Stremio desktop found"

if [ ! -f "$CERT" ]; then
  echo
  warn "Stremio has not yet issued its HTTPS certificate."
  echo "     Do this first, then re-run the installer:"
  echo "       1. Open Stremio desktop"
  echo "       2. Settings -> Streaming"
  echo "       3. Set 'Streaming HTTPS endpoint' to your Mac's LAN IP address"
  echo "       4. Wait a few seconds for it to fetch the certificate"
  exit 1
fi
CERT_DOMAIN="$(node -e "console.log(JSON.parse(require('fs').readFileSync('$CERT','utf8')).domain)")"
ok "HTTPS certificate present ($CERT_DOMAIN)"

# ---------------------------------------------------------------- install
echo
bold "Installing"

mkdir -p "$INSTALL_DIR"
for f in addon.mjs autoheal.mjs auth.mjs urls.sh; do
  cp "$SRC_DIR/src/$f" "$INSTALL_DIR/$f"
done
chmod +x "$INSTALL_DIR/urls.sh"
ok "Files installed to $INSTALL_DIR"

# The addon scans these by default; create them so a fresh Mac has somewhere to drop media.
mkdir -p "$HOME/Media/Movies"
ok "Media folder ready at $HOME/Media/Movies"

mkdir -p "$HOME/Library/LaunchAgents"
sed -e "s|__NODE__|$NODE|g" -e "s|__DIR__|$INSTALL_DIR|g" \
  "$SRC_DIR/templates/launchagent.plist.template" > "$PLIST"
plutil -lint "$PLIST" >/dev/null || die "Generated launch agent is invalid."
ok "Launch agent written to $PLIST"

# ---------------------------------------------------------------- start
echo
bold "Starting the service"

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST" 2>/dev/null \
  || launchctl load -w "$PLIST" 2>/dev/null \
  || die "Could not load the launch agent."

for _ in $(seq 1 30); do
  if lsof -nP -iTCP:$PORT -sTCP:LISTEN >/dev/null 2>&1; then break; fi
  sleep 1
done
lsof -nP -iTCP:$PORT -sTCP:LISTEN >/dev/null 2>&1 \
  || die "Service did not start. Check $INSTALL_DIR/addon.log"
ok "Listening on port $PORT"

# Give the self-heal a moment to register the addon on the Stremio account.
for _ in $(seq 1 20); do
  grep -q "autoheal:" "$INSTALL_DIR/addon.log" 2>/dev/null && break
  sleep 1
done

STATUS="$(curl -s -m 10 -o /dev/null -w '%{http_code}' "https://$CERT_DOMAIN:$PORT/manifest.json" || true)"
[ "$STATUS" = "200" ] && ok "Addon responding over HTTPS" || warn "Addon did not answer over HTTPS yet (got '$STATUS')"

# ---------------------------------------------------------------- done
echo
bold "Done"
echo
if grep -q "autoheal: INSTALLED\|autoheal: URL updated\|autoheal: account URL already correct" "$INSTALL_DIR/addon.log" 2>/dev/null; then
  echo "  The addon has been added to your Stremio account automatically."
  echo "  It will follow this Mac onto any network from now on."
else
  echo "  Sign in to Stremio desktop, then run:  $INSTALL_DIR/urls.sh"
  echo "  and paste the install URL under Addons."
fi
echo
echo "  On your TV:  Settings -> Server -> URL"
echo "      https://$CERT_DOMAIN:12470/"
echo
echo "  Whenever you change wifi, run this to see the current URL:"
echo "      $INSTALL_DIR/urls.sh"
echo
echo "  Logs:      tail -f $INSTALL_DIR/addon.log"
echo "  Uninstall: $SRC_DIR/uninstall.sh"
echo
