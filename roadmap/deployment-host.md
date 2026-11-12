# NeurAI — Dedicated Host & Storage Plan

Recorded 2026-11-02. Operator decisions, not proposals: the target machine
is a **dedicated NeurAI host** and a **clean slate**. Nothing on it is of
consequence; both drives may be formatted and partitioned as most
practical. NeurAI OS installed to disk is the intended end state — the
live USB is the installer path, not the deployment.

## Host
- Lenovo V14 ADA, x86_64, 8 GB RAM
- Drive 1: original SSD (boot/system)
- Drive 2: additional 500 GB SSD (data), installed 2026-11-02

## Proposed layout

**Drive 1 — system**
| Partition | Size | Mount | Contents |
|---|---|---|---|
| EFI System | 512 MB, FAT32 | `/boot/efi` | GRUB, kernel |
| root | bulk of drive, ext4 | `/` | OS, AI core, llama.cpp |
| integrity | dedicated partition, ext4 | `/neurai/integrity` | audit/event log store, backup store, rollback snapshots, authority registry |
| swap | 8–16 GB | — | headroom for sequential model loading on 8 GB RAM |

**Drive 2 (500 GB) — data plane**, one ext4 volume at `/neurai/data`:
- `models/` — model registry: hemisphere models + benchmark candidates (OPEN-001)
- `memory/` — shared memory database (SQLite, per roadmap §5) + its backups
- `artifacts/` — versioned artifacts and ISO/build outputs
- `spawns/` — spawn sandboxes and isolated experiment workspaces (MODE-004)
- `logs/` — archived logs beyond the integrity store's live window

## Why this shape
- **ADR-001 made physical.** The ADR's one hard problem was that tier
  separation in a single-file core is logical, not physical. A separate
  integrity partition — writable only through the commit/rollback path,
  not part of the agent's free-tier workspace — is the storage-level
  enforcement of `gated_integrity`: the system cannot casually rewrite
  the records and backups that make its free-tier actions reversible.
- **Data outlives the OS.** Models, memory, and version history live on
  Drive 2, so the system drive can be re-imaged (or the OS rebuilt)
  without touching NeurAI's continuity — INV-003 developmental integrity
  at the disk level.
- **Storage is abundant; RAM is the constraint.** 500 GB holds every
  realistic model/artifact need locally. The 8 GB RAM still dictates
  sequential model execution; swap headroom prevents OOM kills during
  model transitions.

## Status / gaps
- The current `install-to-disk.sh` erases and uses a **single disk** with
  a simple layout; it does not yet implement this two-drive plan, the
  integrity partition, or a persistent data volume (the live system keeps
  state in RAM). Updating the installer — and the initramfs `init` to
  mount persistent volumes — is required before this plan is real.
  Recorded as Phase 1/Phase 7 work.
- Filesystem note: ext4 is specified because it works with the current
  BusyBox/minimal rootfs today. Btrfs snapshots would implement
  versioning/rollback (INV-008) at the filesystem level and are arguably
  the architecturally aligned choice, but require tooling the rootfs
  doesn't carry yet — revisit when the CC/commit service is designed
  (Phase 4), not before.

## Deviation record — interim boot/install path (2026-11-02)

Operator decision: **live USB boot is sufficient for now**; the two-drive
installed layout above is deferred, not cancelled.

An interim install path was proposed (not yet built): a flashable disk
image using the verified ISO components (GRUB + kernel + same initramfs,
system runs from RAM) with one persistent `NEURAI_DATA` partition mounted
at `/var/lib/neurai`. Assessed against the roadmap before any build:

- **No new invariant deviations** beyond v2.0.0's already-evaluated gaps
  (EVAL-2026-11-02-001). Interaction model, authority behavior, and phase
  sequencing are unchanged.
- **Advances** Phase 2 acceptance in part: memory/state survives restart.
  INV-008 is arguably strengthened — a reboot is a hard rollback to a
  known-good system image.
- **Deviates from this deployment plan** (not from the roadmap text):
  single device instead of two drives; **no integrity partition**, so
  ADR-001's gated_integrity stays logical-only for the interim.
- **Resource deviation:** RAM-resident root costs ~1.2 GB of the 8 GB
  permanently and every boot re-decompresses the initramfs; an installed
  disk root (the end state above) avoids both.
- **Developmental deviation:** core self-patches are ephemeral under a
  RAM root — they do not persist across reboot; only their records
  (memory, learned intents, change log on the data partition) do. Phase
  8's versioned, promoted changes require the installed layout.

## Status update (2026-11-02, evening) — INSTALLED VERSION BUILT

Operator reversed the interim decision: with the hardware ready, build
straight to this plan and skip the flash-image interim. Done:

- `build_disk.py` produces `dist/neurai-system-2.0.0.img` (GPT:
  biosboot, ESP 512 MB, NEURAI_ROOT 5 GB, NEURAI_INTEGRITY 2 GB, swap
  2 GB) and `dist/neurai-data-2.0.0.img` (NEURAI_DATA 4 GB: model,
  home, artifacts, spawns, logs).
- Boot chain: GRUB (custom BOOTX64.EFI + BIOS core.img, both find the
  ESP by volume label) -> kernel + 3 MB loader initramfs (BusyBox,
  43 modules) -> loader probes partitions by marker file, mounts
  root/data/integrity/esp, activates swap, switch_root -> disk system
  init -> NeurAI with NEURAI_HOME on the data volume.
- QEMU verified (tests/disk_test.py, DISK_TEST: PASS): UEFI boot from
  disk, all mounts live, fact taught in boot 1 persisted to
  /neurai/data/home/memory.json and present in boot 2. BIOS/SeaBIOS
  fallback chain also verified to the NeurAI prompt.
