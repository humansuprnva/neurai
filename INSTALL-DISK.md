# Installing NeurAI on the Lenovo V14 ADA (two-drive layout)

You get two disk images. Written to the two SSDs, they implement the
recorded partition plan exactly — nothing is partitioned by hand.

| Image | Goes on | Contents |
|---|---|---|
| `neurai-system-2.0.0.img` (~10 GB) | internal SSD #1 (the smaller/original one) | GPT: BIOS-boot + ESP 512 MB + `NEURAI_ROOT` 5 GB + `NEURAI_INTEGRITY` 2 GB (ADR-001 gated store) + swap 2 GB |
| `neurai-data-2.0.0.img` (~4 GB) | the 500 GB SSD | GPT: single `NEURAI_DATA` volume — the Qwen2.5-1.5B model, NeurAI home (memory, tasks, goals), artifacts, spawns, logs |

The images are smaller than the drives on purpose: everything works as
written; the unused space can be reclaimed later by growing the data
volume. Nothing in the current build needs it.

Verified before shipping: QEMU UEFI boot from these exact images →
NeurAI up with its brain → taught a fact → powered off → rebooted →
fact still in memory on the data volume (DISK_TEST: PASS).

## What you need

- The laptop, both SSDs installed. **Everything on both drives will be erased** — they're already a clean slate by your designation.
- A USB stick (16 GB+) holding the two `.img.gz` files.
- The existing NeurAI live USB (the v2 ISO) as the tool environment.

## Steps

1. **BIOS setup (F2 at power-on):** disable **Secure Boot** (the GRUB in
   the images is unsigned). Save and exit.
2. **Boot the live USB** (F12 boot menu → USB). Wait for the NeurAI
   prompt. The live system runs entirely from RAM.
3. **Insert the stick with the images.** In the NeurAI shell, use the
   `!` prefix to run system commands. Mount the stick (it will usually
   be `/dev/sdc1` — check `!cat /proc/partitions` and pick by size):

   ```
   !mkdir -p /mnt/usb; mount /dev/sdc1 /mnt/usb; ls -la /mnt/usb
   ```

   You should see `neurai-system-2.0.0.img.gz` and `neurai-data-2.0.0.img.gz`.
4. **Identify the internal drives** (do not skip this):

   ```
   !cat /proc/partitions
   ```

   The 500 GB drive is obvious by size (~488 million 1K-blocks). The
   other internal drive takes the system image. USB devices will be the
   remaining entries — the live USB and the image stick. Write down
   which `/dev/sdX` is which before continuing.
5. **Write the system image** to internal drive #1 (example uses `sda`;
   substitute the device YOU identified):

   ```
   !gzip -dc /mnt/usb/neurai-system-2.0.0.img.gz | dd of=/dev/sda bs=4M
   ```

   This takes a few minutes and prints nothing until it finishes.
6. **Write the data image** to the 500 GB drive (example `sdb`):

   ```
   !gzip -dc /mnt/usb/neurai-data-2.0.0.img.gz | dd of=/dev/sdb bs=4M
   ```
7. **Sync, remove both USB sticks, reboot:**

   ```
   !sync; poweroff
   ```

   Power on. The machine boots from its own SSD: GRUB → loader →
   NeurAI greets you and already knows its brain is present. Try it:
   `remember that today is install day`, then reboot and ask
   `status` — the fact survives, because memory now lives on the data
   volume, not in RAM.

## Notes and gotchas

- If it doesn't boot from disk: re-enter the F12 menu and pick the
  internal SSD explicitly; check Secure Boot is still off. The GRUB on
  the ESP registers via the standard fallback path (`EFI/BOOT/BOOTX64.EFI`),
  so no firmware boot entry is required.
- A rescue entry is in the GRUB menu ("rescue shell") — it drops the
  loader to a shell before mounting, for diagnosis.
- The live USB remains your recovery tool: from it you can re-write
  either image at any time. The data volume is a plain ext4 filesystem;
  any Linux can read NeurAI's memory files off it.
- The integrity volume is real but the current core doesn't write to
  it yet — that's Phase 4 (factoring the integrity machinery out of
  the single-file core), per ADR-001's recorded implementation gap.
