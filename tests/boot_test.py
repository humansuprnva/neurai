import subprocess, time, sys, os, threading
QR = os.path.expanduser("~/workspace/neurai-os/.build/qemu-root")
ISO = os.path.expanduser("~/workspace/neurai-os/dist/neurai-os-2.0.0-amd64.iso")
loader = f"{QR}/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2"
cmd = [loader, "--library-path", f"{QR}/usr/lib/x86_64-linux-gnu:{QR}/lib/x86_64-linux-gnu",
       f"{QR}/usr/bin/qemu-system-x86_64", "-m", "4096", "-nographic", "-no-reboot",
       "-L", f"{QR}/usr/share/qemu", "-L", f"{QR}/usr/share/seabios", "-nic", "none", "-cdrom", ISO, "-boot", "d", "-serial", "stdio", "-monitor", "none"]
env = dict(os.environ); env["QEMU_MODULE_DIR"] = f"{QR}/usr/lib/x86_64-linux-gnu/qemu"
p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
out = []
LOGF = open(os.path.expanduser("~/workspace/neurai-os/.build/boot-iso.log"), "w")
def reader():
    while True:
        b = p.stdout.readline()
        if not b: break
        s = b.decode(errors="replace")
        out.append(s)
        LOGF.write(s); LOGF.flush()
t = threading.Thread(target=reader, daemon=True); t.start()
def wait_for(s, timeout=120):
    start = time.time()
    while time.time() - start < timeout:
        if s in "".join(out): return True
        time.sleep(0.5)
    return False
ok_banner = wait_for("NeurAI Integrated OS", 100)
print("banner_seen:", ok_banner)
if ok_banner:
    ok_prompt = wait_for("Just talk to me in plain language", 180)
    print("prompt_seen:", ok_prompt)
    p.stdin.write(b"status\n"); p.stdin.flush()
    ok_status = wait_for("healthy", 300)
    print("status_answer_seen:", ok_status)
    p.stdin.write(b"calculate 6 * 7\n"); p.stdin.flush()
    ok_calc = wait_for("= 42", 300)
    print("calc_answer_seen:", ok_calc)
else:
    ok_status = ok_calc = False
try: p.terminate()
except Exception: pass
log = "".join(out)
open(os.path.expanduser("~/workspace/neurai-os/.build/boot-iso.log"), "w").write(log)
print("---- last 40 lines ----")
print("\n".join(log.splitlines()[-40:]))
print("BOOT_TEST:", "PASS" if (ok_banner and ok_status and ok_calc) else "PARTIAL/FAIL")
