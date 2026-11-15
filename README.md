<!-- SPDX-License-Identifier: Apache-2.0 -->

# Hargassner Smart HV for Home Assistant

An unofficial Home Assistant integration for Hargassner Smart HV wood-log boilers,
using the controller's local HTTP API.
Read operational telemetry and, with explicit per-setting permission, control
heating-circuit modes, persistent room targets, and comfort programs.

**No cloud service or separate SDK installation is required.** The SDK sources
ship inside the component. The minimum tested Home Assistant version is
**2026.11.1**.

This project is not affiliated with or endorsed by the manufacturer. It is not
a safety-certified heating controller.

## Home Assistant integration

- Operational sensors for reviewed temperatures, fan speed, oxygen, and
  calculated indications; optional diagnostics for unclassified channels.
- Circuit 1, 2, and A mode selects and climate controls, where applicable and
  individually opted in.
- Off, Automatic, and four permanent/one-time comfort/reduced presets.
- Persistent target editing under separate numeric write permissions.
- A dedicated full-program action; no arbitrary parameter-write action.
- Read-only setup and polling, with every write disabled by default.

Supported calls were curated to avoid initiating the boiler's **SERVICE mode**.
The integration does not detect or clear SERVICE mode entered by another client.
The controller retains regulation and one-time override expiry; Home Assistant
does not provide a replacement thermostat algorithm or restore old targets.

## Installation

**Publication preparation:** a public release and Marketplace catalog submission
are still pending. This source tree does not imply the integration is already
listed in Marketplace.

Once the public repository and release are available, add
`CircuitLantern/ha-hargassner-hv` as an **Integration** custom repository in
Marketplace (HACS on older Home Assistant versions). Catalog acceptance is a
separate review; it will make the integration discoverable without this step.

For manual development installation, copy the complete
`custom_components/hargassner_hv/` folder, including `hvclient/`, into your Home
Assistant configuration's `custom_components/` directory. Restart Core after
updates, then add **Hargassner Smart HV** under **Settings → Devices & services**.
Enter the bare LAN host and HTTP port. No password is required.

See the [installation and control guide](docs/home-assistant.md) for details,
upgrade instructions, permission opt-ins, and troubleshooting.

### Enable selected write permissions

The integration starts read-only. To opt in to individual controls:

1. Open `http://homeassistant.local:8123/config/integrations/integration/hargassner_hv`
   in your browser. Replace `http://homeassistant.local:8123` with the address
   you normally use to open Home Assistant. You can also navigate through **Settings →
   Devices & services → Hargassner Smart HV**.
2. Click the **gear (Configure)** icon for your **Hargassner Smart HV** device.
3. Under **Allow writes to these settings**, add only the settings you want
   Home Assistant to control.
4. Check **I understand the operational risk of enabling selected writes**
   if enabling any writes. Select the optional diagnostic checkboxes as desired;
   these enable additional diagnostics, not write permissions.
5. Submit the form to save your choices.

Enabling writes can affect heating operation. Permissions are per setting:
allowing circuit-mode changes does not also allow persistent target edits or
program changes. Leave settings you do not need unselected.

## Safety and compatibility

- Keep the boiler HTTP interface on a trusted, isolated LAN; do not expose it
  to the internet. The device protocol is plain HTTP.
- Start read-only. Enabling controls can affect heating operation.
- Writes are individually reviewed and verified, but cross-setting checks are
  not atomic and changes from other clients can race them.
- Lost write responses are never replayed; there is no automatic rollback.
- No implicit global scheduling, circuit-type, limit, or boiler-wide mode edits.

Protocol analysis is based on firmware **2.7.4**, with separate SDK live
reads/previews on firmware reporting **smart_hv_3.4.0**. An initial user climate
smoke test was successful, but detailed physical effects, target persistence
after expiry, reboot persistence, and broad firmware compatibility remain
unverified. See [validation boundaries](docs/home-assistant.md#testing-and-validation-boundary).

## Documentation

| Guide | Contents |
|---|---|
| [Home Assistant](docs/home-assistant.md) | Installation, entities, permissions, programs, and validation limits |
| [SDK reference](docs/library.md) | Independent Python API, write policy, and `hvctl` |
| [Channel guide](docs/channel-guide.md) | Reviewed channel meanings, units, and validity |
| [Parameter guide](docs/parameter-guide.md) | English meanings and manual crosswalk |
| [HTTP API](docs/boiler-http-api.md) | Protocol findings and endpoint contracts |
| [Development overview](docs/development-overview.md) | Preserved research context, tools, and live-test precautions |
| [Development plan](docs/client-development-plan.md) | Architecture, safety rationale, and historical decisions |
| [Climate plan](docs/climate-development-plan.md) | Mode/preset semantics and acceptance coverage |
| [Publication checklist](docs/publication.md) | Validation, release, and catalog gates |

## Research tools

Research helpers, firmware provenance records, and SDK CLI instructions are
retained in the [development overview](docs/development-overview.md). They are
not dependencies of the Home Assistant integration. Vendor firmware, manuals,
private captures, and generated decompilation are not distributed.

## Development

The canonical SDK source is `custom_components/hargassner_hv/hvclient/`. The
standalone `hargassner-hvclient` distribution builds that same source as the
`hvclient` module, independently of Home Assistant, with `hvctl` as its sole CLI.
There is no duplicate SDK tree and no requirement to publish it to PyPI.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[test]'
HVCLIENT_LIVE=0 .venv/bin/python -m pytest -q
```

For actual-HA tests, use the separate environment in the
[integration guide](docs/home-assistant.md#testing-and-validation-boundary).
CI uses synthetic data and loopback devices, never a real boiler.

## Licence and provenance

Project-maintained code and documentation use **Apache-2.0**. See
[`LICENSE`](LICENSE) and [`LICENSING.md`](LICENSING.md) for full terms,
agent-generated development provenance, and third-party exclusions. Supplied
artwork may be redistributed but retains separate underlying rights.

Technical documentation and substantive commit messages were preserved during a
privacy-related historical import. Public attribution uses CircuitLantern;
private maintainer and installation identifiers are not publication material.
