#!/usr/bin/env python3
"""
NeurAI ISO builder (pure Python, no xorriso needed).
Assembles: Debian kernel + BusyBox + host Python3 + NeurAI core
into an initramfs, then a bootable ISO9660/El Torito (BIOS isolinux)
image with pycdlib. UEFI machines that support Legacy/CSM boot, and
all BIOS machines, boot this directly. For pure-UEFI, build-iso.sh
(grub-mkrescue) produces the dual-mode image from the same rootfs.
"""
import os, sys, gzip, shutil, subprocess, io
from pathlib import Path

ROOT = Path(__file__).parent
B = ROOT / ".build"; TOOLS = B / "tools"; RFS = B / "rootfs"; ISO = B / "isoroot"
OUT = ROOT / "dist" / "neurai-os-2.0.0-amd64.iso"

def extract_debs():
    TOOLS.mkdir(parents=True, exist_ok=True)
    for deb in (B / "debs").glob("*.deb"):
        dest = TOOLS / deb.stem
        if dest.exists(): continue
        dest.mkdir(parents=True)
        subprocess.run(["dpkg-deb", "-x", str(deb), str(dest)], check=True)
    print("debs extracted")

def find(patterns, base):
    for p in patterns:
        hits = sorted(x for x in Path(base).rglob(p) if x.is_file())
        if hits: return hits[0]
    raise FileNotFoundError(patterns)

def copy_with_libs(binary, dest_root):
    """Copy a dynamic binary + its ldd shared libs into dest_root."""
    import re
    shutil.copy2(binary, dest_root / binary.lstrip("/"))
    out = subprocess.run(["ldd", binary], capture_output=True, text=True).stdout
    for m in re.finditer(r"=>\s+(/\S+)|^\s+(/\S+)", out, re.M):
        lib = m.group(1) or m.group(2)
        if lib and Path(lib).exists():
            d = dest_root / lib.lstrip("/"); d.parent.mkdir(parents=True, exist_ok=True)
            if not d.exists(): shutil.copy2(lib, d)

