<!-- SPDX-License-Identifier: MPL-2.0 -->

# Hargassner Smart HV

Custom Home Assistant integration using the separately installed `hvclient`
library. Configuration and normal polling only read. Number/select controls
and the dedicated program action require explicit per-setting write opt-in.
The supported calls were curated to avoid initiating the boiler's SERVICE mode.

The repository's `docs/home-assistant.md` describes installation, entity
mapping, program actions, limitations, and the development test environment.
The pinned library must be published before normal dependency installation
works; the reserved manifest URLs and empty codeowner list are release
placeholders, not public project information.

## Licence

This Source Code Form is subject to the terms of the Mozilla Public
License, v. 2.0. If a copy of the MPL was not distributed with this
file, You can obtain one at https://mozilla.org/MPL/2.0/.

The repository includes `LICENSE` and `LICENSING.md` for licence scope and
development provenance. Vendor material and third-party dependencies retain
their own rights; no vendor endorsement or safety certification is implied.
