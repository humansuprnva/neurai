#!/bin/sh
# NeurAI Integrated OS - build a bootable x86_64 ISO (BIOS + UEFI)
# Run on Ubuntu/Debian:  ./build-iso.sh
# Produces: dist/neurai-os-1.0.0-amd64.iso  (live, bootable from USB)
set -e
cd "$(dirname "$0")"
ARCH=amd64; OUT="dist/neurai-os-1.0.0-${ARCH}.iso"; WORK=".build"
say(){ echo "[build] $*"; }

# 1. Dependencies (only what's missing)
need=""
for t in grub-mkrescue xorriso mksquashfs; do command -v $t >/dev/null || need="$need $t"; done
if [ -n "$need" ]; then
  say "Installing build tools: grub2-common grub-pc-bin grub-efi-amd64-bin xorriso squashfs-tools busybox-static cpio"
  sudo apt-get update -qq && sudo apt-get install -y grub2-common grub-pc-bin grub-efi-amd64-bin xorriso squashfs-tools busybox-static cpio mtools
fi
command -v busybox >/dev/null || { echo "busybox not found"; exit 1; }

# 2. Kernel: host /boot first, else the distro package
KERNEL=""
for k in /boot/vmlinuz /boot/vmlinuz-* ; do [ -f "$k" ] && KERNEL="$k" && break; done
if [ -z "$KERNEL" ]; then
  say "No /boot/vmlinuz - fetching linux-image package"
  sudo apt-get install -y linux-image-generic
  KERNEL=$(ls /boot/vmlinuz-* | head -1)
fi
say "Kernel: $KERNEL"

# 3. Rootfs (initramfs) - BusyBox + Python3 runtime + NeurAI core
rm -rf "$WORK"; mkdir -p "$WORK/rootfs"/{bin,sbin,proc,sys,dev,etc,root,tmp,var/lib/neurai,var/log,usr/local/lib/neurai,usr/bin,lib,lib64}
cp "$(command -v busybox)" "$WORK/rootfs/bin/busybox"; chmod +x "$WORK/rootfs/bin/busybox"
# Python3: copy the interpreter + stdlib (keeps the image self-contained & local)
PYBIN=$(readlink -f "$(command -v python3)"); cp "$PYBIN" "$WORK/rootfs/usr/bin/python3"
PYLIB=$(python3 -c "import sysconfig;print(sysconfig.get_paths()['stdlib'])")
mkdir -p "$WORK/rootfs/usr/lib"; cp -a "$PYLIB" "$WORK/rootfs/usr/lib/" 2>/dev/null || true
for lib in libpython3*.so* libc.so* libm.so* libz.so* libbz2* liblzma* libffi* libssl* libcrypto* libsqlite3* libreadline* libncurses*; do
  find /usr/lib /lib -name "$lib" 2>/dev/null | head -1 | while read f; do cp -a "$f" "$WORK/rootfs/usr/lib/" 2>/dev/null || true; cp -a "$f" "$WORK/rootfs/lib/" 2>/dev/null || true; done
done
cp /lib64/ld-linux-x86-64.so.2 "$WORK/rootfs/lib64/" 2>/dev/null || true
# NeurAI itself
cp neurai/neurai.py neurai/ui.html "$WORK/rootfs/usr/local/lib/neurai/"
cp rootfs/init "$WORK/rootfs/init"; chmod +x "$WORK/rootfs/init"
echo "neurai-os" > "$WORK/rootfs/etc/hostname"
printf "root::0:0:root:/root:/bin/sh\n" > "$WORK/rootfs/etc/passwd"
# Pack initramfs (newc cpio, gzip -9 for size)
( cd "$WORK/rootfs" && find . | cpio -o -H newc 2>/dev/null | gzip -9 ) > "$WORK/initramfs.gz"
say "initramfs: $(du -h "$WORK/initramfs.gz" | cut -f1)"

# 4. ISO tree + GRUB (BIOS + EFI via grub-mkrescue)
mkdir -p "$WORK/iso/boot/grub"
cp "$KERNEL" "$WORK/iso/boot/vmlinuz"; cp "$WORK/initramfs.gz" "$WORK/iso/boot/initramfs.gz"
cp iso/boot/grub/grub.cfg "$WORK/iso/boot/grub/grub.cfg"
cp install-to-disk.sh "$WORK/iso/install-to-disk.sh" 2>/dev/null || true
mkdir -p dist
grub-mkrescue -o "$OUT" "$WORK/iso" 2>&1 | tail -2
say "DONE: $OUT ($(du -h "$OUT" | cut -f1))"
say "Write to USB:  sudo dd if=$OUT of=/dev/sdX bs=4M status=progress conv=fsync   (or use Rufus/Balena Etcher on Windows)"
say "Test in QEMU:  qemu-system-x86_64 -m 1024 -cdrom $OUT -boot d"
