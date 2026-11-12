#!/bin/sh
# NeurAI installer - run from the booted live ISO/USB.
# Usage: sh install-to-disk.sh /dev/sdX   (TARGET DISK WILL BE ERASED - asks first)
set -e
DISK="${1:-}"
[ -n "$DISK" ] && [ -b "$DISK" ] || { echo "Usage: $0 /dev/sdX   (list disks: lsblk / fdisk -l)"; exit 1; }
echo "WARNING: $DISK will be ERASED and NeurAI installed (EFI, x86_64)."
printf "Type ERASE to continue: "; read A; [ "$A" = "ERASE" ] || { echo "Aborted."; exit 1; }
parted -s "$DISK" mklabel gpt mkpart ESP fat32 1MiB 261MiB set 1 esp on mkpart neurai ext4 261MiB 100%
mkfs.vfat -F32 "${DISK}1" 2>/dev/null || mkfs.vfat -F32 "${DISK}p1"
P2="${DISK}2"; [ -b "$P2" ] || P2="${DISK}p2"; P1="${DISK}1"; [ -b "$P1" ] || P1="${DISK}p1"
mkfs.ext4 -F "$P2"
mkdir -p /mnt/neurai; mount "$P2" /mnt/neurai; mkdir -p /mnt/neurai/boot/efi; mount "$P1" /mnt/neurai/boot/efi
# Copy the live system
cp -a /usr/local/lib/neurai /mnt/neurai/ 2>/dev/null || true
mkdir -p /mnt/neurai/var/lib/neurai
cp /boot/vmlinuz /mnt/neurai/boot/ 2>/dev/null || cp /vmlinuz /mnt/neurai/boot/ 2>/dev/null || echo "Copy your kernel to /mnt/neurai/boot/vmlinuz"
grub-install --target=x86_64-efi --efi-directory=/mnt/neurai/boot/efi --boot-directory=/mnt/neurai/boot --removable "$DISK" || \
grub-install --target=i386-pc --boot-directory=/mnt/neurai/boot "$DISK"
cp /boot/grub/grub.cfg /mnt/neurai/boot/grub/grub.cfg 2>/dev/null || true
sync; echo "Installed. Remove the USB and reboot into NeurAI."
