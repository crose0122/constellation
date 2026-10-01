#!/bin/bash
# Debian postinst for Constellation Setup (electron-builder afterInstall).
# electron-builder's default steps are kept below; the AppArmor load is added so the
# app can use its sandbox on Ubuntu 24.04+ without anyone disabling the system-wide
# user-namespace restriction.

if type update-alternatives 2>/dev/null >&1; then
    # Remove previous link if it doesn't use update-alternatives
    if [ -L '/usr/bin/${executable}' -a -e '/usr/bin/${executable}' -a "`readlink '/usr/bin/${executable}'`" != '/etc/alternatives/${executable}' ]; then
        rm -f '/usr/bin/${executable}'
    fi
    update-alternatives --install '/usr/bin/${executable}' '${executable}' '/opt/${sanitizedProductName}/${executable}' 100 || ln -sf '/opt/${sanitizedProductName}/${executable}' '/usr/bin/${executable}'
else
    ln -sf '/opt/${sanitizedProductName}/${executable}' '/usr/bin/${executable}'
fi

# Load (or reload) the shipped profile that grants `userns` to this binary only.
# Skipped when AppArmor is absent or disabled; a failure never aborts installation.
if [ -d /sys/kernel/security/apparmor ] && command -v apparmor_parser >/dev/null 2>&1; then
    apparmor_parser -r -W -T /etc/apparmor.d/constellation-setup || true
fi

# Check if user namespaces are supported by the kernel and working with a quick test:
if ! { [[ -L /proc/self/ns/user ]] && unshare --user true; }; then
    # Use SUID chrome-sandbox only on systems without user namespaces:
    chmod 4755 '/opt/${sanitizedProductName}/chrome-sandbox' || true
else
    chmod 0755 '/opt/${sanitizedProductName}/chrome-sandbox' || true
fi

if hash update-mime-database 2>/dev/null; then
    update-mime-database /usr/share/mime || true
fi

if hash update-desktop-database 2>/dev/null; then
    update-desktop-database /usr/share/applications || true
fi
