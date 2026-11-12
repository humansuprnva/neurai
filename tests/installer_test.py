"""QEMU verification for the NeurAI universal installer, all SeaBIOS.

Boots the NEURAI_TEST_SERIAL=1 installer ISO (built by build_installer.py
to .build/neurai-installer-1.0.0-amd64-serial.iso), delivers the payload
on a separate ext4 disk image (mke2fs -d, whole-disk filesystem, payload
found by the installer's auto-scan - no --payload flag), runs
`neurai-install --auto ... --serial-test --yes` through the NeurAI
shell's `!` escape, waits for the success line, powers off, and boots
the installed system from hard disk (no cdrom).

Scenarios:
  a) single, 20 GiB, role interface -> boot check, teach a codeword,
     power off, boot again, codeword still in memory.json (persistence)
  b) split, 20 GiB system + 8 GiB data, role memory -> boot check,
     persistence, node.json + neurai-release both say role memory
     (NOTE: the task text said a 12 GiB system disk, but the settled
     sizing - root 5120 + integrity 8192 for role memory + ESP 512 +
     biosboot 1 + swap 2048 = 15873 MiB - cannot fit in 12 GiB, so the
     system disk here is 20 GiB; the installer math is unchanged.)
  c) single, 20 GiB, role inference -> boot check, node.json role only

Echo safety: serial ttys echo typed input, so persistence greps use a
regex (`instcode[a-z]*`) whose literal echo cannot match the wait
string; role checks wait for JSON/release content the command line
itself does not contain.
"""
import os
import subprocess
import sys
import threading
import time

BASE = os.path.expanduser("~/workspace/neurai-os")
QR = f"{BASE}/.build/qemu-root"
TDIR = f"{BASE}/.build/installertest"
ISO = f"{BASE}/.build/neurai-installer-1.0.0-amd64-serial.iso"
PAYLOAD_IMG = f"{TDIR}/payload.img"
os.makedirs(TDIR, exist_ok=True)

GIB = 1024 ** 3


def make_payload_image():
    manifest = f"{BASE}/dist/neurai-payload/manifest.json"
    if not os.path.exists(manifest):
        print("payload missing - run build_installer.py first")
        sys.exit(2)
    if os.path.exists(PAYLOAD_IMG) and os.path.getmtime(PAYLOAD_IMG) > os.path.getmtime(manifest):
        print("payload image ready (cached)")
        return
    stage = f"{TDIR}/payload-stage"
    subprocess.run(["rm", "-rf", stage], check=True)
    os.makedirs(stage)
    subprocess.run(["cp", "-r", f"{BASE}/dist/neurai-payload", f"{stage}/neurai-payload"], check=True)
    total = sum(os.path.getsize(os.path.join(dp, f))
                for dp, _, fs in os.walk(f"{stage}/neurai-payload") for f in fs)
    size_mib = (total // (1024 * 1024) + 512 + 255) // 256 * 256
    with open(PAYLOAD_IMG, "wb") as f:
        f.truncate(size_mib * 1024 * 1024)
    blocks = size_mib * 1024 * 1024 // 4096
    subprocess.run(["mke2fs", "-q", "-t", "ext4", "-F", "-b", "4096", "-d", stage,
                    "-O", "^metadata_csum,^metadata_csum_seed",
                    "-L", "PAYLOAD", PAYLOAD_IMG, str(blocks)], check=True)
    print(f"payload image ready: {size_mib} MiB ext4 with neurai-payload/ ({total // 1048576} MiB of files)")


def sparse(path, gib):
    with open(path, "wb") as f:
        f.truncate(gib * GIB)
    return path


def boot(tag, drives, cdrom=False):
    """drives: list of (file, ide_index, snapshot). Returns the
    (proc, wait_for, send, out) quartet in disk_test.py style."""
    loader = f"{QR}/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2"
    cmd = [loader, "--library-path", f"{QR}/usr/lib/x86_64-linux-gnu:{QR}/lib/x86_64-linux-gnu",
           f"{QR}/usr/bin/qemu-system-x86_64", "-m", "4096", "-nographic", "-no-reboot",
           "-L", f"{QR}/usr/share/qemu", "-L", f"{QR}/usr/share/seabios",
           "-nic", "none", "-serial", "stdio", "-monitor", "none"]
    if cdrom:
        cmd += ["-cdrom", ISO, "-boot", "d"]
    else:
        cmd += ["-boot", "c"]
    for path, idx, snap in drives:
        spec = f"file={path},format=raw,if=ide,index={idx}"
        if snap:
            spec += ",snapshot=on"
        cmd += ["-drive", spec]
    env = dict(os.environ)
    env["QEMU_MODULE_DIR"] = f"{QR}/usr/lib/x86_64-linux-gnu/qemu"
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, env=env)
    out = []
    logf = open(f"{TDIR}/{tag}.log", "w")

    def reader():
        while True:
            b = p.stdout.readline()
            if not b:
                break
            s = b.decode(errors="replace")
            out.append(s)
            logf.write(s)
            logf.flush()

    threading.Thread(target=reader, daemon=True).start()

    def wait_for(s, timeout=240, fail_markers=()):
        t0 = time.time()
        while time.time() - t0 < timeout:
            blob = "".join(out)
            if s in blob:
                return True
            for fm in fail_markers:
                if fm in blob:
                    print(f"  [wait] fail marker seen: {fm}")
                    return False
            if p.poll() is not None:
                return s in "".join(out)
            time.sleep(1)
        return s in "".join(out)

    def send(s):
        try:
            p.stdin.write((s + "\n").encode())
            p.stdin.flush()
        except (BrokenPipeError, ValueError):
            pass

    return p, wait_for, send, out


