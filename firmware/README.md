# Firmware artifacts

The firmware is not tracked in git. Keep the vendor archive and the extracted
tree outside version control; this directory records provenance only.

Expected local layout, relative to the repository root:

```text
smart_hv_2.7.4.hup
smart_hv_2.7.4/
  boot/
    boot.bin
    sama5d27_som1_smart_hv.dtb
    u-boot.bin
    uboot.env
    zImage
  data/
    app/bin/app
    app/bin/app.kuna/            # generated, ignored
    app/bin/app.kuna.incomplete/ # generated, ignored
    app/bin/app.kuna.tar.xz      # generated, ignored
    smart-web/bin/smart-web
  rootfs/
  smart-update
  update.json
  update.sh
```

Verify the artifacts from the repository root:

```bash
sha256sum -c firmware/SHA256SUMS
```

## Notes

- `smart_hv_2.7.4.hup` — vendor update archive for version 2.7.4.
- `data/app/bin/app` — ARM 32-bit Qt 5 HTTP service binary on TCP/80.
- `data/app/bin/app.kuna/` — Kuna decompilation used by the analysis.
- `data/app/bin/app.kuna.incomplete/` — leftover partial export; not used.
- `data/app/bin/app.kuna.tar.xz` — generated decompilation archive.
- `data/smart-web/bin/smart-web` — separate DBus client, not the HTTP server.

The checksums identify the exact artifacts the notes were derived from. They do
not imply the files are safe to redistribute.
