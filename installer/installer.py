#!/usr/bin/env python3
"""
NeurAI universal installer (installer_version 1.0.0, for OS 2.0.0).

Runs inside the live NeurAI system (BusyBox + Python 3.12, stdlib only).
Replaces the fixed two-image install: payloads are plain FILES
(neurai-payload/: rootfs.tar, integrity.tar, data-seed.tar, kernel,
loader initrd, pristine GRUB images, grub.cfg, manifest.json) and this
program partitions/formats the target disks at install time, so one
installer serves a single-drive machine, a split two-drive machine,
or — one machine at a time, with a role seed — each member of a fleet.

Layouts: single (all partitions on one disk, data fills the rest),
         split  (system partitions on one disk, data volume on another).
Roles (install-time seeds + partition sizing only; no distributed
services are installed): interface (default), inference, memory.

GPT/FAT32 construction is reused from build_disk.py (imported; the
installer ISO ships build_disk.py + build_iso.py beside this file in
/usr/local/lib/neurai/). The installed system's boot loader
(rootfs/init-disk-loader) is layout-agnostic and unchanged.

Invocation notes: the NeurAI shell's `!` escape kills commands after
20 s, so `--auto` installs detach by default: the parent returns
immediately and the worker streams progress to /dev/console and
/tmp/neurai-install.log. Pass --foreground (or run from a plain
shell) to keep the install in the foreground.
"""
import argparse
import datetime
import hashlib
import json
import os
import struct
import subprocess
import sys
import tarfile
import time
from pathlib import Path

INSTALLER_VERSION = "1.0.0"
OS_VERSION = "2.0.0"
SECTOR = 512
MIB = 1024 * 1024
BLKRRPART = 0x125F
FIRST_LBA = 2048
LOG_PATH = "/tmp/neurai-install.log"

MOUNT_ROOT = Path("/mnt/inst-root")
MOUNT_DATA = Path("/mnt/inst-data")
MOUNT_INT = Path("/mnt/inst-integrity")
MOUNT_PROBE = Path("/mnt/probe")
MOUNT_VERIFY = Path("/mnt/verify")


class InstallerError(Exception):
    pass


# ------------------------------------------------------------------ output
def log(msg):
    print(f"[installer] {msg}", flush=True)


def fail(msg):
    raise InstallerError(msg)


class _Tee:
    """Write to several streams at once (detached worker: log + console)."""

    def __init__(self, *streams):
        self.streams = streams

    def write(self, s):
        for st in self.streams:
            try:
                st.write(s)
                st.flush()
            except Exception:
                pass
        return len(s)

    def flush(self):
        for st in self.streams:
            try:
                st.flush()
            except Exception:
                pass


def _setup_console_tee():
    """Detached worker: mirror stdout/stderr to /dev/console as well."""
    try:
        console = open("/dev/console", "w", buffering=1)
    except Exception:
        return
    sys.stdout = _Tee(sys.stdout, console)
    sys.stderr = _Tee(sys.stderr, console)


# ------------------------------------------------------------------ build_disk import
_bd = None


def bd():
    """Import build_disk (GPT/Fat32/grub helpers) — shipped beside this
    file in the installer ISO; in a repo checkout it sits at the root."""
    global _bd
    if _bd is not None:
        return _bd
    here = Path(__file__).resolve().parent
    for cand in (here, here.parent, Path("/usr/local/lib/neurai"), Path.cwd()):
        if (cand / "build_disk.py").exists() and str(cand) not in sys.path:
            sys.path.insert(0, str(cand))
    try:
        import build_disk
    except Exception as e:
        fail(f"cannot import build_disk.py (needed for GPT/FAT32 writers): {e}")
    _bd = build_disk
    return _bd


# ------------------------------------------------------------------ small helpers
def run(cmd, check=True):
    log("$ " + " ".join(str(c) for c in cmd))
    try:
        r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True)
    except FileNotFoundError as e:
        if check:
            fail(f"command not found: {cmd[0]} ({e})")
        return 127, "", str(e)
    out = (r.stdout or "") + (r.stderr or "")
    for line in out.rstrip().splitlines():
        print(f"  {line}", flush=True)
    if check and r.returncode != 0:
        fail(f"command failed ({r.returncode}): {' '.join(str(c) for c in cmd)}")
    return r.returncode, r.stdout or "", r.stderr or ""


