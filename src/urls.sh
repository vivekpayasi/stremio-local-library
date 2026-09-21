#!/bin/bash
# Prints the Stremio URLs for whatever network this Mac is currently on.
# The hostname encodes the LAN IP, so it changes every time the IP does.
# URLs are built from the LIVE IP plus the cert's install hash (same as autoheal.mjs):
# the cert is a wildcard for that hash, so it stays valid even while the `domain`
# string in httpsCert.json still names the previous network's IP.
CERT="$HOME/Library/Application Support/stremio-server/httpsCert.json"
IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null)
read -r DOMAIN EXPIRES < <(node -e '
  const c = JSON.parse(require("fs").readFileSync(process.argv[1], "utf8"));
  console.log(c.domain, c.notAfter);' "$CERT" 2>/dev/null)
HASH=${DOMAIN#*.}

echo "LAN IP        : ${IP:-none}"
echo "cert domain   : ${DOMAIN:-none}   (expires ${EXPIRES:-?})"
if [ -z "$IP" ] || [ -z "$DOMAIN" ]; then
  [ -z "$IP" ] && echo "  !! no LAN IP — connect to a network first"
  [ -z "$DOMAIN" ] && echo "  !! no httpsCert.json — launch Stremio desktop once so it issues a cert"
  exit 1
fi
HOST="$(echo "$IP" | tr . -).$HASH"
if [ "$DOMAIN" != "$HOST" ]; then
  echo "  (cert file still names the previous IP — harmless, it is a wildcard for *.$HASH)"
fi
echo
echo "TV  Settings -> Server -> URL :"
echo "    https://$HOST:12470/"
echo
echo "Desktop  Addons -> paste to install :"
echo "    https://$HOST:12471/manifest.json"
echo
printf 'streaming server :12470  -> %s\n' "$(curl -s -m 6 -o /dev/null -w 'http=%{http_code} ssl=%{ssl_verify_result}' "https://$HOST:12470/settings")"
printf 'local library    :12471  -> %s\n' "$(curl -s -m 6 -o /dev/null -w 'http=%{http_code} ssl=%{ssl_verify_result}' "https://$HOST:12471/manifest.json")"
