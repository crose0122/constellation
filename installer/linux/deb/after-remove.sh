#!/bin/bash
# Debian postrm for Constellation Setup (electron-builder afterRemove).

# Delete the link to the binary
if type update-alternatives >/dev/null 2>&1; then
    update-alternatives --remove '${executable}' '/usr/bin/${executable}'
else
    rm -f '/usr/bin/${executable}'
fi

# Unload and delete the AppArmor profile only when the package is really going away
# (not on upgrade, where the new postinst reloads the new profile).
case "$1" in
    remove|purge)
        if [ -d /sys/kernel/security/apparmor ] && command -v apparmor_parser >/dev/null 2>&1; then
            apparmor_parser -R /etc/apparmor.d/constellation-setup 2>/dev/null || true
        fi
        rm -f /etc/apparmor.d/constellation-setup
        ;;
esac
