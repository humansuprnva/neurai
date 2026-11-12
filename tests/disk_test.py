"""QEMU test for the INSTALLED NeurAI system (dist/*.img).
Boots the system+data images under UEFI (OVMF) with a test-variant ESP
(identical images except grub.cfg uses the serial console), verifies:
loader switch_root, data+integrity mounts, brain detection, a fact
taught in boot 1 still present in memory.json in boot 2 (persistence).
"""
import subprocess, time, sys, os, threading, shutil, struct

BASE = os.path.expanduser("~/workspace/neurai-os")
QR = f"{BASE}/.build/qemu-root"
TDIR = f"{BASE}/.build/disktest"
os.makedirs(TDIR, exist_ok=True)
SYSIMG = f"{TDIR}/sys.img"; DATAIMG = f"{TDIR}/data.img"

def make_test_images():
    if os.path.exists(SYSIMG): return
    subprocess.run(["cp", "--sparse=always", f"{BASE}/dist/neurai-system-2.0.0.img", SYSIMG], check=True)
    subprocess.run(["cp", "--sparse=always", f"{BASE}/dist/neurai-data-2.0.0.img", DATAIMG], check=True)
    # test-variant ESP: same files, grub.cfg with serial console
    sys.path.insert(0, BASE)
    import build_disk as bd
    esp = bd.Fat32(512 * bd.MIB // bd.SECTOR, hidden=4096)
    cfg = (bd.ROOT / "iso/boot/grub/grub-disk.cfg").read_text().replace("console=tty0", "console=tty0 console=ttyS0")
    img, _ = esp.build({
        "EFI/BOOT/BOOTX64.EFI": (bd.WORK / "BOOTX64.EFI").read_bytes(),
        "BOOT/VMLINUZ": (bd.B / "isoroot/boot/vmlinuz").read_bytes(),
        "BOOT/INITRD.GZ": (bd.WORK / "initrd.gz").read_bytes(),
        "BOOT/GRUB/GRUB.CFG": cfg.encode(),
    })
    with open(SYSIMG, "r+b") as f:
        f.seek(4096 * 512); f.write(img)
    print("test images ready")

def boot():
    loader = f"{QR}/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2"
    vars_copy = f"{TDIR}/OVMF_VARS.fd"
    if not os.path.exists(vars_copy):
        shutil.copy2(f"{QR}/usr/share/OVMF/OVMF_VARS.fd", vars_copy)
    cmd = [loader, "--library-path", f"{QR}/usr/lib/x86_64-linux-gnu:{QR}/lib/x86_64-linux-gnu",
           f"{QR}/usr/bin/qemu-system-x86_64", "-m", "4096", "-nographic", "-no-reboot",
           "-L", f"{QR}/usr/share/qemu", "-L", f"{QR}/usr/share/seabios",
           "-drive", f"if=pflash,format=raw,readonly=on,file={QR}/usr/share/OVMF/OVMF_CODE.fd",
           "-drive", f"if=pflash,format=raw,file={vars_copy}",
           "-drive", f"file={SYSIMG},format=raw,if=ide,index=0",
           "-drive", f"file={DATAIMG},format=raw,if=ide,index=1",
           "-nic", "none", "-serial", "stdio", "-monitor", "none"]
    env = dict(os.environ); env["QEMU_MODULE_DIR"] = f"{QR}/usr/lib/x86_64-linux-gnu/qemu"
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
    out = []
    logf = open(f"{TDIR}/boot.log", "a")
    def reader():
        while True:
            b = p.stdout.readline()
            if not b: break
            s = b.decode(errors="replace"); out.append(s); logf.write(s); logf.flush()
    threading.Thread(target=reader, daemon=True).start()
    def wait_for(s, timeout=240):
        t0 = time.time()
        while time.time() - t0 < timeout:
            if s in "".join(out): return True
            if p.poll() is not None: return False
            time.sleep(1)
        return s in "".join(out)
    def send(s):
        p.stdin.write((s + "\n").encode()); p.stdin.flush()
    return p, wait_for, send, out

def main():
    make_test_images()
    print("=== BOOT 1 ===")
    p, wait_for, send, out = boot()
    ok_loader = wait_for("switching to installed system", 300)
    print("loader_switch_root:", ok_loader)
    ok_brain = wait_for("brain: llama.cpp", 300)
    print("briefing_brain:", ok_brain)
    send("!cat /proc/mounts")
    ok_data = wait_for("/neurai/data", 120)
    ok_int = wait_for("/neurai/integrity", 30)
    print("mounts data/integrity:", ok_data, ok_int)
    send("remember that the disk test codeword is persimmon")
    ok_rem = wait_for("Remembered", 420)
    print("remember_saved:", ok_rem)
    send("!poweroff -f")
    try: p.wait(timeout=90)
    except Exception: p.terminate()
    print("=== BOOT 2 ===")
    p, wait_for, send, out = boot()
    ok_brain2 = wait_for("brain: llama.cpp", 300)
    print("boot2_briefing:", ok_brain2)
    send("!grep -o persimmon /neurai/data/home/memory.json")
    ok_persist = wait_for("persimmon", 120)
    print("persistence:", ok_persist)
    send("!poweroff -f")
    try: p.wait(timeout=90)
    except Exception: p.terminate()
    passed = all([ok_loader, ok_brain, ok_data, ok_int, ok_rem, ok_brain2, ok_persist])
    print("DISK_TEST:", "PASS" if passed else "PARTIAL/FAIL")

if __name__ == "__main__":
    main()