def try_run(cmd):
    try:
        r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True)
        return r.returncode
    except Exception:
        return 127


def load_modules():
    """Best-effort: insmod /lib/modules/order.txt (loader pattern), mdev -s."""
    order = Path("/lib/modules/order.txt")
    if order.exists():
        n = 0
        for line in order.read_text(errors="replace").splitlines():
            name = line.strip()
            if name:
                try_run(["insmod", f"/lib/modules/{name}"])
                n += 1
        log(f"kernel modules: attempted {n} from /lib/modules/order.txt")
    else:
        log("no /lib/modules/order.txt - assuming drivers are built in")
    try_run(["mdev", "-s"])


# ------------------------------------------------------------------ inventory
def read_mounts():
    """device path -> mountpoint, from /proc/mounts."""
    out = {}
    try:
        for line in Path("/proc/mounts").read_text().splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0].startswith("/dev/"):
                out[parts[0]] = parts[1]
    except Exception:
        pass
    return out


def block_info():
    """All block devices from /sys/class/block."""
    base = Path("/sys/class/block")
    out = []
    if not base.exists():
        return out
    for p in sorted(base.iterdir(), key=lambda x: x.name):
        try:
            size_sectors = int((p / "size").read_text().strip())
        except Exception:
            size_sectors = 0
        is_part = (p / "partition").exists()
        model = ""
        try:
            mf = p / "device" / "model"
            if mf.exists():
                model = mf.read_text().strip()
        except Exception:
            pass
        parent = None
        if is_part:
            try:
                parent = Path(os.path.realpath(p)).parent.name
            except Exception:
                parent = None
        out.append({
            "name": p.name, "dev": f"/dev/{p.name}", "partition": is_part,
            "size_sectors": size_sectors, "model": model, "parent": parent,
        })
    return out


def is_installable_disk(b):
    if b["partition"]:
        return False
    n = b["name"]
    if n.startswith(("loop", "ram", "dm-", "sr", "fd")):
        return False
    return b["size_sectors"] > 0


def disk_mounted(name, blocks, mounts):
    devs = {f"/dev/{name}"} | {b["dev"] for b in blocks if b["parent"] == name}
    return any(d in mounts for d in devs)


def print_inventory(blocks, mounts):
    print("[installer] block devices:", flush=True)
    parts = {b["name"]: b for b in blocks if b["partition"]}
    for b in blocks:
        if b["partition"] or b["name"].startswith("sr"):
            continue
        size_gib = b["size_sectors"] * SECTOR / (1024 ** 3)
        mnt = " MOUNTED" if disk_mounted(b["name"], blocks, mounts) else ""
        print(f"[installer]   {b['dev']}  {size_gib:.1f} GiB  {b['model'] or '(no model)'}{mnt}", flush=True)
        for pb in blocks:
            if pb["parent"] == b["name"]:
                pg = pb["size_sectors"] * SECTOR / (1024 ** 3)
                pm = " mounted at " + mounts[pb["dev"]] if pb["dev"] in mounts else ""
                print(f"[installer]     └ {pb['dev']}  {pg:.1f} GiB{pm}", flush=True)
    for b in blocks:  # orphan partitions / optical drives etc.
        if not b["partition"] and b["name"].startswith(("sr",)):
            size_gib = b["size_sectors"] * SECTOR / (1024 ** 3)
            print(f"[installer]   {b['dev']}  {size_gib:.1f} GiB  {b['model'] or 'optical'}", flush=True)
    _ = parts


def part_dev_name(disk_name, idx):
    return disk_name + (f"p{idx}" if disk_name[-1].isdigit() else f"{idx}")


def base_disk_of(dev, blocks):
    """'/dev/sdb1' -> 'sdb'; '/dev/sdb' -> 'sdb'; '/dev/sr0' -> 'sr0'."""
    name = dev.rsplit("/", 1)[-1]
    for b in blocks:
        if b["name"] == name:
            return b["parent"] if b["partition"] else b["name"]
    # fall back to name stripping rules
    for b in blocks:
        if not b["partition"] and name.startswith(b["name"]):
            return b["name"]
    return name


