#!/bin/bash
# SwiftBar plugin — menu bar status for the Stremio Local Library addon and the torrent server.
# Shows whether the servers are up, and copies the URLs for the current network on click.
#
# <xbar.title>Stremio Local Library</xbar.title>
# <xbar.desc>Status and URLs for the Stremio Local Library addon.</xbar.desc>
# <swiftbar.hideAbout>true</swiftbar.hideAbout>
# <swiftbar.hideRunInTerminal>true</swiftbar.hideRunInTerminal>
# <swiftbar.hideDisablePlugin>true</swiftbar.hideDisablePlugin>
#
# SwiftBar runs this with a bare PATH, so everything here is a stock macOS tool by absolute
# path — no node, and no Homebrew coreutils shadowing the BSD ones.
INSTALL_DIR="$HOME/.stremio-local-addon"
LABEL="com.local.stremio-library"
TLABEL="com.local.stremio-torrent"
LOG="$INSTALL_DIR/addon.log"
TLOG="$INSTALL_DIR/torrent.log"
CERT="$HOME/Library/Application Support/stremio-server/httpsCert.json"
SELF="${SWIFTBAR_PLUGIN_PATH:-$0}"

# Menu actions re-invoke this script: `copy <text> <what>` puts text on the clipboard.
if [ "${1:-}" = "copy" ]; then
  printf '%s' "$2" | /usr/bin/pbcopy
  /usr/bin/osascript -e "display notification \"$2\" with title \"Copied $3\"" >/dev/null 2>&1
  exit 0
fi

