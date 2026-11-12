# Installing NeurAI with the universal installer

The universal installer replaces the two fixed disk images
(`INSTALL-DISK.md`) with **payloads (plain files) + an installer
program** that partitions and formats the real disks at install time.
One USB stick can therefore set up:

- a **single** computer on **one** drive,
- a **single** computer split across **two** drives, or
- **multiple devices**, one at a time — each machine gets a *role seed*
  at install time (see below).

The installed system's boot loader finds its partitions by marker
files, not by which disk they sit on, so it boots any layout the
installer writes. That loader is unchanged from the image-based
install.

## What you get

| Artifact | What it is |
|---|---|
| `neurai-installer-1.0.1-amd64.iso` (~2.6 GB) | Live installer system: the NeurAI live OS + `neurai-install` + the payload embedded at `/neurai-payload` on the ISO. Hybrid boot packaging (FAT ESP El Torito image + hybrid GPT) — boots from Ventoy, a written stick, or CD. **Supersedes 1.0.0, whose ISO did not boot on UEFI hardware — do not use it** (see *Honest limits*). |
| `neurai-payload/` (or `neurai-payload-1.0.0.tar`) | The payload by itself: `rootfs.tar`, `integrity.tar`, `data-seed.tar` (model + fresh NeurAI home), kernel, loader initrd, pristine GRUB images, `grub.cfg`, and `manifest.json` (SHA-256 + size for every file). Payload version is still 1.0.0; only the ISO packaging changed in 1.0.1. |

Build both with `python3 build_installer.py`. The v2.0.0 artifacts in
`dist/` are not touched.

## Layouts and roles

Partition order on a disk is always: BIOS-boot, ESP, root, integrity,
swap, then (single layout) data. Sectors are 512 B; the first
partition starts at LBA 2048.

| | single | split |
|---|---|---|
| System partitions | on the one disk | on the system disk (rest left unallocated) |
| Data volume | fills the rest of the same disk | fills the second disk |
| Minimum sizes | fixed sum + 4096 MiB | system disk ≥ fixed sum; data disk ≥ 4096 MiB |

| Partition | interface | inference | memory |
|---|---|---|---|
| biosboot | 1 MiB | 1 MiB | 1 MiB |
| ESP (FAT32) | 512 MiB | 512 MiB | 512 MiB |
| root (ext4) | 5120 MiB | 5120 MiB | 5120 MiB |
| integrity (ext4) | 2048 MiB | 2048 MiB | **8192 MiB** |
| swap | 2048 MiB | RAM size, clamped to 2048–8192 MiB | 2048 MiB |

Roles are **install-time seeds and partition sizing only**. The
installer writes the role into the new system's `etc/neurai-release`
and into `home/node.json` on the data volume
(`{"name": ..., "role": ..., "layout": ..., "peers": []}`). No
distributed services are installed — see *Honest limits*.

## Three ways the payload reaches the installer

1. **Embedded in the installer ISO.** Boot the ISO and just run the
   installer; it auto-discovers the payload on the CD device.
2. **A `neurai-payload/` directory on any stick partition** — ext4,
   FAT/vFAT, exFAT, or UDF. On a **Ventoy stick**, copy the payload
   directory onto the stick's data partition next to the ISO file:
   boot the ISO from Ventoy's menu, and the installer finds the
   payload on the stick (auto-scan mounts each partition read-only
   and looks for `neurai-payload/manifest.json`). This avoids
   rebuilding the ISO when only the payload changes.
3. **`--payload DIR`** — point the installer at a payload directory
   explicitly (fixed paths `/neurai-payload`,
   `/mnt/usb/neurai-payload`, and `/mnt/payload` are also checked
   before auto-scan). Because payloads are plain files, fetching them
   from a URL later is a natural extension of this design; it is not
   implemented yet.

Before anything is written, every payload file is verified against
`manifest.json` (size + SHA-256).

## Physical steps (Lenovo)

