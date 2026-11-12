#!/usr/bin/env python3
"""
NeurAI universal-installer builder.

Produces, without touching any existing dist artifact:
  dist/neurai-payload/                 payload directory (files, not images)
    rootfs.tar  integrity.tar  data-seed.tar  vmlinuz  initrd.gz
    grub/{BOOTX64.EFI, core.img, boot.img, grub.cfg}  manifest.json
  dist/neurai-payload-1.0.0.tar        the payload dir as one tar
  dist/neurai-installer-1.0.1-amd64.iso
      live ISO (build_iso rootfs + installer + mke2fs + kernel modules
      for storage/filesystems) with the payload embedded at
      /neurai-payload on the ISO filesystem. Hybrid boot packaging:
      BIOS El Torito (boot/bios.img), UEFI El Torito whose boot image
      is a real FAT ESP (boot/esp.img, build_disk.Fat32), plus a
      protective/hybrid MBR and a GPT carrying the ESP, so the file
      boots from Ventoy, from a dd/Etcher/Rufus-dd written stick, or
      from CD, under BIOS or UEFI.
  .build/neurai-installer-1.0.1-amd64-serial.iso
      same ISO with console=ttyS0 appended (QEMU test variant; also
      written to dist/ instead when env NEURAI_TEST_SERIAL=1).

The payload version stays 1.0.0; only the ISO packaging is 1.0.1.
ISO 1.0.0 was defective on real hardware: its UEFI El Torito entry
used the raw BOOTX64.EFI binary as the boot image (UEFI firmware
expects a FAT filesystem image - OVMF fell into the UEFI shell with
BLK mappings only), and it had no hybrid MBR/GPT, so a written USB
stick presented no bootable partition. Its BIOS entry was fine,
which is why the SeaBIOS-only QEMU tests passed. 1.0.1 supersedes
it; the 1.0.0 file is kept untouched for the record.

GPT/FAT32/module machinery is reused from build_disk.py by import;
the live rootfs comes from build_iso.py, and the ISO is written with
xorriso using GRUB boot images built below (chain derived from
build-grub-iso.sh, extended with the ESP image and hybrid options;
see build_installer_iso).
"""
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

import build_disk as bd
import build_iso

B = ROOT / ".build"
DIST = ROOT / "dist"
PAYLOAD = DIST / "neurai-payload"
INSTALLER_VERSION = "1.0.0"   # payload version (payload dir, tar, manifest)
ISO_VERSION = "1.0.1"         # ISO packaging version (boot fix; see docstring)
OS_VERSION = "2.0.0"
# ESP boot image size. The Fat32 writer's geometry (8-sector clusters)
# yields <65525 data clusters below ~264 MiB, and UEFI FAT drivers
# (OVMF's included) then refuse the volume despite its FAT32 BPB:
# a 32 MiB ESP fell into the UEFI shell in probing (.build/probe101),
# while 272 MiB (69492 clusters) reached the GRUB menu. Keep >= 272.
ESP_MIB = 272
WORK = B / "installer"
ISO_STAGE = B / "installer-isoroot"


# ---------------------------------------------------------------- modules
def module_closure_extra(extra_roots):
    """build_disk.module_closure with additional root modules.

    Same algorithm (modinfo depends= walk over the Debian kernel tree),
    reusing bd._modinfo_depends / bd._norm; build_disk.py is unmodified.
    Extra roots: filesystem drivers the installer needs to READ payloads
    (isofs/exfat/udf) and to see USB payload sticks (usb_storage/uas).
    """
    kdir = next(p for p in bd.TOOLS.rglob("modules.builtin")).parent
    by_name = {}
    for p in kdir.rglob("*.ko"):
        by_name.setdefault(bd._norm(p.name), p)
    deps = {}

    def get_deps(norm):
        if norm not in deps:
            deps[norm] = [bd._norm(d) for d in bd._modinfo_depends(by_name[norm])] if norm in by_name else []
        return deps[norm]

    roots = ["ext4", "vfat", "nvme", "ahci", "ata_piix", "sd_mod", "sr_mod",
             "virtio_blk", "virtio_pci", "xhci_hcd", "xhci_pci", "ehci_hcd",
             "usbhid", "hid_generic",
             "crc32c_intel", "libcrc32c", "crc32c_generic", "crc32c",
             "nls_cp437", "nls_iso8859_1", "nls_utf8", "nls_ascii"] + list(extra_roots)
    order, seen = [], set()

    def visit(name):
        n = bd._norm(name)
        if n in seen or n not in by_name:
            return
        seen.add(n)
        for d in get_deps(n):
            visit(d)
        order.append(by_name[n].name)

    for r in roots:
        visit(r)
    missing = [r for r in roots if bd._norm(r) not in seen]
    if missing:
        print("  modules not present (built-in or absent, ok):", missing)
    return order, {bd._norm(p.name): p for p in by_name.values()}


