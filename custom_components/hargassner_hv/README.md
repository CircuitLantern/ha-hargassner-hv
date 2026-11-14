<!-- SPDX-License-Identifier: Apache-2.0 -->

# Hargassner Smart HV

Custom Home Assistant integration using the separately installed `hvclient`
library. Configuration and normal polling only read. Climate/number/select controls
and the dedicated program action require explicit per-setting write opt-in.
The supported calls were curated to avoid initiating the boiler's SERVICE mode.

Reviewed operating temperatures, fan speed, oxygen, and calculated indications
are normal Sensors and do not require the advanced diagnostic option. Setting
readbacks are Diagnostics; opted-in writable numbers/selects are Configuration.
Applicable circuits with mode writes enabled also have normal climate controls.
Their four presets preserve the controller's permanent/one-time distinction;
the temperature dial edits persistent comfort/reduced settings, including in
one-time presets. Expiry stays controller-owned and does not restore old targets.
Unclassified channels remain optional diagnostics. Upgrades retain entity IDs,
custom names, and explicit user enable/disable choices.

The repository's `docs/home-assistant.md` describes installation, entity
mapping, program actions, limitations, and the development test environment.
The pinned library must be published before normal dependency installation
works; the reserved manifest URLs and empty codeowner list are release
placeholders, not public project information.

## Licence

Project-maintained code and documentation are licensed under the Apache License,
Version 2.0 (`Apache-2.0`): https://www.apache.org/licenses/LICENSE-2.0.
Software is provided on an "AS IS" basis, without warranties or conditions of
any kind; the full licence governs permissions and limitations.

The repository includes `LICENSE` and `LICENSING.md` for licence scope and
development provenance. Vendor material and third-party dependencies retain
their own rights; no vendor endorsement or safety certification is implied.