1. **BIOS setup (F2 at power-on):** disable **Secure Boot** (the GRUB
   the installer writes is unsigned). Save and exit.
2. Put `neurai-installer-1.0.1-amd64.iso` on your **Ventoy stick** (or
   write it to a plain stick with Rufus/Balena Etcher/`dd`). Boot it
   from the **F12** boot menu — UEFI or BIOS firmware, either medium.
   One exception: a legacy-**BIOS** machine booting a **written** stick
   does not get past GRUB stage 1 (see *Honest limits*); on such a
   machine use Ventoy or a CD instead. NeurAI greets you as usual —
   the whole installer runs inside that live system. Note for UEFI
   boots: GRUB may print `error: no suitable video mode found /
   Booting in blind mode` and the screen can then sit unchanged for a
   few minutes while the system finishes booting — that is the kernel
   starting without a GRUB-provided framebuffer, not a hang.
3. At the NeurAI prompt, run the installer with the `!` prefix:
   - Interactive (recommended first time): `!neurai-install`
     It lists the disks, asks for layout/disks/role, prints the full
     plan, and requires you to **type each target disk's path exactly**
     (`/dev/sdX`) before it erases anything.
   - Scripted: `!neurai-install --auto --layout split --system-disk /dev/sda --data-disk /dev/sdb --role memory --yes`
     (single-drive: `--layout single --system-disk /dev/sda`).
     Note: the NeurAI shell kills `!` commands after 20 s, so `--auto`
     installs **detach**: the prompt returns immediately and the
     install streams its progress to the console and to
     `/tmp/neurai-install.log`. Wait for the success line
     (`INSTALL COMPLETE`) before powering off. From a plain shell
     (rescue boot), add `--foreground` to keep it in the foreground.
   - `!neurai-install --list` just inventories the disks;
     `--dry-run` (with the layout flags) verifies the payload and the
     plan and writes nothing.
4. When the install reports success, power off, **remove the stick**,
   and boot the machine normally. GRUB → loader → NeurAI, with its
   brain and persistent home on the data volume.

## Safety design

- **Nothing is written before payload verification** (SHA-256 against
  the manifest).
- The installer **refuses any disk that is currently mounted** (checked
  against `/proc/mounts`, partitions included) and any disk below the
  layout minimum, and it refuses to erase the disk the payload itself
  was found on.
- It writes **only to disks named explicitly** — typed exactly in
  interactive mode, given by flags in `--auto` mode (which also
  requires `--yes`). The plan printout says `ERASES /dev/sdX (size,
  model)` for each target.
- GRUB's BIOS embedding is patched only after asserting the pristine
  values (the same asserts `build_disk.py` uses); a non-pristine
  `core.img`/`boot.img` aborts the install.
- After writing, before declaring success, the installer re-reads each
  disk's GPT header and re-mounts every new filesystem read-only to
  check its marker files (`etc/neurai-release`, `.neurai-data`,
  `.neurai-integrity`, `EFI/BOOT/BOOTX64.EFI`). Only then does it
  print `INSTALL COMPLETE`.

## Verification status

Verified end-to-end in QEMU (SeaBIOS throughout) by
`tests/installer_test.py` on 2026-11-07 — **all three scenarios PASS**:

- (a) single 20 GiB, role interface: install from the serial ISO with
  the payload auto-discovered on a separate disk → installed boot →
  taught fact → power off → reboot → fact still in
  `/neurai/data/home/memory.json`.
- (b) split 20 GiB + 8 GiB, role memory: same cycle, plus
  `node.json` and `etc/neurai-release` both confirm `role=memory`,
  and persistence across reboot. (The memory role's fixed partitions
  sum to 15,873 MiB, so its system disk cannot be smaller than
  ~16 GiB; the test uses 20 GiB.)
- (c) single 20 GiB, role inference: install → boot → `node.json`
  confirms `role=inference`; the swap partition was sized from guest
  RAM (3,915 MiB), per the clamping rule.

