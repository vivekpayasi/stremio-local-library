#!/bin/bash
#
# Stremio Local Library — uninstaller
#
set -euo pipefail

INSTALL_DIR="$HOME/.stremio-local-addon"
LABEL="com.local.stremio-library"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }

bold "Stremio Local Library — uninstaller"
echo

# Remove the addon from the Stremio account while the code is still present to do it.
if [ -f "$INSTALL_DIR/auth.mjs" ]; then
  read -r -p "Remove the addon from your Stremio account too? [Y/n] " reply
  case "$reply" in
    [nN]*) warn "Left in your account — remove it under Addons in Stremio." ;;
    *)
      node --disable-warning=ExperimentalWarning --input-type=module -e "
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
        const { addons } = await api('AddonCollectionGet', { update: true });
        const keep = addons.filter((a) => a?.manifest?.id !== 'org.local.library');
        if (keep.length === addons.length) { console.log('  ✓ Not in your account already.'); process.exit(0); }
        await api('AddonCollectionSet', { addons: keep });
        console.log('  ✓ Removed from your Stremio account (' + addons.length + ' -> ' + keep.length + ' addons)');
      " 2>/dev/null || warn "Account cleanup failed — remove it under Addons in Stremio."
      ;;
  esac
fi

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl unload -w "$PLIST" 2>/dev/null || true
ok "Service stopped"

rm -f "$PLIST"
ok "Launch agent removed"

if [ -d "$INSTALL_DIR" ]; then
  read -r -p "Delete $INSTALL_DIR (logs, caches, backups)? [y/N] " reply
  case "$reply" in
    [yY]*) rm -rf "$INSTALL_DIR"; ok "Removed $INSTALL_DIR" ;;
    *) warn "Kept $INSTALL_DIR" ;;
  esac
fi

echo
echo "  Your media files were not touched."
echo
