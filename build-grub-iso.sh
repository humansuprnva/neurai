#!/bin/sh
# NeurAI - build the dual BIOS+UEFI GRUB ISO (the method used for the shipped ISO).
# Host needs: grub-pc-bin grub-efi-amd64-bin xorriso (apt install on Ubuntu/Debian)
# Expects .build/initramfs.gz + kernel at .build/isoroot/boot/vmlinuz
# (both produced by: python3 build_iso.py  - its rootfs/initramfs stage)
set -e; cd "$(dirname "$0")"
GI=.build/grub-iso; mkdir -p $GI/boot/grub $GI/EFI/BOOT
cp .build/isoroot/boot/vmlinuz $GI/boot/vmlinuz
cp .build/initramfs.gz $GI/boot/initramfs.gz
cp iso/boot/grub/grub.cfg $GI/boot/grub/grub.cfg
cp README.md install-to-disk.sh $GI/ 2>/dev/null || true
MODS="biosdisk iso9660 part_gpt part_msdos normal linux search search_fs_file configfile echo test true"
grub-mkimage -O i386-pc -o .build/core.img -p /boot/grub $MODS
cat /usr/lib/grub/i386-pc/cdboot.img .build/core.img > $GI/boot/bios.img
grub-mkimage -O x86_64-efi -o $GI/EFI/BOOT/BOOTX64.EFI -p /boot/grub fat iso9660 part_gpt part_msdos normal linux search search_fs_file configfile echo test true
xorriso -as mkisofs -V NEURAI_OS -J -R -o dist/neurai-os-2.0.0-amd64.iso \
  -b boot/bios.img -no-emul-boot -boot-load-size 4 -boot-info-table \
  -eltorito-alt-boot -e EFI/BOOT/BOOTX64.EFI -no-emul-boot $GI
echo "DONE: dist/neurai-os-2.0.0-amd64.iso"
echo "Boot test: python3 tests/boot_test.py   (needs qemu-system-x86_64)"
