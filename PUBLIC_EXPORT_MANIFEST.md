# Public Export Manifest

- Source repository: `/root/autodl-tmp/webcraft`
- Source commit: `9a27a833d7967fba54949f34bacb52089024ba80`
- Source subject: `fix: scope W15 contact decomposition to specified suites`
- Export date: 2026-10-20

This repository was exported from the committed source tree only. It intentionally
excludes working-tree changes and the following classes of material: private and
raw corpus inputs, credentials, server and environment directories, generated
reports, release bundles, Node dependencies, and Python bytecode.

`PUBLIC_EXPORT_FILES.sha256` is generated after the export files are finalized.

## Method source addition

- ARBITER source: `c7c4fb7d25c47d77cdfefa5f1acbd076d44b94a5`.
- Destination: `method_code/`; all committed source, tests, scripts, configuration,
  documentation and registrations from that snapshot.
- CP/VB helpers: `scripts/` from the WebCraft source commit above.
- Included research inputs: frozen prior bundle and PF-RAG development selection
  manifests. Generated episode logs and human audit labels remain excluded.
- No W18/W19 files, credentials, private configuration files, or raw/private
  corpus files are included in this addition.
- Publication-only edits are described in `method_code/PUBLIC_SOURCE.md`.
- Offline checks: 115 method/package tests passed; 136 foundation tests passed,
  8 skipped, 179 subtests passed. The full historical analysis test suite was not
  run because its private episode artifacts are intentionally absent.
- Secret scan: no detected real keys or private-key bodies; the two key-shaped
  matches are explicit synthetic credential-parser test fixtures.

The hash inventory now uses Git-tracked files only, excluding itself. This fixes
the initial export's inclusion of untracked pytest cache entries. Regenerate
after staging with `python scripts/update_public_manifest.py`; verify with
`sha256sum --check --quiet PUBLIC_EXPORT_FILES.sha256`.
