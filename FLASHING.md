# Flashing ACE2-Open

> ## ⚠️ READ THIS FIRST — YOU CAN DESTROY YOUR HARDWARE
>
> This flashes **modified firmware** onto your ACE 2 Pro. It is not an Anycubic release, it is
> not tested by anyone but us, and it has been run on **exactly one device**.
>
> **You are entirely on your own.** There is no warranty, no support obligation, and no
> guarantee any of this works on your hardware. Flashing will almost certainly void your
> warranty. If the device ends up unusable, that is your risk and yours alone.
>
> Specific ways this can go wrong:
>
> * **A patched application that faults before its IAP task starts leaves no RS-485 receiver.**
>   Recovery then requires **SWD/JTAG on the PCB** — opening the unit and attaching a probe.
> * **Interrupting the commit** (power loss during the copy from staging to the application
>   region) can leave a half-written application.
> * **Your firmware may not be V1.1.31.** Every address in this project is version-specific.
>   Applying these patches to a different build will produce garbage, and the build script
>   refuses to run unless the base image matches exactly — do not override that check.
> * **Clone silicon.** Our unit reports a non-genuine reader (`VersionReg 0x18`). Yours may
>   differ in ways we cannot predict.
>
> If you are not comfortable recovering a bricked device with a hardware programmer, **do not
> flash this.** The research documents in [docs/](docs/) are useful on their own and carry no
> risk.

---

## What reduces the risk

The design deliberately minimises exposure:

- **All patches hook a single command handler** (`GET_FILAMENT_INFO` / `FILAMENT_IDENTIFY`),
  on paths that are either error exits or unreachable. Nothing runs during boot, during a print,
  or during any motion. A bug in our code cannot prevent the device from starting.
- **The bootloader is never written.** OTA only writes the staging area and the application
  region, so the bootloader — which implements the protocol and can re-flash you — stays intact.
- **A failed transfer is harmless.** Nothing overwrites the running application until the commit.

The realistic worst case is "the device boots and behaves like stock", not "the device is dead".

## You do not need a password

The `.swu` password protects Anycubic's distribution ZIP and is only needed to **extract** a base
image. The flasher takes a raw `.bin`, so no password is used at flash time. This repository ships
**no Anycubic firmware and no passwords**.

You supply the base image yourself. Verify it is the right one:

```
71592 bytes, md5 79fb22e7914bae1dc75ac91b30739c19
```

## Build

With an ARM toolchain (`arm-none-eabi-as`, `ld`, `objcopy` — Klipper hosts usually have these):

```bash
python3 firmware/build_patch.py --base ACE2_V1.1.31_20260306.bin --out ACE2-Open.bin
```

Without a toolchain:

```bash
python3 firmware/apply_patch.py --base ACE2_V1.1.31_20260306.bin --out ACE2-Open.bin
```

Both verify the base image, insert the stubs **before the 8-byte IAP magic trailer**, apply the
hooks, and set the version string to `V1.1.3O`.

## Flash

1. **Stop whatever owns the serial port.** On a Klipper host: `sudo systemctl stop klipper`.
2. **Power-cycle the ACE.** This clears the OTA state byte. Skipping this is the single most
   common cause of a flash that reports success and does nothing — see
   [docs/02-ota-and-iap.md](docs/02-ota-and-iap.md).
3. Flash with hakimio's updater, patched with `tools/ota_updater.patch`:

   ```bash
   python3 ace2-ota-update.py /dev/ttyACM0 ACE2-Open.bin --version 1.1.31 --force
   ```

4. **Verify what is actually running.** Do not trust the tool's success message:

   ```
   GET_INFO -> version = V1.1.3O
   ```

   If it still says `V1.1.31`, the commit did not happen — power-cycle and try again.

   The updater will print a spurious "timed out waiting for expected version" at the end, because
   it compares against the string passed to `--version`. That is cosmetic; the reported version is
   what matters.

5. Restart Klipper.

## Recovery

Re-flash a stock image the same way. The bootloader speaks the protocol — in IAP mode `GET_INFO`
returns `boot_version = V1.0.2` and `status = upgrading`, and it will accept a new image.

**Critically: apply `tools/ota_updater.patch` before you need it.** The unpatched updater filters
out the bootloader's replies and will tell you the device is not connected, which looks exactly
like a brick and is not one. We hit this twice.

Keep a stock image on the machine you flash from.
