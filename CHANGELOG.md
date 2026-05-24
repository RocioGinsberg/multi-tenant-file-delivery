# Changelog

All notable changes to this project will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- Public README split into English `README.md` and Chinese `README_ZH.md`.
- Demo generation helper at `examples/demo.sh`; generated media path is `docs/media/demo.gif`.
- Public contribution guide at `docs/CONTRIBUTING.md`.
- Root MIT license.

### Changed
- Root documentation is now organized as a public project entry point instead of a phase log.
- `docs/ROADMAP.md` marks the next release target as `v0.1.0`.
- Public-readiness dependency hardening upgrades the Go data-plane baseline to Go 1.25 and switches the control-plane MySQL async driver from `asyncmy` to `aiomysql`.

## [0.1.0] - 2026-05-24

First public release. The implementation covers Phase 0 through Phase 6.5.

### Added
- **Monorepo foundation**: Python control plane, Go data plane, static web UI, Docker Compose dependencies, profiles, and structured docs.
- **HQ folder upload**: browser folder picker, multipart file upload, server-side internal archive, classification, preview, confirmation, retry, and progress stream.
- **Classification profile engine**: target matching, document-type mapping, ignored file filters, warnings, blocking errors, and persisted task item metadata.
- **Control/data bridge**: `delivery.tasks.v1` and `delivery.results.v1` messages over local file-spool and Kafka.
- **Go data-plane worker**: file source, object source resolver, mock sink, S3/MinIO single-part PUT sink, receipt SHA-256, and result publishing.
- **Source reference path**: staged internal archive in MinIO/S3 so workers can read source bytes without sharing the control-plane filesystem.
- **MySQL and migrations**: Alembic migrations for tenant, auth, task ownership, workspace, physical object, and workspace object metadata.
- **Redis capability layer**: progress pub/sub, short-TTL idempotency guard, result-apply lease, and data-plane fixed-window upload limiter.
- **Observability stack**: OTel Collector, Prometheus, Grafana dashboard, control-plane metrics, data-plane metrics, and W3C `traceparent` propagation.
- **Multi-tenancy and auth baseline**: dev actor headers, default local actor, tenant/user ownership, tenant-aware repositories, and role-derived workspace access.
- **Workspace read view**: workspace list, object list/detail, short-TTL presigned download URL, audit event for URL issuance, and static subsidiary page.
- **Design records**: PRD, architecture, data model, RFCs, ADRs, phase plans, benchmark notes, and sink protocol notes.

### Changed
- README narrative refocused from generic "HQ-to-subsidiary" distribution to the cross-boundary scenario: HQ distributing operational data (order KPIs, performance reports) to external partners who cannot access the internal network.
- README restructured for three reader personas: HQ operators, external partners, and platform engineers.
- "What this implementation proves" tech-stack checklist replaced with role-organized capability descriptions.
- Status section shortened; Key Invariants moved to ARCHITECTURE.md reference.
- All sample data de-identified: real company names replaced with generic placeholders.
- Demo GIF regenerated with de-identified content and slower frame timing.

### Known Gaps
- S3 multipart, resume, OSS/Webhook/SFTP sinks, platform-level dedup, refcount GC, sink credential encryption, and full audit log table are deferred.
- Result consume/apply currently starts a separate trace; result messages do not yet continue the worker trace context back to the control plane.
- The static UI is a local demo surface, not a production identity provider or frontend build pipeline.

[Unreleased]: https://github.com/RocioGinsberg/multi-tenant-file-delivery/commits/main
[0.1.0]: https://github.com/RocioGinsberg/multi-tenant-file-delivery/releases/tag/v0.1.0