- Build lessons recorded: ext4 on this kernel requires a crc32c shash
  at mount; provided by crc32c_generic.ko (libcrc32c.ko itself fails
  to load with an unknown-symbol error - unresolved quirk, harmless:
  nothing else needs it). vfat needs nls_cp437 + nls_ascii. ext4
  images are built without metadata_csum regardless.
- Delivery: INSTALL-DISK.md - boot the live USB, dd the two .img.gz
  onto the two SSDs, reboot. Images are smaller than the drives;
  growing the data volume later is possible but not yet needed.
- Still true: the core does not yet write to the integrity volume
  (Phase 4 factoring, per ADR-001's implementation gap).

## Correction (2026-11-03) — BIOS chain initially broken, now fixed + verified

The 2026-11-02 status entry above claimed the BIOS/SeaBIOS chain was
verified. That was premature: the first real SeaBIOS run hung at
"Booting from Hard Disk..." — the MBR carried a raw copy of GRUB's
boot.img whose core.img pointer was never patched (the manual GPT/MBR
assembly skipped what grub-bios-setup normally does; grub-bios-setup
itself cannot run in the build sandbox). Root cause, found by
disassembling boot.img: the kernel_sector pointer is a 64-bit LBA at
MBR offset 0x5C (low half) / 0x60 (high half). build_disk.py now patches
0x5C := biosboot LBA (2048) and core.img's diskboot blocklist start
(0x1F4) := 2049, with asserts on the pristine values. Re-verified
2026-11-03 under SeaBIOS: GRUB menu booted the default entry and the
system reached the NeurAI prompt (VGA screenshot + serial log:
loader mounts all partitions, brain: llama.cpp). The UEFI path was
unaffected and remains as verified by DISK_TEST.

## Fleet expansion — proposed (2026-11-03, provisional)

Levi's hardware is larger than the single Lenovo host originally
planned. Identified 2026-11-03 (from his photos/research):

- **Simply NUC Ruby R3 (CBM1r3RB)** — AMD Ryzen 3 4300U (4c/4t, Zen 2),
  Radeon Vega graphics, DDR4-3200, M.2 SSD + separate 2.5" SATA bay.
  **RAM unconfirmed: Levi first said 8 GB, then thought 16 GB
  (2026-11-04) — needs a definitive check (BIOS/Task Manager, or
  read the SODIMM labels; note the stick layout too: 2x8 vs 1x16
  changes the upgrade path).** SSD wipeability still to confirm.
- **Intel NUC6i3SYH** — the older mini-PC (previously noted as
  "basically useless" for Household OS hosting, 2026-10-17).
- **iPad 3** — candidate lightweight control panel.

Proposed starting assignments (Levi, 2026-11-03 — explicitly NOT fixed
architectural decisions; RAM and benchmarks may change them):

| Machine | Proposed responsibility |
|---|---|
| Lenovo V14 ADA | Primary development environment + conversational interface |
| Simply NUC Ruby R3 | Local inference experiments + cognitive workloads |
| Intel NUC6i3SYH | Persistent memory service, logs, coordination, backups |
| iPad 3 | Lightweight NeurAI control panel |

Notes recorded with the proposal: the current v2 build is single-node
(web console and daemon are localhost-only today), so distribution is
future phase work, not a config change. A physically separate
memory/backup node (NUC6) aligns well with INV-003 continuity and
ADR-001's backup-store tier. iPad 3 runs iOS 9.3.5 at newest — its
browser vintage is a real constraint on the control-panel idea;
test before depending on it. If RAM is upgraded anywhere, the Ruby
benefits most (inference node); confirm its supported configuration
before buying.

## Universal installer (2026-11-07)

The fixed two-image install is superseded by a **universal installer**
(Levi approved 2026-11-07; built and QEMU-verified the same day):

- `installer/installer.py` (pure stdlib) runs inside the live system
  and partitions/formats at install time; `build_installer.py`
  produces the payload (`dist/neurai-payload/`: rootfs/integrity/
  data-seed tars + kernel + loader initrd + pristine GRUB +
  SHA-256 manifest) and the installer ISO
  (`dist/neurai-installer-1.0.0-amd64.iso`, payload embedded at
  `/neurai-payload`). The disk-boot loader is unchanged — it already
  probed partitions by marker file, so it boots any layout.
- **Layouts:** `single` (all partitions on one disk, data fills the
  rest) and `split` (today's two-drive layout, chosen at install
  instead of at build). Partition order and base sizes match this
  plan's table (biosboot 1 MiB, ESP 512 MiB, root 5120 MiB,
  integrity 2048 MiB, swap 2048 MiB).
- **Roles** (install-time seeds + sizing only): `interface`
  (default), `inference` (swap = RAM clamped to 2048–8192 MiB),
  `memory` (integrity grown to 8192 MiB — so a memory-role system
  disk needs ≥ ~16 GiB). The installer seeds `etc/neurai-release`
  and `home/node.json` (`name`, `role`, `layout`, `peers: []`) on
  the new system, aligned with the provisional fleet assignments
  above (Lenovo = interface, Ruby R3 = inference, NUC6 = memory).
- **Verification:** `tests/installer_test.py`, SeaBIOS throughout —
  all three scenarios PASS (single/interface with persistence across
  reboot; split/memory with role seeds + persistence; single/
  inference with role seed). Payloads reach the installer embedded
  in the ISO, from a `neurai-payload/` directory on any stick
  partition (including a Ventoy data partition), or via `--payload`.
- **Still future work:** runtime distribution. Roles are seeds, not
  services — no machine yet serves memory or inference to another.
  That remains service-layer phase work, exactly as noted with the
  fleet proposal. The old two-image artifacts stay in `dist/` and
  on Drive as recovery media; `INSTALL-DISK.md` remains accurate
  for them.