The 1.0.1 ISO was additionally boot-tested as a **final file** (no
test variant) across the full firmware × medium matrix in QEMU on
2026-11-11, after the 1.0.0 ISO failed on physical UEFI hardware:

- **BIOS + CD:** PASS — boots to the NeurAI banner and live prompt.
- **UEFI (OVMF) + CD:** PASS — GRUB loads the kernel from the ESP
  boot image; a serial-console twin of the same ISO confirms the OS
  reaches its prompt (`brain: llama.cpp`). On VGA the screen stays at
  GRUB's "Booting in blind mode" message while the OS comes up (see
  step 2's note).
- **UEFI (OVMF) + written stick** (ISO copied byte-for-byte to a
  disk): PASS — same trajectory via the hybrid GPT's ESP partition.
- **BIOS + written stick:** FAIL — GRUB stage 1 loads from the MBR
  and stops; the core does not proceed. Two MBR constructions were
  attempted (syslinux isohdpfx: `isolinux.bin missing or corrupt.`;
  GRUB boot_hybrid: hangs after printing `GRUB`). Legacy-BIOS
  machines should boot the ISO via Ventoy or CD, both of which work.
- **Install smoke test** with the 1.0.1 ISO (scenario (a) above,
  rerun 2026-11-11): PASS, including persistence across reboot —
  the repackaging did not disturb the install flow.

## Honest limits

- **QEMU only, so far.** The install path is verified in emulation,
  not yet on the Lenovo. The first physical run should be treated as
  a test: keep the old images/ISO around as recovery media.
- **Distribution is still future phase work.** Roles are seeds —
  node name, role, layout, an empty peer list — plus partition
  sizing. Machines do not yet cooperate at runtime (no shared memory
  service, no remote inference); that is service-layer work from the
  roadmap's later phases, and no boot process or installer can
  substitute for it.
- **Why 1.0.1 exists (the 1.0.0 defect, on the record).** The 1.0.0
  ISO's UEFI El Torito entry used the raw `BOOTX64.EFI` binary as the
  boot image; UEFI firmware expects a FAT filesystem image there and
  fell into the UEFI shell, and the ISO had no hybrid MBR/GPT, so a
  written stick showed no bootable partition. It failed on every
  physical device tried, while every QEMU test had passed — because
  those tests were BIOS-only. The 1.0.1 packaging fixes both: the
  El Torito EFI image is a real 272 MiB FAT ESP built with
  `build_disk.py`'s Fat32 writer (smaller ESP images probe-failed:
  below ~264 MiB the writer's geometry yields <65,525 clusters and
  UEFI FAT drivers reject the volume), and xorriso adds a protective
  MBR plus a GPT carrying that ESP (`-isohybrid-gpt-basdat`,
  `-isohybrid-mbr` with GRUB's boot_hybrid template). The v2.0.0
  live ISO from `build-grub-iso.sh` has the same two defects as
  1.0.0 and is superseded by this installer ISO as boot media.
- **Ventoy itself is not QEMU-verified** — it cannot be simulated
  here. What is verified is every structure Ventoy consumes: the
  ISO9660 tree (with `/EFI/BOOT/BOOTX64.EFI` present as a file), the
  El Torito catalog (BIOS + UEFI entries), and the hybrid GPT. The
  first Ventoy boot of 1.0.1 on real hardware is still a first.
- The installer ISO is built with a GRUB + xorriso chain (in
  `build_installer.py`, derived from `build-grub-iso.sh`). The
  original plan — a pycdlib isolinux ISO modeled on `build_iso.py`
  — was abandoned after the stock `build_iso.py` output was shown
  to fail in this environment the same way (`Failed to load
  ldlinux.c32`).
- ext4 filesystems are created with the real e2fsprogs `mke2fs`
  (shipped in the installer ISO); the installer resolves it by
  absolute path because the BusyBox applet of the same name cannot
  create them.
