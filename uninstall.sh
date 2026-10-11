#!/bin/bash
#
# Stremio Local Library — uninstaller
#
set -euo pipefail

INSTALL_DIR="$HOME/.stremio-local-addon"
LABEL="com.local.stremio-library"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
TLABEL="com.local.stremio-torrent"
TPLIST="$HOME/Library/LaunchAgents/$TLABEL.plist"

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }

bold "Stremio Local Library — uninstaller"
echo

# Remove the addons from the Stremio account while the code is still present to do it.
if [ -f "$INSTALL_DIR/auth.mjs" ]; then
  read -r -p "Remove the addons from your Stremio account too? [Y/n] " reply
  case "$reply" in
    [nN]*) warn "Left in your account — remove Local Library and Stream Advisor under Addons in Stremio." ;;
    *)
      node --disable-warning=ExperimentalWarning --input-type=module -e "
        import fs from 'node:fs';
        import { readAuthKey } from '$INSTALL_DIR/auth.mjs';
        const key = readAuthKey();
        if (!key) { console.log('  ! Not signed in — skipping account cleanup.'); process.exit(0); }
        const api = async (type, extra = {}) => {
          const r = await fetch('https://api.strem.io/api/' + type[0].toLowerCase() + type.slice(1), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ type, authKey: key, ...extra }),
          });
          const j = await r.json();
          if (j.error) throw new Error(j.error.message ?? JSON.stringify(j.error));
          return j.result;
        };
        const ours = ['org.local.library', 'org.local.advisor'];
        const { addons } = await api('AddonCollectionGet', { update: true });
        // The advisor may have stood in for Torrentio: put the addons it rated back where it was.
        let upstreams = [];
        try { upstreams = JSON.parse(fs.readFileSync('$INSTALL_DIR/advisor.json', 'utf8')).upstreams ?? []; } catch {}
        const restored = [];
        for (const u of upstreams) {
          if (!addons.some((a) => a?.manifest?.id === 'org.local.advisor')) break;
          if (addons.some((a) => a.transportUrl === u.url)) continue;
          let manifest = null;
          try {
            const r = await fetch(u.url, { headers: { 'User-Agent': 'Mozilla/5.0 (Macintosh) Chrome/129.0 Safari/537.36' },
              signal: AbortSignal.timeout(15000) });
            manifest = await r.json();
          } catch { console.log('  ! Could not fetch ' + (u.name ?? 'an upstream addon') + ' to put it back — reinstall it in Stremio.'); }
          if (!manifest?.id || addons.some((a) => a?.manifest?.id === manifest.id)) continue;
          restored.push({ transportUrl: u.url, transportName: '', manifest, flags: { official: false, protected: false } });
        }
        const keep = addons.flatMap((a) => a?.manifest?.id === 'org.local.advisor' ? restored
          : ours.includes(a?.manifest?.id) ? [] : [a]);
        if (keep.length === addons.length && !restored.length) { console.log('  ✓ Not in your account already.'); process.exit(0); }
        fs.mkdirSync('$INSTALL_DIR/collection-backups', { recursive: true });
        fs.writeFileSync('$INSTALL_DIR/collection-backups/before-uninstall.json', JSON.stringify({ addons }, null, 2), { mode: 0o600 });
        await api('AddonCollectionSet', { addons: keep });
        console.log('  ✓ Removed from your Stremio account (' + addons.length + ' -> ' + keep.length + ' addons)'
          + (restored.length ? '; put back ' + restored.map((a) => a.manifest.name).join(', ') : ''));
      " 2>/dev/null || warn "Account cleanup failed — remove Local Library and Stream Advisor under Addons in Stremio."
      ;;
  esac
fi

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl unload -w "$PLIST" 2>/dev/null || true
ok "Service stopped"

rm -f "$PLIST"
ok "Launch agent removed"

launchctl bootout "gui/$(id -u)/$TLABEL" 2>/dev/null || true
launchctl unload -w "$TPLIST" 2>/dev/null || true
rm -f "$TPLIST"
ok "Torrent server stopped and removed — set the TV's Server URL back to port 12470"

# The menu bar plugin may live in SwiftBar's own plugin folder, outside $INSTALL_DIR.
PLUGIN_DIR="$(defaults read com.ameba.SwiftBar PluginDirectory 2>/dev/null || true)"
if [ -n "$PLUGIN_DIR" ] && [ -f "$PLUGIN_DIR/stremio-library.30s.sh" ]; then
  rm -f "$PLUGIN_DIR/stremio-library.30s.sh"
  ok "Menu bar icon removed (SwiftBar itself was left installed)"
fi

if [ -d "$INSTALL_DIR" ]; then
  CACHE_SIZE="$(du -sh "$INSTALL_DIR/torrent-cache" 2>/dev/null | cut -f1 || true)"
  read -r -p "Delete $INSTALL_DIR (logs, backups, torrent cache ${CACHE_SIZE:-empty})? [y/N] " reply
  case "$reply" in
    [yY]*) rm -rf "$INSTALL_DIR"; ok "Removed $INSTALL_DIR" ;;
    *) warn "Kept $INSTALL_DIR" ;;
  esac
fi

echo
echo "  Your media files were not touched."
echo