def device_for_path(path, mounts):
    """Device backing a filesystem path, by longest mountpoint prefix."""
    best, best_len = None, -1
    for dev, mnt in mounts.items():
        if path == mnt or path.startswith(mnt.rstrip("/") + "/"):
            if len(mnt) > best_len:
                best, best_len = dev, len(mnt)
    return best


# ------------------------------------------------------------------ payload
def _locate_payload_in(root: Path):
    for cand in (root / "neurai-payload", root / "NEURAI_PAYLOAD", root):
        if (cand / "manifest.json").exists():
            return cand
    return None


def find_payload(explicit):
    """Returns (payload_dir, source_device_or_None). Keeps an auto-scan
    mount alive at /mnt/probe when the payload came from one."""
    if explicit:
        d = Path(explicit)
        hit = _locate_payload_in(d) if d.exists() else None
        if hit is None and (d / "manifest.json").exists():
            hit = d
        if hit is None:
            fail(f"--payload {explicit}: no manifest.json found there")
        return hit, device_for_path(str(hit), read_mounts())
    for cand in ("/neurai-payload", "/mnt/usb/neurai-payload",
                 "/mnt/payload", "/mnt/payload/neurai-payload"):
        if (Path(cand) / "manifest.json").exists():
            log(f"payload: found at {cand}")
            return Path(cand), device_for_path(cand, read_mounts())
    # auto-scan: partitions first, then whole disks (incl. the CD, sr0)
    blocks = block_info()
    scan = [b for b in blocks if b["partition"]]
    scan += [b for b in blocks if not b["partition"]
             and not b["name"].startswith(("loop", "ram", "dm-"))]
    MOUNT_PROBE.mkdir(parents=True, exist_ok=True)
    for b in scan:
        dev = b["dev"]
        if not Path(dev).exists():
            continue
        for fstype in ("ext4", "vfat", "exfat", "iso9660", "udf"):
            if try_run(["mount", "-t", fstype, "-o", "ro", dev, str(MOUNT_PROBE)]) != 0:
                continue
            hit = _locate_payload_in(MOUNT_PROBE)
            if hit is not None:
                log(f"payload: found at {hit} (on {dev}, {fstype})")
                return hit, dev
            try_run(["umount", str(MOUNT_PROBE)])
            break  # mounted fine but no payload - next device
    fail("no payload found: no manifest.json at the fixed paths or on any "
         "scannable partition/disk (looked for neurai-payload/manifest.json)")


def verify_payload(payload_dir: Path):
    mpath = payload_dir / "manifest.json"
    try:
        manifest = json.loads(mpath.read_text())
    except Exception as e:
        fail(f"payload manifest unreadable: {e}")
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        fail("payload manifest has no files table")
    log(f"payload: verifying {len(files)} files against manifest "
        f"(os {manifest.get('os_version')}, installer {manifest.get('installer_version')})")
    for rel in sorted(files):
        meta = files[rel]
        fp = payload_dir / rel
        if not fp.exists():
            fail(f"payload file missing: {rel}")
        size = fp.stat().st_size
        if size != meta.get("size"):
            fail(f"payload size mismatch: {rel} is {size}, manifest says {meta.get('size')}")
        h = hashlib.sha256()
        with open(fp, "rb") as f:
            while True:
                chunk = f.read(8 * MIB)
                if not chunk:
                    break
                h.update(chunk)
        if h.hexdigest() != meta.get("sha256"):
            fail(f"payload sha256 mismatch: {rel}")
        log(f"payload: ok {rel} ({size // MIB} MiB)")
    return manifest


# ------------------------------------------------------------------ plan
def mem_total_mib():
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) // 1024
    except Exception:
        pass
    return 8192


def role_sizes(role):
    integrity_mib = 8192 if role == "memory" else 2048
    if role == "inference":
        swap_mib = min(8192, max(2048, mem_total_mib()))
    else:
        swap_mib = 2048
    return integrity_mib, swap_mib