IP=$(/usr/sbin/ipconfig getifaddr en0 2>/dev/null || /usr/sbin/ipconfig getifaddr en1 2>/dev/null)
DOMAIN=$(/usr/bin/plutil -extract domain raw -o - "$CERT" 2>/dev/null)
HASH=${DOMAIN#*.}

footer() {
  echo "---"
  echo "Open dashboard | href=http://localhost:11472/ui sfimage=gauge.with.dots.needle.67percent"
  echo "Open Stremio | bash=/usr/bin/open param1=-a param2=Stremio terminal=false refresh=true sfimage=play.rectangle"
  echo "Restart addon | bash=/bin/launchctl param1=kickstart param2=-k param3=gui/$(/usr/bin/id -u)/$LABEL terminal=false refresh=true sfimage=arrow.clockwise"
  echo "Restart torrent server | bash=/bin/launchctl param1=kickstart param2=-k param3=gui/$(/usr/bin/id -u)/$TLABEL terminal=false refresh=true sfimage=arrow.clockwise"
  echo "Open log | bash=/usr/bin/open param1=-a param2=Console param3=\"$LOG\" terminal=false sfimage=doc.text"
  echo "Open torrent log | bash=/usr/bin/open param1=-a param2=Console param3=\"$TLOG\" terminal=false sfimage=doc.text"
  echo "Open Media folder | bash=/usr/bin/open param1=\"$HOME/Media\" terminal=false sfimage=folder"
}

if [ -z "$IP" ] || [ -z "$DOMAIN" ]; then
  echo "| sfimage=play.tv sfcolor=gray"
  echo "---"
  [ -z "$IP" ] && echo "No network — connect to wifi"
  [ -z "$DOMAIN" ] && echo "No HTTPS certificate — open Stremio desktop once"
  footer
  exit 0
fi

HOST="$(echo "$IP" | /usr/bin/tr . -).$HASH"
TV_URL="https://$HOST:12472/"
ADDON_URL="https://$HOST:12471/manifest.json"

# Probe the LAN address the TV would use, under the real hostname so TLS is checked too.
# --resolve skips the public DNS lookup, so a flaky internet link cannot fake an outage.
probe() { # port path
  /usr/bin/curl -s -m 3 -o /dev/null -w '%{http_code}' --resolve "$HOST:$1:$IP" "https://$HOST:$1$2" 2>/dev/null
}
[ "$(probe 12471 /manifest.json)" = "200" ] && ADDON_UP=1 || ADDON_UP=0
[ "$(probe 12470 /settings)" = "200" ] && SERVER_UP=1 || SERVER_UP=0
[ "$(probe 12472 /torrentd/health)" = "200" ] && TORRENT_UP=1 || TORRENT_UP=0

if [ $ADDON_UP = 0 ] || [ $TORRENT_UP = 0 ]; then COLOR=red
elif [ $SERVER_UP = 0 ]; then COLOR=yellow
else COLOR=green; fi
echo "| sfimage=play.tv.fill sfcolor=$COLOR"
echo "---"

if [ $ADDON_UP = 1 ]; then
  echo "Local Library :12471 — running | sfimage=checkmark.circle.fill sfcolor=green refresh=true"
elif /usr/sbin/lsof -nP -iTCP:12471 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Local Library :12471 — listening but not answering over HTTPS | sfimage=exclamationmark.triangle.fill sfcolor=red refresh=true"
else
  echo "Local Library :12471 — not running | sfimage=xmark.circle.fill sfcolor=red refresh=true"
fi
if [ $TORRENT_UP = 1 ]; then
  echo "Torrent server :12472 — running | sfimage=checkmark.circle.fill sfcolor=green refresh=true"
else
  echo "Torrent server :12472 — not running | sfimage=xmark.circle.fill sfcolor=red refresh=true"
fi
if [ $SERVER_UP = 1 ]; then
  echo "Stremio server :12470 — running | sfimage=checkmark.circle.fill sfcolor=green refresh=true"
else
  echo "Stremio server :12470 — closed (torrents still play; no transcoding) | sfimage=moon.circle.fill sfcolor=yellow refresh=true"
fi
echo "LAN IP $IP | sfimage=wifi refresh=true"

echo "---"
echo "TV  ›  Settings  ›  Server URL | size=11"
echo "$TV_URL | bash=\"$SELF\" param1=copy param2=$TV_URL param3=\"TV Server URL\" terminal=false sfimage=doc.on.doc"
echo "Addon install URL | size=11"
echo "$ADDON_URL | bash=\"$SELF\" param1=copy param2=$ADDON_URL param3=\"addon install URL\" terminal=false sfimage=doc.on.doc"
# The dashboard opens on other devices only when torrentd.json says "dashboard_lan": true.
if /usr/bin/grep -q '"dashboard_lan"[[:space:]]*:[[:space:]]*true' "$INSTALL_DIR/torrentd.json" 2>/dev/null; then
  PHONE_URL="https://$HOST:12472/ui"
  echo "Dashboard on your phone | size=11"
  echo "$PHONE_URL | bash=\"$SELF\" param1=copy param2=$PHONE_URL param3=\"dashboard URL\" terminal=false sfimage=iphone"
fi

# Last library scan and last self-heal result, straight from the addon's log.
if [ -f "$LOG" ]; then
  RECENT=$(/usr/bin/tail -n 400 "$LOG")
  LIB=$(echo "$RECENT" | /usr/bin/grep ' library: ' | /usr/bin/tail -n 1)
  HEAL=$(echo "$RECENT" | /usr/bin/grep ' autoheal: ' | /usr/bin/tail -n 1)
  echo "---"
  [ -n "$LIB" ] && echo "Library: ${LIB#* — } | sfimage=film refresh=true"
  if [ -n "$HEAL" ]; then
    WHEN=$(/bin/date -j -f '%Y-%m-%dT%H:%M:%S%z' "${HEAL%%.*}+0000" '+%b %-d %H:%M' 2>/dev/null)
    case "$HEAL" in
      *"already correct"*) MSG="account URL is correct" ;;
      *"URL updated"*)     MSG="account URL updated for this network" ;;
      *"entry updated"*)   MSG="account entry refreshed" ;;
      *INSTALLED*)         MSG="addon added to your account" ;;
      *"cannot read authKey"*) MSG="signed out of Stremio — cannot update account" ;;
      *) MSG=$(echo "${HEAL#* autoheal: }" | /usr/bin/tr '|' '/' | /usr/bin/cut -c1-70) ;;
    esac
    echo "Self-heal: $MSG${WHEN:+ ($WHEN)} | sfimage=cross.case refresh=true"
  fi
fi

footer
