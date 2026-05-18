# Commit Template

Use this template for commits that change behavior, tests, architecture, or phase status.

Keep the first line concise and imperative:

```text
phase3x: add worker item concurrency
```

Use the body for the context reviewers need:

```text
Summary:
- Add configurable item concurrency to the Go worker pipeline.
- Keep default concurrency at 1 for conservative local behavior.
- Update Phase 3.x plan status for 3.11.

Changed:
- data-plane/internal/pipeline: process uploadable items with bounded concurrency.
- data-plane/cmd/worker: add -item-concurrency CLI flag and validation.
- docs/plans: record completed scope and verification.

Tests:
- cd data-plane && GOCACHE=/tmp/smh_go_cache go test ./...
- cd control-plane && .venv/bin/python -m ruff check tests/integration/test_phase2_bridge.py
- cd control-plane && RUN_DOCKER_TESTS=1 .venv/bin/python -m pytest tests/integration/test_phase2_bridge.py::test_duplicate_source_reference_kafka_task_keeps_final_state_stable

Notes:
- Default remains serial item processing.
- Docker Kafka/MinIO must be running for RUN_DOCKER_TESTS=1.
```

## Minimum Body

For small commits, still include at least:

```text
Summary:
- ...

Tests:
- ...
```

## When To Include Notes

Add `Notes` when any of these apply:

- Tests were skipped or require Docker.
- A known limitation remains.
- The change intentionally preserves compatibility behavior.
- The commit updates docs / plans without runtime code.

## Suggested Command

Use a commit message file when the body is longer than one line:

```bash
git commit -F /tmp/commit-message.txt
```