# ---------------------------------------------------------------- payload
def make_tar(stage: Path, out: Path):
    with tarfile.open(out, "w", format=tarfile.GNU_FORMAT) as tf:
        tf.add(stage, arcname=".", recursive=True)
    print(f"  tar: {out.name} {out.stat().st_size // 1048576} MiB (from {stage})")
    return out


def sha256_of(p: Path):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while True:
            chunk = f.read(8 * 1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def build_payload():
    print("== staging (reusing build_disk) ==")
    bd.build_sysroot()
    bd.build_aux_stages()
    initrd = bd.build_loader_initrd()
    bios_core, efi_bin, _boot_code = bd.grub_images()
    if PAYLOAD.exists():
        shutil.rmtree(PAYLOAD)
    (PAYLOAD / "grub").mkdir(parents=True)
    make_tar(bd.SYS_STAGE, PAYLOAD / "rootfs.tar")
    make_tar(bd.INT_STAGE, PAYLOAD / "integrity.tar")
    make_tar(bd.DATA_STAGE, PAYLOAD / "data-seed.tar")
    kernel = build_iso.find(["vmlinuz-*"], bd.TOOLS)
    shutil.copy2(kernel, PAYLOAD / "vmlinuz")
    shutil.copy2(initrd, PAYLOAD / "initrd.gz")
    (PAYLOAD / "grub" / "BOOTX64.EFI").write_bytes(efi_bin)
    # PRISTINE (unpatched) BIOS core: the installer patches it at
    # install time for the actual biosboot LBA, like build_disk.main.
    (PAYLOAD / "grub" / "core.img").write_bytes(bios_core)
    boot_img = bd.B / "grub-root" / "usr/lib/grub/i386-pc/boot.img"
    shutil.copy2(boot_img, PAYLOAD / "grub" / "boot.img")
    shutil.copy2(ROOT / "iso/boot/grub/grub-disk.cfg", PAYLOAD / "grub" / "grub.cfg")
    files = {}
    for p in sorted(PAYLOAD.rglob("*")):
        if p.is_file():
            rel = str(p.relative_to(PAYLOAD))
            files[rel] = {"sha256": sha256_of(p), "size": p.stat().st_size}
    manifest = {"os_version": OS_VERSION, "installer_version": INSTALLER_VERSION,
                "files": files}
    (PAYLOAD / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"  payload: {len(files)} files, "
          f"{sum(f['size'] for f in files.values()) // 1048576} MiB total")
    # payload as a single tar (top-level neurai-payload/ directory)
    tar_out = DIST / f"neurai-payload-{INSTALLER_VERSION}.tar"
    if tar_out.exists():
        tar_out.unlink()
    with tarfile.open(tar_out, "w", format=tarfile.GNU_FORMAT) as tf:
        tf.add(PAYLOAD, arcname="neurai-payload", recursive=True)
    print(f"  payload tar: {tar_out} {tar_out.stat().st_size // 1048576} MiB")
    return PAYLOAD


# ---------------------------------------------------------------- installer ISO rootfs
def build_installer_rootfs():
    print("== installer rootfs (build_iso.build_rootfs + installer) ==")
    build_iso.build_rootfs()
    RFS = build_iso.RFS
    inst_dir = RFS / "usr/local/lib/neurai"
    shutil.copy2(ROOT / "installer/installer.py", inst_dir / "installer.py")
    # The installer imports build_disk (GPT/Fat32 writers) at runtime;
    # ship build_disk.py + build_iso.py beside it as libraries.
    shutil.copy2(ROOT / "build_disk.py", inst_dir / "build_disk.py")
    shutil.copy2(ROOT / "build_iso.py", inst_dir / "build_iso.py")
    wrapper = RFS / "usr/local/bin/neurai-install"
    wrapper.parent.mkdir(parents=True, exist_ok=True)
    wrapper.write_text("#!/bin/sh\nexec python3 /usr/local/lib/neurai/installer.py \"$@\"\n")
    os.chmod(wrapper, 0o755)
    print("  installer: installer.py + build_disk/build_iso libs + neurai-install wrapper")
    # ext4 formatter for the installer (host binary + its shared libs);
    # copy_with_libs does not create the binary's own parent directory
    (RFS / "usr/sbin").mkdir(parents=True, exist_ok=True)
    build_iso.copy_with_libs("/usr/sbin/mke2fs", RFS)
    print("  mke2fs copied with libs")
    # kernel modules: storage + filesystems so the installer can see
    # target disks and read payloads from ext4/vfat/exfat/iso9660/udf
    order, by_name = module_closure_extra(["isofs", "exfat", "udf", "usb_storage", "uas"])
    moddir = RFS / "lib/modules"
    moddir.mkdir(parents=True, exist_ok=True)
    for fname in order:
        shutil.copy2(by_name[bd._norm(fname)], moddir / fname)
    (moddir / "order.txt").write_text("\n".join(order) + "\n")
    print(f"  modules: {len(order)} flat in lib/modules + order.txt")


def _linktree(src: Path, dst: Path):
    """Recreate src's tree at dst using hardlinks (same filesystem)."""
    for p in sorted(src.rglob("*")):
        rel = p.relative_to(src)
        d = dst / rel
        if p.is_dir():
            d.mkdir(parents=True, exist_ok=True)
        elif p.is_file():
            d.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.link(p, d)
            except OSError:
                shutil.copy2(p, d)


def _ensure_grub_lib():
    """grub-mkimage (from .build/shims) reads modules from /usr/lib/grub;
    point it at the extracted grub-root, as build_disk.grub_images does."""
    link = Path("/usr/lib/grub")
    if not link.exists():
        try:
            link.symlink_to(bd.B / "grub-root" / "usr/lib/grub")
        except Exception:
            pass


def build_installer_iso(out_path: Path, serial: bool, initramfs: Path):
    """Bootable hybrid ISO, payload embedded at /neurai-payload.

    Boot packaging (the 1.0.1 fix; 1.0.0's UEFI entry pointed El
    Torito straight at the raw BOOTX64.EFI binary, which UEFI
    firmware cannot use, and the ISO had no hybrid structure at all):

    - BIOS El Torito: boot/bios.img (cdboot.img + i386-pc GRUB core),
      unchanged from 1.0.0 - this path always worked under SeaBIOS.
    - UEFI El Torito: boot/esp.img, a real FAT filesystem image built
      with build_disk.Fat32 (the installed-disk ESP construction),
      containing EFI/BOOT/BOOTX64.EFI. The EFI GRUB carries an
      embedded early config that locates /boot/grub/grub.cfg on the
      ISO9660 volume (search --file) and configfiles it, so kernel
      and initramfs are read from the ISO tree exactly as in the
      BIOS path - the ESP holds only the bootloader. ESP size is
      ESP_MIB: smaller images have <65525 FAT clusters and UEFI FAT
      drivers reject them (probed: 32 MiB -> UEFI shell, 272 MiB ->
      GRUB menu; .build/probe101).
    - Hybrid: xorriso --protective-msdos-label + -isohybrid-gpt-basdat
      put a GPT on the image with the ESP registered as a partition,
      and -isohybrid-mbr (GRUB's own boot_hybrid.img template - the
      syslinux isohdpfx template was tried first and fails here: it
      chain-loads expecting isolinux and prints 'isolinux.bin missing
      or corrupt.' on a GRUB/GPT-basdat layout) writes MBR boot code,
      so a stick written with dd/Etcher/Rufus-dd boots too.

    Earlier deviation (kept): the ISO is written with this GRUB +
    xorriso chain, not pycdlib isolinux (build_iso.build_iso) - the
    stock pycdlib output does not boot in this sandbox ('Failed to
    load ldlinux.c32', verified 2026-11-07 with a minimal ISO)."""
    _ensure_grub_lib()
    GI = B / "installer-grub-iso"
    if GI.exists():
        shutil.rmtree(GI)
    (GI / "boot/grub").mkdir(parents=True)
    (GI / "EFI/BOOT").mkdir(parents=True)
    kernel = build_iso.find(["vmlinuz-*"], bd.TOOLS)
    shutil.copy2(kernel, GI / "boot/vmlinuz")
    shutil.copy2(initramfs, GI / "boot/initramfs.gz")
    cfg = (ROOT / "iso/boot/grub/grub.cfg").read_text()
    cfg = cfg.replace(" console=ttyS0", "")
    if serial:
        cfg = cfg.replace("console=tty0", "console=tty0 console=ttyS0")
    (GI / "boot/grub/grub.cfg").write_text(cfg)
    shutil.copy2(ROOT / "README.md", GI / "README.md")
    _linktree(PAYLOAD, GI / "neurai-payload")
    env = dict(os.environ)
    env["PATH"] = str(B / "shims") + ":" + env["PATH"]
    mods_bios = "biosdisk iso9660 part_gpt part_msdos normal linux search search_fs_file configfile echo test true"
    core = B / "installer-core.img"
    subprocess.run(["grub-mkimage", "-O", "i386-pc", "-o", str(core), "-p", "/boot/grub"]
                   + mods_bios.split(), check=True, env=env, capture_output=True)
    cdboot = Path("/usr/lib/grub/i386-pc/cdboot.img")
    if not cdboot.exists():
        cdboot = bd.B / "grub-root/usr/lib/grub/i386-pc/cdboot.img"
    (GI / "boot/bios.img").write_bytes(cdboot.read_bytes() + core.read_bytes())
    # UEFI bootloader: embedded early config finds the ISO's grub.cfg
    # (the same search pattern build_disk uses for the disk ESP, but
    # by file path instead of volume label, since the ISO is the root).
    early = WORK / "grub-early-iso.cfg"
    WORK.mkdir(parents=True, exist_ok=True)
    early.write_text("search --no-floppy --file /boot/grub/grub.cfg --set=root\n"
                     "set prefix=($root)/boot/grub\n"
                     "configfile ($root)/boot/grub/grub.cfg\n")
    mods_efi = "fat iso9660 part_gpt part_msdos normal linux search search_fs_file search_label configfile echo test true"
    subprocess.run(["grub-mkimage", "-O", "x86_64-efi", "-o", str(GI / "EFI/BOOT/BOOTX64.EFI"),
                    "-c", str(early), "-p", "/boot/grub"] + mods_efi.split(),
                   check=True, env=env, capture_output=True)
    # The El Torito EFI boot image: a FAT ESP holding that bootloader,
    # built with the same Fat32 writer as the installed-disk ESP.
    esp = bd.Fat32(ESP_MIB * bd.MIB // bd.SECTOR, hidden=0)
    esp_img, _ = esp.build({"EFI/BOOT/BOOTX64.EFI": (GI / "EFI/BOOT/BOOTX64.EFI").read_bytes()})
    (GI / "boot/esp.img").write_bytes(esp_img)
    print(f"  esp.img: {ESP_MIB} MiB FAT ({esp.nclusters} clusters)")
    hybrid_mbr = bd.B / "grub-root/usr/lib/grub/i386-pc/boot_hybrid.img"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        out_path.unlink()
    subprocess.run(["xorriso", "-as", "mkisofs", "-V", "NEURAI_OS", "-J", "-R",
                    "-o", str(out_path),
                    "-b", "boot/bios.img", "-no-emul-boot", "-boot-load-size", "4",
                    "-boot-info-table", "-eltorito-alt-boot",
                    "-e", "boot/esp.img", "-no-emul-boot",
                    "--protective-msdos-label", "-isohybrid-gpt-basdat",
                    "-isohybrid-mbr", str(hybrid_mbr), str(GI)],
                   check=True, env=env, capture_output=True, text=True)
    print(f"ISO DONE: {out_path} {out_path.stat().st_size // 1048576} MiB (serial={serial})")
    return out_path


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    tar_out = DIST / f"neurai-payload-{INSTALLER_VERSION}.tar"
    if (PAYLOAD / "manifest.json").exists() and tar_out.exists():
        # Payload is versioned content, independent of ISO packaging:
        # reuse the shipped 1.0.0 payload byte-for-byte rather than
        # restaging it (tar bytes embed staging mtimes).
        manifest = json.loads((PAYLOAD / "manifest.json").read_text())
        assert manifest["installer_version"] == INSTALLER_VERSION, manifest["installer_version"]
        print(f"== payload: reusing existing {PAYLOAD} "
              f"({len(manifest['files'])} files, manifest v{INSTALLER_VERSION}) ==")
    else:
        build_payload()
    build_installer_rootfs()
    initramfs = build_iso.build_initramfs()
    dist_iso = DIST / f"neurai-installer-{ISO_VERSION}-amd64.iso"
    serial_env = os.environ.get("NEURAI_TEST_SERIAL") == "1"
    build_installer_iso(dist_iso, serial=serial_env, initramfs=initramfs)
    if not serial_env:
        # QEMU tests always get a serial-console variant alongside.
        build_installer_iso(B / f"neurai-installer-{ISO_VERSION}-amd64-serial.iso",
                            serial=True, initramfs=initramfs)
    print("DONE")


if __name__ == "__main__":
    main()