def build_rootfs():
    if RFS.exists(): shutil.rmtree(RFS)
    for d in ["bin","sbin","proc","sys","dev","etc","root","tmp","var/lib/neurai","var/log",
              "usr/local/lib/neurai","usr/bin","boot","mnt"]:
        (RFS / d).mkdir(parents=True, exist_ok=True)
    busybox = find(["busybox"], TOOLS)
    shutil.copy2(busybox, RFS / "bin/busybox")
    os.chmod(RFS / "bin/busybox", 0o755)
    # Applet symlinks must exist before /init runs (its shebang is /bin/sh)
    applets = subprocess.run([str(RFS / "bin/busybox"), "--list"], capture_output=True, text=True).stdout.split()
    for ap in applets:
        for d in ["bin", "sbin", "usr/bin"]:
            link = RFS / d / ap
            if not link.exists() and not link.is_symlink():
                link.parent.mkdir(parents=True, exist_ok=True)
                try: os.symlink("/bin/busybox", link)
                except FileExistsError: pass
                break
    # python3 generic name
    p3 = RFS / "usr/bin/python3"
    if not p3.exists() and not p3.is_symlink(): os.symlink("python3.12", p3)
    # Python3 runtime from host (interpreter + stdlib + libs)
    copy_with_libs("/usr/bin/python3.12", RFS)
    pylib = Path("/usr/lib/python3.12")
    shutil.copytree(pylib, RFS / "usr/lib/python3.12",
                    ignore=shutil.ignore_patterns("test","tests","__pycache__","lib2to3","idlelib","tkinter","ensurepip"))
    # extra libs python modules need
    for libdir in ["/usr/lib/x86_64-linux-gnu", "/lib/x86_64-linux-gnu"]:
        for pat in ["libbz2.so*","liblzma.so*","libffi.so*","libssl.so*","libcrypto.so*","libsqlite3.so*","libz.so*","libexpat.so*","libstdc++.so*","libgcc_s.so*","libgomp.so*"]:
            for f in Path(libdir).glob(pat):
                d = RFS / "usr/lib" / f.name
                if not d.exists(): shutil.copy2(f, d, follow_symlinks=False) if f.is_symlink() else shutil.copy2(f, d)
    # NeurAI core + init
    shutil.copy2(ROOT / "neurai/neurai.py", RFS / "usr/local/lib/neurai/neurai.py")
    shutil.copy2(ROOT / "neurai/ui.html", RFS / "usr/local/lib/neurai/ui.html")
    # Bundled local language model (v2 brain): llama.cpp + GGUF model
    llama_src = ROOT / "llm"
    llama_dst = RFS / "usr/local/lib/neurai/llama"; llama_dst.mkdir(parents=True, exist_ok=True)
    comp = find(["llama-completion"], llama_src)
    shutil.copy2(comp, llama_dst / "llama-completion"); os.chmod(llama_dst / "llama-completion", 0o755)
    for so in comp.parent.glob("*.so*"):
        if so.is_file(): shutil.copy2(so, llama_dst / so.name)
    models_dst = RFS / "usr/local/lib/neurai/models"; models_dst.mkdir(parents=True, exist_ok=True)
    model_file = llama_src / "model.gguf"
    if not model_file.exists(): raise SystemExit("llm/model.gguf missing - download the GGUF model first (see README)")
    shutil.copy2(model_file, models_dst / "model.gguf")
    print("bundled model:", model_file.stat().st_size // 1048576, "MB")
    shutil.copy2(ROOT / "rootfs/init", RFS / "init"); os.chmod(RFS / "init", 0o755)
    (RFS / "etc/hostname").write_text("neurai-os\n")
    (RFS / "etc/passwd").write_text("root::0:0:root:/root:/bin/sh\n")
    print("rootfs built:", sum(f.stat().st_size for f in RFS.rglob("*") if f.is_file()) // 1048576, "MB")

def cpio_newc(root: Path) -> bytes:
    """Minimal newc cpio writer."""
    out = io.BytesIO()
    ino = [1]
    def emit(name, mode, data=b"", linkname=None):
        ino[0] += 1
        if linkname is not None: data = linkname.encode()
        fields = [ino[0], mode, 0, 0, 1, 0, len(data), 0, 0, 0, 0, len(name) + 1, 0]
        hdr = "070701" + "".join(f"{f:08x}" for f in fields)
        out.write(hdr.encode()); out.write(name.encode() + b"\0")
        pad = (4 - (110 + len(name) + 1) % 4) % 4; out.write(b"\0" * pad)
        out.write(data); pad = (4 - len(data) % 4) % 4; out.write(b"\0" * pad)
    emit(".", 0o40755)
    for p in sorted(root.rglob("*")):
        rel = str(p.relative_to(root))
        if p.is_symlink(): emit(rel, 0o120777, linkname=os.readlink(p))
        elif p.is_dir(): emit(rel, 0o40000 | (p.stat().st_mode & 0o777))
        elif p.is_file(): emit(rel, 0o100000 | (p.stat().st_mode & 0o777), p.read_bytes())
    emit("TRAILER!!!", 0)
    return out.getvalue()

def build_initramfs():
    raw = cpio_newc(RFS)
    dest = B / "initramfs.gz"
    with open(dest, "wb") as f:
        with gzip.GzipFile(filename="", mode="wb", fileobj=f, compresslevel=1, mtime=0) as g: g.write(raw)
    print("initramfs.gz:", dest.stat().st_size // 1048576, "MB")
    return dest

def build_iso(initramfs):
    import pycdlib
    if ISO.exists(): shutil.rmtree(ISO)
    (ISO / "boot").mkdir(parents=True)
    kernel = find(["vmlinuz-*"], TOOLS)
    shutil.copy2(kernel, ISO / "boot/vmlinuz")
    shutil.copy2(initramfs, ISO / "boot/initramfs.gz")
    isolinux_bin = find(["isolinux.bin"], TOOLS)
    ldlinux = find(["ldlinux.c32"], TOOLS)
    shutil.copy2(isolinux_bin, ISO / "boot/isolinux.bin")
    shutil.copy2(ldlinux, ISO / "boot/ldlinux.c32")
    isohdpfx = find(["isohdpfx.bin"], TOOLS)
    (ISO / "boot/isolinux.cfg").write_text(
        "DEFAULT neurai\nPROMPT 0\nTIMEOUT 50\n"
        "LABEL neurai\n  KERNEL /boot/vmlinuz\n  APPEND initrd=/boot/initramfs.gz console=tty0 quiet loglevel=3\n"
        "LABEL verbose\n  KERNEL /boot/vmlinuz\n  APPEND initrd=/boot/initramfs.gz console=tty0 loglevel=7\n"
        "LABEL rescue\n  KERNEL /boot/vmlinuz\n  APPEND initrd=/boot/initramfs.gz console=tty0 init=/bin/sh\n")
    shutil.copy2(ROOT / "README.md", ISO / "README.md")
    shutil.copy2(ROOT / "install-to-disk.sh", ISO / "install-to-disk.sh")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    iso = pycdlib.PyCdlib()
    iso.new(interchange_level=3, joliet=3, rock_ridge="1.09", vol_ident="NEURAI_OS", sys_ident="LINUX")
    for p in sorted(ISO.rglob("*")):
        if p.is_dir():
            rel = "/" + str(p.relative_to(ISO)).upper().replace("-", "_")
            iso.add_directory(iso_path=rel, joliet_path="/" + str(p.relative_to(ISO)), rr_name=p.name)
    for p in sorted(ISO.rglob("*")):
        if p.is_file():
            rel = "/" + str(p.relative_to(ISO)).upper().replace("-", "_")
            iso.add_file(str(p), iso_path=rel + ";1", joliet_path="/" + str(p.relative_to(ISO)), rr_name=p.name)
    iso.add_eltorito("/BOOT/ISOLINUX.BIN;1", boot_load_size=4, boot_info_table=True)
    try: iso.add_isohybrid()
    except Exception as e: print("isohybrid skipped:", e)
    iso.write(str(OUT)); iso.close()
    print("ISO DONE:", OUT, OUT.stat().st_size // 1048576, "MB")

if __name__ == "__main__":
    extract_debs(); build_rootfs(); build_iso(build_initramfs())
