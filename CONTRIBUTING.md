<!-- SPDX-License-Identifier: Apache-2.0 -->

# Contributing

Keep protocol and validation logic in `custom_components/hargassner_hv/hvclient/`.
This is the single SDK source, also built as the independent `hvclient` package.
Keep Home Assistant imports outside it. Adapter code uses relative SDK imports;
SDK tests use the standalone package, and HA tests use the bundled namespace.

Run the offline SDK and actual-HA tests described in the
[integration guide](docs/home-assistant.md#testing-and-validation-boundary).
Changes must preserve default-deny writes, reviewed individual opt-ins, no
SERVICE-initiating calls, and no write replay or automatic rollback. Do not add
arbitrary parameter writes, a replacement thermostat, custom datastore, or daemon.

Do not submit firmware, manuals, private captures, generated decompilation,
credentials, or identifying installation information. Use synthetic examples.
Describe copied/adapted sources, applicable licences, and material AI generation.
Preserve required attribution; do not assume generated code is copyrightable.

Technical documentation and meaningful commit messages are valuable project
knowledge. Update them without erasing historical evidence or presenting offline
checks as hardware validation. Hardware tests require explicit, scoped approval.

The public identity CircuitLantern is intentionally public. Other personal or
installation identifiers are not; inspect diffs, Git metadata, and assets before
submitting. See [licensing and privacy](LICENSING.md).
