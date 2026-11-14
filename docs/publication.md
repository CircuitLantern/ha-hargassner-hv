<!-- SPDX-License-Identifier: Apache-2.0 -->

# Public release and Marketplace checklist

Target: **CircuitLantern/ha-hargassner-hv**. Publication is not automatic, and
this preparation does not imply Marketplace catalog acceptance.

## Repository and privacy

- [ ] Confirm the GitHub account and repository are available and controlled by
  the intended maintainer. Review the profile for unintended identity links.
- [ ] Review the sanitized historical import and final preparation
  diff. Preserve technical docs, substantive messages, and third-party notices.
- [ ] Confirm public author/committer metadata uses the approved pseudonym and
  GitHub no-reply address; do not upload private sanitization maps or audit logs.
- [ ] Audit tracked content and all history for private names, contact details,
  home paths, identifiers, credentials, and captures. Audit release archives and
  workflow logs too. Recheck after every new commit or artifact build.
- [ ] Create the public repository only after approval, with issues, description,
  and relevant topics enabled. Consider GitHub private vulnerability reporting.

The original development repository is not the publication remote. History
rewriting belongs only in the isolated publication copy. Rewritten hashes and
publicly searchable source can still permit correlation; pseudonymity is not a
guarantee of anonymity.

## Packaging and validation

- [x] Pass SDK tests on Python 3.11 and the supported HA Python version.
- [x] Build and inspect the independent SDK wheel and sdist: SDK only, no HA
  dependencies or adapter code, sole CLI `hvctl`, standard licence/provenance.
- [x] Pass actual-HA tests without a separately installed SDK, including isolated
  component import, synthetic loopback reads/writes, and existing entry migration.
- [ ] Validate manual/Marketplace upgrade from the former external-SDK 0.3.0
  install, retaining config and entity identities. Never automatically uninstall
  an old globally installed SDK.
- [x] Pass lint, SDK typing, licensing/header checks, documentation links, and
  bundle integrity checks. Only one SDK source tree may exist.
- [ ] Pass hassfest and HACS integration validation with no ignored checks.
  GitHub repository metadata validation needs the actual public repository.
- [ ] Review validator dependencies. Workflow wrappers are pinned to reviewed
  commits, but upstream HACS/hassfest Docker images remain upstream-managed.
- [ ] Confirm current Marketplace requirements and that the chosen domain does
  not collide with another catalog/core integration.
- [ ] Complete final provenance and rights review. Supplied artwork has public
  redistribution permission but remains distinct from project Apache licensing.

Home Assistant 2026.11.1 supplies aiohttp 3.14.5 and yarl 1.25.1, both within the
SDK's declared compatible ranges. Additional dependencies, if ever needed, must
use ordinary manifest requirements; no custom installer is permitted.

### Local preparation evidence

The final local SDK runs passed **241 tests, 642 subtests**, with the live test
skipped, on both Python **3.11.18** and **3.14.8**. The separate Home Assistant
**2026.11.1** environment passed **180 tests**, with only upstream deprecation
warnings and no standalone SDK installed. Lint and SDK typing passed; local
links, anchors, and code fences passed in 17 documents, and GitHub YAML parsed.

The moved SDK source bytes match the historical implementation exactly; adapter
logic matches after normalizing import relocation/order. The private source-only
0.4.0 bundle passed full checksum coverage, source identity, licence presence,
and wheel/cache/build-metadata exclusion checks. This is not a public release.

The historical import retained all 43 commits, their complete messages and file
trees, chronology, and topology with approved public attribution. Original
identity-term checks passed across publication candidates, all stored Git
objects, local Git metadata, and bundle members; PNGs contain no text or EXIF
metadata. Final human privacy/provenance approval remains required.

Local hassfest could not run because the Docker daemon socket was unavailable;
no daemon was started. HACS repository-metadata validation and GitHub workflows
have not run against the public repository. These gates remain unchecked.

## Release and inclusion

- [x] Approve integration **0.4.0** for the packaging conversion. The manifest
  and adapter version are aligned; the unchanged SDK remains 0.1.0.
- [ ] Ensure release tag matches the manifest version. Publish a full GitHub
  release with privacy-safe release notes; a tag alone is insufficient.
- [ ] Verify installation from the release using standard repository layout,
  not private bundle wheels or `zip_release`.
- [ ] Add an interim Marketplace custom-repository link to the README after the
  public repository is available. This is not shared-catalog inclusion.
- [ ] Submit the integration entry to `hacs/default` using its current template,
  with release and successful HACS/hassfest workflow links.
- [ ] Address review feedback and wait for acceptance and scheduled scanning.
  New submissions can take months; do not claim listing before acceptance.

Retain explicit hardware-validation limits. No live boiler requests, intentional
writes, deployment, remote push, release publication, or catalog submission are
authorized by running tests or building a private bundle. Each needs its own
approval. This repository contains no automatic publication/deployment workflow.

## References

- [Publishing integrations](https://contributors.home-assistant.io/marketplace/guides/integrations/)
- [Repository structure](https://contributors.home-assistant.io/marketplace/reference/repository-structure/)
- [Validation](https://contributors.home-assistant.io/marketplace/reference/validation/)
- [Catalog submission](https://contributors.home-assistant.io/marketplace/guides/catalog/)
