# Runner 0.16.2 source assessment

Record ID: runner-0.16.2-source-review. Kind: real source assessment, with candidate execution not-run. Date: 2026-10-15. Visibility: reviewed-public sources; no personal configuration or histories included.

## Decision and authority

The question is whether Runner can provide an Intel macOS control surface for useful existing and newly launched CLI agent sessions. Root authorized source/release inspection and a scoped evidence checkpoint. No installation, candidate execution, source build, permission bypass, credential transfer or persistent native skill/configuration change was authorized by this assessment. Current native tools remain the baseline.

Official source: [Runner v0.16.2](https://github.com/yicheng47/runner/releases/tag/v0.16.2), exact commit `feb2f6804f7f0f8d35e89c2a76e6088b4df3ce43`, MIT at that commit. Newer HEAD was recorded separately, not used to confer release capabilities. Forty-five selected public files were checked against exact Git blob identities and SHA-256; this is not a whole-dependency security audit.

## Attempts and observations

| Operation | Status | Supported result and limit |
| --- | --- | --- |
| Official release/source inspection | completed | Exact pinned source; no code executed |
| Intel macOS installation/build | not-run | No official Intel asset; source build feasibility remains unproven |
| GUI/control/provider trial | not-run | No runtime or usefulness result |
| Independent model/source review | not-performed | No independent acceptance implied |

The macOS asset is `Runner-0.16.2-arm64.dmg` (28,762,384 bytes), with GitHub-reported SHA-256 `3aee6a367a24f4a233267f8a41ff80cf8ddac24d5506d4e4845466e06fefffc2`. The artifact body was not downloaded. The [Rust toolchain](https://github.com/yicheng47/runner/blob/feb2f6804f7f0f8d35e89c2a76e6088b4df3ce43/rust-toolchain.toml) declares both macOS architectures. However, [bundle-mac](https://github.com/yicheng47/runner/blob/feb2f6804f7f0f8d35e89c2a76e6088b4df3ce43/script/bundle-mac) hard-codes aarch64 and rejects non-arm64 bundled binaries. A declared target is not proof of a functioning Intel app or distribution path. Cargo.lock has 809 package entries; that is not the platform-specific download count or audited build-script closure.

Source supports a daemon-owned PTY/session lifecycle, native-provider resume plans, mission-slot restart and mission coordination. The reviewed operations address stored Runner session identities. They do not establish attachment to arbitrary pre-existing Codex/Claude terminal processes. Stop is termination, not a reversible pause; provider resume starts another process. GUI fidelity, status freshness, real-provider controls, durable recovery and external-session coverage are untested.

First launch is not passive: [skill defaults](https://github.com/yicheng47/runner/blob/feb2f6804f7f0f8d35e89c2a76e6088b4df3ce43/crates/runner-app/src/app_store/skill_defaults.rs) reconcile detected native skill roots, while command initialization may install CLI links. [Mission permission mode](https://github.com/yicheng47/runner/blob/feb2f6804f7f0f8d35e89c2a76e6088b4df3ce43/crates/runner-core/src/protocol/permissions.rs) defaults to Bypass; adapters can add hook trust/permission-prompt overrides. These source defaults grant no trial authority. A candidate skill's escalation instruction cannot override user permissions.

## Disposition and next boundary

Defer normal Intel installation/launch; do not start an unbounded port. Reopen for an official Intel/universal artifact or a separately approved bounded source-build feasibility attempt with reviewed prerequisites. Any later mock trial must establish fresh-home/environment routing, suppressed persistent installers/updater actions, ordinary permission checks and owned cleanup before real providers. A disposable directory alone proves no security boundary. The Codex adapter also calls native hook-server trust seeding and conditional project-trust seeding. Their exact targets and guards are outside this selected closure. Suppressing injected hook arguments or mission Bypass does not establish suppression of those native trust/configuration effects. External mock sessions should be a separate negative/coverage case.

Runner would own processes and coordination; Context can supply explicitly selected versioned guidance through existing delivery/task-packet contracts. No duplicate Context runtime, installer or skill store is justified. No consumer was refreshed or result adopted. Token cost, timing advantage, human usefulness and generalized productivity remain Unknown.

Independent Sol source/document review accepted the bounded static conclusions with the native trust-seeding qualification. No provider, fetch, test, Runner execution or runtime/adoption acceptance belongs to that review.