def build_plan(layout, role, disks):
    """disks: {disk_dev: total_sectors}. Returns
    {disk_dev: {'total_sectors': n, 'parts': [(guid, name, first, last, mib)]}}"""
    b = bd()
    integrity_mib, swap_mib = role_sizes(role)
    fixed = [  # (guid, name, mib) in on-disk order
        (b.GPT_BIOS, "biosboot", 1),
        (b.GPT_EFI, "esp", 512),
        (b.GPT_LINUX, "root", 5120),
        (b.GPT_LINUX, "integrity", integrity_mib),
        (b.GPT_SWAP, "swap", swap_mib),
    ]
    fixed_mib = sum(m for _, _, m in fixed)
    plan = {}
    if layout == "single":
        dev = next(iter(disks))
        total = disks[dev]
        need_mib = fixed_mib + 4096
        if total * SECTOR < need_mib * MIB + (FIRST_LBA + 34) * SECTOR:
            fail(f"{dev} too small for layout single + role {role}: needs at "
                 f"least ~{need_mib} MiB of partitions, disk is {total * SECTOR // MIB} MiB")
        lba = FIRST_LBA
        parts = []
        for guid, name, mib in fixed:
            nsec = mib * MIB // SECTOR
            parts.append((guid, name, lba, lba + nsec - 1, mib))
            lba += nsec
        last = total - 34
        data_mib = (last - lba + 1) * SECTOR // MIB
        parts.append((b.GPT_LINUX, "data", lba, last, data_mib))
        plan[dev] = {"total_sectors": total, "parts": parts}
    elif layout == "split":
        sys_dev, data_dev = list(disks)[:2]
        total_sys = disks[sys_dev]
        if total_sys * SECTOR < fixed_mib * MIB + (FIRST_LBA + 34) * SECTOR:
            fail(f"{sys_dev} too small for the system partitions (role {role}): "
                 f"needs at least ~{fixed_mib} MiB, disk is {total_sys * SECTOR // MIB} MiB")
        lba = FIRST_LBA
        parts = []
        for guid, name, mib in fixed:
            nsec = mib * MIB // SECTOR
            parts.append((guid, name, lba, lba + nsec - 1, mib))
            lba += nsec
        plan[sys_dev] = {"total_sectors": total_sys, "parts": parts}
        total_data = disks[data_dev]
        last = total_data - 34
        data_mib = (last - FIRST_LBA + 1) * SECTOR // MIB
        if data_mib < 4096:
            fail(f"{data_dev} too small for the data volume: needs at least "
                 f"4096 MiB, has {total_data * SECTOR // MIB} MiB")
        plan[data_dev] = {"total_sectors": total_data,
                          "parts": [(b.GPT_LINUX, "data", FIRST_LBA, last, data_mib)]}
    else:
        fail(f"unknown layout: {layout}")
    return plan


def print_plan(plan, layout, role, node_name, blocks):
    by_name = {b["name"]: b for b in blocks}
    print(f"[installer] plan: layout={layout} role={role} node={node_name}", flush=True)
    for dev, spec in plan.items():
        name = dev.rsplit("/", 1)[-1]
        info = by_name.get(name, {})
        gib = spec["total_sectors"] * SECTOR / (1024 ** 3)
        print(f"[installer]   ERASES {dev} ({gib:.1f} GiB, {info.get('model') or 'unknown model'})", flush=True)
        for guid, pname, first, last, mib in spec["parts"]:
            print(f"[installer]     {pname:<10} {mib:>6} MiB  LBA {first}..{last}", flush=True)


