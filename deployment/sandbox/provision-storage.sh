#!/bin/sh
# Operator-only persistent bounded filesystem for foundation managed storage.
# Run BEFORE creating projects. Never expose this script through a member tool.
set -eu
if [ "$(id -u)" != 0 ]; then echo 'Run as host root (operator provisioning only)' >&2; exit 1; fi
if [ "$#" != 3 ]; then echo 'usage: provision-storage.sh /absolute/managed-storage /absolute/storage.img APP_UID' >&2; exit 1; fi
root=$1
image=$2
uid=$3
case "$root:$image" in /*:/*) ;; *) echo 'absolute paths required' >&2; exit 1;; esac
case "$uid" in ''|*[!0-9]*) echo 'numeric nonroot APP_UID required' >&2; exit 1;; esac
[ "$uid" -gt 0 ] || exit 1
[ "$root" != / ] && [ ! -L "$root" ] && [ ! -L "$image" ] || exit 1
case "$image" in "$root"/*) echo 'image must not be inside managed storage' >&2; exit 1;; esac
if mountpoint -q "$root"; then echo 'Already mounted; verify mount/capacity explicitly' >&2; exit 1; fi
mkdir -p "$root"
if [ -n "$(find "$root" -mindepth 1 -maxdepth 1 -print -quit)" ]; then
  echo 'Refusing to hide existing project files. Migrate them offline first.' >&2; exit 1
fi
if [ -e "$image" ]; then echo 'Refusing to overwrite existing filesystem image' >&2; exit 1; fi
# Allocation is bounded independently of all chat containers and survives
# environment recreation. Backing disk must have at least this space available.
(umask 077; fallocate -l 2048M "$image")
mkfs.ext4 -q -F -m 0 "$image"
mount -o loop,nosuid,nodev "$image" "$root"
chown "$uid:$uid" "$root"
chmod 700 "$root"
# Bounded-volume marker consumed by app/runtime/storage.py
# managed_storage_status() and the startup capability check. Removing it
# makes the deployment report unbounded (fail-closed admission is unchanged).
(umask 077; printf 'capacity_mb=2048\nimage=%s\nprovisioned=%s\n' "$image" "$(date -u +%FT%TZ)" > "$root/.tomo-bounded")
chown "$uid:$uid" "$root/.tomo-bounded"
chmod 600 "$root/.tomo-bounded"
printf 'Provisioned 2GiB filesystem. Add a loop,nosuid,nodev fstab entry for %s at %s.\n' "$image" "$root"
