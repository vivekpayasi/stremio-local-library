#!/bin/bash
# Prints the Stremio URLs for whatever network this Mac is currently on.
# The hostname encodes the LAN IP, so it changes every time the IP does.
CERT="$HOME/Library/Application Support/stremio-server/httpsCert.json"
IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null)
DOMAIN=$(python3 -c "import json;print(json.load(open('$CERT'))['domain'])" 2>/dev/null)
EXPIRES=$(python3 -c "import json;print(json.load(open('$CERT'))['notAfter'])" 2>/dev/null)

echo "LAN IP        : ${IP:-none}"
echo "cert domain   : ${DOMAIN:-none}   (expires ${EXPIRES:-?})"
if [ "${DOMAIN%%.*}" != "$(echo "$IP" | tr . -)" ]; then
  echo "  !! cert is STALE for this IP — restart Stremio desktop so it re-issues"
fi
echo
echo "TV  Settings -> Server -> URL :"
echo "    https://$DOMAIN:12470/"
echo
echo "Desktop  Addons -> paste to install :"
echo "    https://$DOMAIN:12471/manifest.json"
echo
printf 'streaming server :12470  -> %s\n' "$(curl -s -m 6 -o /dev/null -w 'http=%{http_code} ssl=%{ssl_verify_result}' https://$DOMAIN:12470/settings)"
printf 'local library    :12471  -> %s\n' "$(curl -s -m 6 -o /dev/null -w 'http=%{http_code} ssl=%{ssl_verify_result}' https://$DOMAIN:12471/manifest.json)"