# ------------------------------------------------------------------ writing
def patch_grub(core_bytes, boot_img_bytes, bios_lba):
    """Same BIOS embedding patch build_disk.main applies: GRUB's
    boot.img kernel_sector (0x5C) := biosboot LBA; core.img diskboot
    blocklist start (0x1F4) := biosboot LBA + 1. Pristine values are
    asserted first, exactly as build_disk does."""
    core = bytearray(core_bytes)
    if len(core) < 0x1FC + 10:
        fail("grub core.img too small to patch")
    bl_start, bl_len = struct.unpack_from("<QH", core, 0x1F4)
    expect_len = -(-len(core) // SECTOR) - 1
    if not (bl_start == 2 and bl_len == expect_len):
        fail(f"grub core.img blocklist not pristine: start={bl_start} len={bl_len}, "
             f"expected start=2 len={expect_len} - refusing to patch")
    struct.pack_into("<Q", core, 0x1F4, bios_lba + 1)
    bc = bytearray(boot_img_bytes[:440])
    bc += bytes(440 - len(bc))
    cur = struct.unpack_from("<Q", bc, 0x5C)[0]
    if cur != 1:
        fail(f"grub boot.img kernel_sector not pristine: {cur} != 1 - refusing to patch")
    struct.pack_into("<Q", bc, 0x5C, bios_lba)
    return bytes(core), bytes(bc)


def write_tables(disk_dev, total_sectors, parts, boot_code):
    b = bd()
    triples = [(g, n, f, l) for (g, n, f, l, _m) in parts]
    gpt = b.gpt_bytes(total_sectors, triples, Path(disk_dev).name)
    with open(disk_dev, "r+b") as f:
        mbr = bytearray(512)
        if boot_code:
            mbr[0:440] = boot_code[:440]
        mbr[446:462] = bytes([0, 0, 2, 0, 0xEE, 0xFF, 0xFF, 0xFF, 1, 0, 0, 0]) + \
            struct.pack("<I", min(total_sectors - 1, 0xFFFFFFFF))
        mbr[510:512] = b"\x55\xaa"
        f.seek(0)
        f.write(bytes(mbr))
        for lba in sorted(gpt):
            f.seek(lba * SECTOR)
            f.write(gpt[lba])
        f.flush()
        os.fsync(f.fileno())
        try:
            import fcntl
            fcntl.ioctl(f.fileno(), BLKRRPART, struct.pack("q", 0))
            log(f"{disk_dev}: partition table written, BLKRRPART ok")
        except OSError as e:
            log(f"{disk_dev}: partition table written, BLKRRPART said {e} (continuing)")
    try_run(["mdev", "-s"])


def wait_for_partitions(disk_dev, count):
    name = disk_dev.rsplit("/", 1)[-1]
    want = [f"/dev/{part_dev_name(name, i)}" for i in range(1, count + 1)]
    t0 = time.time()
    while time.time() - t0 < 20:
        if all(os.path.exists(p) for p in want):
            return want
        try_run(["mdev", "-s"])
        time.sleep(0.5)
    missing = [p for p in want if not os.path.exists(p)]
    fail(f"partition device nodes did not appear for {disk_dev}: missing {missing}")


def write_partition_bytes(part_dev, data):
    with open(part_dev, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    log(f"wrote {len(data) / MIB:.1f} MiB to {part_dev}")


def build_esp_image(payload_dir, esp_part, serial_test):
    b = bd()
    _g, _n, first, last, _m = esp_part
    fat = b.Fat32(total_sectors=last - first + 1, hidden=first)
    cfg = (payload_dir / "grub" / "grub.cfg").read_bytes()
    if serial_test:
        cfg = cfg.replace(b"console=tty0", b"console=tty0 console=ttyS0")
        log("serial-test: grub.cfg console=tty0 -> console=tty0 console=ttyS0")
    img, _rc = fat.build({
        "EFI/BOOT/BOOTX64.EFI": (payload_dir / "grub" / "BOOTX64.EFI").read_bytes(),
        "BOOT/VMLINUZ": (payload_dir / "vmlinuz").read_bytes(),
        "BOOT/INITRD.GZ": (payload_dir / "initrd.gz").read_bytes(),
        "BOOT/GRUB/GRUB.CFG": cfg,
    })
    return img


def find_real_mke2fs():
    """The BusyBox mke2fs applet (usually first on PATH via /bin) cannot
    make a flagged ext4 - it rejects -t/-O outright. The installer ISO
    ships the real e2fsprogs binary at /usr/sbin/mke2fs; resolve it by
    absolute path and never accept a symlink into busybox."""
    for cand in ("/usr/sbin/mke2fs", "/sbin/mke2fs", "/usr/bin/mke2fs"):
        if os.path.exists(cand) and "busybox" not in os.path.realpath(cand):
            return cand
    fail("real mke2fs not found (the BusyBox applet cannot create the "
         "required ext4 filesystems) - boot the installer ISO")


def format_ext4(dev, label):
    run([find_real_mke2fs(), "-q", "-t", "ext4", "-F", "-b", "4096",
         "-O", "^metadata_csum,^metadata_csum_seed", "-L", label, dev])


def mount(dev, mnt, fstype="ext4", ro=False):
    mnt.mkdir(parents=True, exist_ok=True)
    args = ["mount", "-t", fstype]
    if ro:
        args += ["-o", "ro"]
    args += [dev, str(mnt)]
    run(args)


def umount(mnt):
    try_run(["umount", str(mnt)])


def extract_tar(tar_path, dest):
    log(f"extracting {tar_path.name} -> {dest} ...")
    with tarfile.open(tar_path) as tf:
        try:
            tf.extractall(dest, filter="data")
        except Exception as e:
            log(f"tar filter=data refused something ({e}); retrying fully-trusted")
            tf.extractall(dest)


def do_install(plan, layout, role, node_name, payload_dir, serial_test):
    sys_dev = next(iter(plan))
    sys_parts = plan[sys_dev]["parts"]
    part_by_name = {}
    dev_by_name = {}
    for dev, spec in plan.items():
        dname = dev.rsplit("/", 1)[-1]
        devs = wait_list = None
        for idx, (g, pname, first, last, mib) in enumerate(spec["parts"], start=1):
            part_by_name[pname] = (dev, first, last)
            dev_by_name[pname] = f"/dev/{part_dev_name(dname, idx)}"
    # GRUB patch (system disk, biosboot is always the first partition)
    core_raw = (payload_dir / "grub" / "core.img").read_bytes()
    boot_raw = (payload_dir / "grub" / "boot.img").read_bytes()
    bios_lba = part_by_name["biosboot"][1]
    core_patched, boot_code = patch_grub(core_raw, boot_raw, bios_lba)
    log(f"grub: BIOS embedding patched for biosboot at LBA {bios_lba}")
    # 1) partition tables
    for dev, spec in plan.items():
        bc = boot_code if dev == sys_dev else None
        write_tables(dev, spec["total_sectors"], spec["parts"], bc)
    # 2) partition nodes
    for dev, spec in plan.items():
        wait_for_partitions(dev, len(spec["parts"]))
    # 3) raw partition content: BIOS core + ESP
    write_partition_bytes(dev_by_name["biosboot"], core_patched)
    esp_img = build_esp_image(payload_dir,
                              [p for p in sys_parts if p[1] == "esp"][0],
                              serial_test)
    write_partition_bytes(dev_by_name["esp"], esp_img)
    # 4) filesystems
    format_ext4(dev_by_name["root"], "NEURAI_ROOT")
    format_ext4(dev_by_name["integrity"], "NEURAI_INTEGRITY")
    format_ext4(dev_by_name["data"], "NEURAI_DATA")
    run(["mkswap", dev_by_name["swap"]])
    # 5) populate
    mounted = []
    try:
        mount(dev_by_name["root"], MOUNT_ROOT)
        mounted.append(MOUNT_ROOT)
        mount(dev_by_name["integrity"], MOUNT_INT)
        mounted.append(MOUNT_INT)
        mount(dev_by_name["data"], MOUNT_DATA)
        mounted.append(MOUNT_DATA)
        extract_tar(payload_dir / "rootfs.tar", MOUNT_ROOT)
        extract_tar(payload_dir / "integrity.tar", MOUNT_INT)
        extract_tar(payload_dir / "data-seed.tar", MOUNT_DATA)
        # 6) seeds (after extraction, so they win over staged defaults)
        release = (
            f"role={role}\n"
            f"layout={layout}\n"
            f"node={node_name}\n"
            f"version={OS_VERSION}\n"
            f"installer_version={INSTALLER_VERSION}\n"
            f"installed={datetime.date.today().isoformat()}\n"
        )
        (MOUNT_ROOT / "etc").mkdir(parents=True, exist_ok=True)
        (MOUNT_ROOT / "etc" / "neurai-release").write_text(release)
        log(f"seed: etc/neurai-release role={role} layout={layout} node={node_name}")
        home = MOUNT_DATA / "home"
        home.mkdir(parents=True, exist_ok=True)
        (home / "node.json").write_text(json.dumps(
            {"name": node_name, "role": role, "layout": layout, "peers": []},
            indent=2) + "\n")
        log(f"seed: data/home/node.json name={node_name} role={role}")
        run(["sync"], check=False)
    finally:
        for m in reversed(mounted):
            umount(m)
    # 7) verification (see verify_install)
    verify_install(plan, dev_by_name, role)


def _find_case_insensitive(root: Path, rel_upper):
    for dirpath, _dirs, files in os.walk(root):
        for fn in files:
            full = str(Path(dirpath) / fn)
            rel = full[len(str(root)):].lstrip("/").upper()
            if rel == rel_upper:
                return True
    return False


def verify_install(plan, dev_by_name, role):
    log("verifying written disks ...")
    for dev in plan:
        with open(dev, "rb") as f:
            f.seek(SECTOR)
            sig = f.read(8)
        if sig != b"EFI PART":
            fail(f"verify: {dev} LBA1 does not start with 'EFI PART'")
        log(f"verify: {dev} GPT header ok")
    checks = []

    def ro_check(dev, fstype, mnt, check_fn, what):
        mount(dev, mnt, fstype=fstype, ro=True)
        try:
            ok = check_fn(mnt)
        finally:
            umount(mnt)
        log(f"verify: {what}: {'ok' if ok else 'MISSING'}")
        checks.append(ok)

    ro_check(dev_by_name["root"], "ext4", MOUNT_VERIFY,
             lambda m: (m / "etc" / "neurai-release").exists()
             and f"role={role}" in (m / "etc" / "neurai-release").read_text(),
             "root marker etc/neurai-release")
    ro_check(dev_by_name["data"], "ext4", MOUNT_VERIFY,
             lambda m: (m / ".neurai-data").exists(),
             "data marker .neurai-data")
    ro_check(dev_by_name["integrity"], "ext4", MOUNT_VERIFY,
             lambda m: (m / ".neurai-integrity").exists(),
             "integrity marker .neurai-integrity")
    ro_check(dev_by_name["esp"], "vfat", MOUNT_VERIFY,
             lambda m: _find_case_insensitive(m, "EFI/BOOT/BOOTX64.EFI"),
             "ESP file EFI/BOOT/BOOTX64.EFI")
    if not all(checks):
        fail("post-write verification failed (see lines above)")


# ------------------------------------------------------------------ detach
def maybe_detach(args):
    """`--auto` via the NeurAI shell `!` escape would be killed by its
    20 s timeout; detach so the install outlives it. The worker mirrors
    output to /dev/console and /tmp/neurai-install.log."""
    if not args.auto or args.foreground or args.dry_run:
        return False
    if os.environ.get("NEURAI_INSTALL_DETACHED") == "1":
        return False
    if os.environ.get("NEURAI_INSTALL_FOREGROUND") == "1":
        return False
    lf = open(LOG_PATH, "ab", buffering=0)
    env = dict(os.environ)
    env["NEURAI_INSTALL_DETACHED"] = "1"
    p = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve())] + sys.argv[1:],
        stdout=lf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
        env=env, start_new_session=True)
    print(f"[installer] install running in the background (pid {p.pid}).", flush=True)
    print(f"[installer] progress streams to this console and {LOG_PATH}; "
          f"the worker prints a success line on this console when done.", flush=True)
    return True