def poweroff(p, send):
    send("!poweroff -f")
    try:
        p.wait(timeout=90)
    except Exception:
        try:
            p.terminate()
        except Exception:
            pass


def run_install(tag, drives, cmd_args):
    p, wait_for, send, out = boot(tag, drives, cdrom=True)
    ok_prompt = wait_for("Just talk to me in plain language", 420)
    print(f"  live_prompt: {ok_prompt}")
    if not ok_prompt:
        poweroff(p, send)
        return False
    send(f"!neurai-install {cmd_args}")
    ok = wait_for("\nINSTALL COMPLETE", 2400,
                  fail_markers=("[installer] ERROR",))
    print(f"  install_complete: {ok}")
    poweroff(p, send)
    return ok


def boot_installed_once(tag, drives, checks):
    """Boot the installed system; checks: list of (send_cmd, wait_str,
    timeout, label). Returns dict label->bool plus loader/brain flags."""
    p, wait_for, send, out = boot(tag, drives, cdrom=False)
    res = {}
    res["loader"] = wait_for("switching to installed system", 300)
    print(f"  loader_switch_root: {res['loader']}")
    res["brain"] = wait_for("brain: llama.cpp", 300)
    print(f"  briefing_brain: {res['brain']}")
    for cmd, want, timeout, label in checks:
        if cmd:
            send(cmd)
        res[label] = wait_for(want, timeout)
        print(f"  {label}: {res[label]}")
    poweroff(p, send)
    return res


def scenario_a():
    print("=== SCENARIO a: single 20 GiB, role interface ===")
    tgt = sparse(f"{TDIR}/a-target.img", 20)
    drives = [(tgt, 0, False), (PAYLOAD_IMG, 1, True)]
    ok_install = run_install("a-install", drives,
                             "--auto --layout single --system-disk /dev/sda "
                             "--role interface --serial-test --yes")
    if not ok_install:
        return False
    r1 = boot_installed_once("a-boot1", [(tgt, 0, False)], [
        ("remember that the installer test codeword is instcodealpha",
         "Remembered", 600, "remember_saved"),
    ])
    r2 = boot_installed_once("a-boot2", [(tgt, 0, False)], [
        ("!grep -o 'instcode[a-z]*' /neurai/data/home/memory.json",
         "instcodealpha", 180, "persistence"),
    ])
    return all([r1.get("loader"), r1.get("brain"), r1.get("remember_saved"),
                r2.get("brain"), r2.get("persistence")])


def scenario_b():
    print("=== SCENARIO b: split 20 GiB + 8 GiB, role memory ===")
    sysd = sparse(f"{TDIR}/b-system.img", 20)
    datd = sparse(f"{TDIR}/b-data.img", 8)
    drives = [(sysd, 0, False), (datd, 1, False), (PAYLOAD_IMG, 3, True)]
    ok_install = run_install("b-install", drives,
                             "--auto --layout split --system-disk /dev/sda "
                             "--data-disk /dev/sdb --role memory --serial-test --yes")
    if not ok_install:
        return False
    both = [(sysd, 0, False), (datd, 1, False)]
    r1 = boot_installed_once("b-boot1", both, [
        ("remember that the installer test codeword is instcodebravo",
         "Remembered", 600, "remember_saved"),
        ("!cat /neurai/data/home/node.json", '"role": "memory"', 180, "node_role"),
        ("!cat /etc/neurai-release", "role=memory", 180, "release_role"),
    ])
    r2 = boot_installed_once("b-boot2", both, [
        ("!grep -o 'instcode[a-z]*' /neurai/data/home/memory.json",
         "instcodebravo", 180, "persistence"),
    ])
    return all([r1.get("loader"), r1.get("brain"), r1.get("remember_saved"),
                r1.get("node_role"), r1.get("release_role"),
                r2.get("brain"), r2.get("persistence")])


def scenario_c():
    print("=== SCENARIO c: single 20 GiB, role inference ===")
    tgt = sparse(f"{TDIR}/c-target.img", 20)
    drives = [(tgt, 0, False), (PAYLOAD_IMG, 1, True)]
    ok_install = run_install("c-install", drives,
                             "--auto --layout single --system-disk /dev/sda "
                             "--role inference --serial-test --yes")
    if not ok_install:
        return False
    r1 = boot_installed_once("c-boot1", [(tgt, 0, False)], [
        ("!cat /neurai/data/home/node.json", '"role": "inference"', 180, "node_role"),
    ])
    return all([r1.get("loader"), r1.get("brain"), r1.get("node_role")])


def main():
    if not os.path.exists(ISO):
        print(f"installer ISO missing: {ISO} - run build_installer.py first")
        print("INSTALL_TEST OVERALL: FAIL")
        return
    make_payload_image()
    only = sys.argv[1] if len(sys.argv) > 1 else None
    results = {}
    for name, fn in (("a", scenario_a), ("b", scenario_b), ("c", scenario_c)):
        if only and name != only:
            continue
        try:
            results[name] = fn()
        except Exception as e:
            print(f"scenario {name} raised: {e!r}")
            results[name] = False
        print(f"INSTALL_TEST {name}: {'PASS' if results[name] else 'FAIL'}")
    overall = all(results.values()) and len(results) > 0
    print("INSTALL_TEST OVERALL:", "PASS" if overall else "FAIL")


if __name__ == "__main__":
    main()