# ------------------------------------------------------------------ main
def parse_args(argv):
    ap = argparse.ArgumentParser(prog="neurai-install", description="NeurAI universal installer")
    ap.add_argument("--list", action="store_true", help="inventory block devices and exit")
    ap.add_argument("--auto", action="store_true", help="non-interactive (requires --layout, disk flags, --yes)")
    ap.add_argument("--dry-run", action="store_true", help="verify payloads + plan + sizes; write nothing")
    ap.add_argument("--layout", choices=["single", "split"])
    ap.add_argument("--system-disk")
    ap.add_argument("--data-disk", help="split layout only")
    ap.add_argument("--role", choices=["interface", "inference", "memory"], default="interface")
    ap.add_argument("--node-name", default="neurai-1")
    ap.add_argument("--payload", help="payload directory (default: auto-discover)")
    ap.add_argument("--serial-test", action="store_true",
                    help="write grub.cfg with an added serial console (tests only)")
    ap.add_argument("--yes", action="store_true", help="confirm erasure in --auto mode")
    ap.add_argument("--foreground", action="store_true",
                    help="do not detach in --auto mode")
    return ap.parse_args(argv)


def main(argv=None):
    args = parse_args(argv if argv is not None else sys.argv[1:])
    if os.environ.get("NEURAI_INSTALL_DETACHED") == "1":
        _setup_console_tee()
    try:
        log(f"NeurAI universal installer {INSTALLER_VERSION} (OS {OS_VERSION})")
        load_modules()
        blocks = block_info()
        mounts = read_mounts()
        if args.list:
            print_inventory(blocks, mounts)
            return 0
        if maybe_detach(args):
            return 0
        # payload: discover + verify BEFORE anything is written
        payload_dir, payload_dev = find_payload(args.payload)
        verify_payload(payload_dir)
        # resolve layout / disks / role / node
        layout, sys_disk, data_disk = args.layout, args.system_disk, args.data_disk
        role, node_name = args.role, args.node_name
        interactive = not args.auto
        if interactive:
            print_inventory(blocks, mounts)
            if not layout:
                layout = (input("[installer] layout [single/split] (single): ").strip() or "single")
            installables = [b for b in blocks if is_installable_disk(b)]
            if not sys_disk:
                if layout == "single":
                    sys_disk = input("[installer] disk to install on (e.g. /dev/sda): ").strip()
                else:
                    sys_disk = input("[installer] system disk (e.g. /dev/sda): ").strip()
            if layout == "split" and not data_disk:
                data_disk = input("[installer] data disk (e.g. /dev/sdb): ").strip()
            r = input(f"[installer] role [interface/inference/memory] ({role}): ").strip()
            if r:
                role = r
            n = input(f"[installer] node name ({node_name}): ").strip()
            if n:
                node_name = n
            _ = installables
        # ---- validation shared by auto / interactive / dry-run
        if layout not in ("single", "split"):
            fail("--layout single|split is required")
        if role not in ("interface", "inference", "memory"):
            fail(f"unknown role: {role}")
        if not sys_disk:
            fail("a system disk is required (--system-disk /dev/X)")
        disks_order = [sys_disk]
        if layout == "split":
            if not data_disk:
                fail("layout split requires --data-disk /dev/Y")
            if data_disk == sys_disk:
                fail("system and data disks must differ")
            disks_order.append(data_disk)
        elif data_disk:
            fail("--data-disk is only valid with --layout split")
        if args.auto and not args.yes:
            fail("--auto requires --yes (it erases the named disks)")
        by_dev = {b["dev"]: b for b in blocks}
        disks = {}
        for dev in disks_order:
            info = by_dev.get(dev)
            if info is None or info["partition"] or not is_installable_disk(info):
                fail(f"{dev} is not an installable whole disk on this machine")
            if disk_mounted(info["name"], blocks, mounts):
                fail(f"refusing {dev}: it (or one of its partitions) is currently mounted")
            disks[dev] = info["size_sectors"]
        if payload_dev:
            pb = base_disk_of(payload_dev, blocks)
            for dev in disks_order:
                if pb == dev.rsplit("/", 1)[-1]:
                    fail(f"refusing {dev}: the payload itself lives on it ({payload_dev})")
        plan = build_plan(layout, role, disks)
        print_plan(plan, layout, role, node_name, blocks)
        if args.dry_run:
            log("DRY RUN: payload verified, plan valid, disk sizes ok - nothing written")
            return 0
        if interactive:
            for dev in disks_order:
                typed = input(f"[installer] type {dev} exactly to confirm ERASURE: ").strip()
                if typed != dev:
                    fail(f"confirmation mismatch ('{typed}' != '{dev}') - aborted, nothing written")
        do_install(plan, layout, role, node_name, payload_dir, args.serial_test)
        try_run(["umount", str(MOUNT_PROBE)])
        print("INSTALL COMPLETE", flush=True)
        return 0
    except InstallerError as e:
        print(f"[installer] ERROR: {e}", flush=True)
        return 1
    except EOFError:
        print("[installer] ERROR: input closed - aborted, nothing written", flush=True)
        return 1
    except KeyboardInterrupt:
        print("[installer] ERROR: interrupted - install may be incomplete", flush=True)
        return 130


if __name__ == "__main__":
    sys.exit(main())
