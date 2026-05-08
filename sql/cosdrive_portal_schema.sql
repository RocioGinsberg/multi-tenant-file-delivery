-- 10. cosdrive_registry_version — 分类匹配注册表版本
--     config_json 存放完整的 task_classification.json 结构体
-- 域归属：Portal / Control Plane / cosdrive registry draft
-- 说明：仅用于 CosDrive 上传规则配置，不属于 MinIO Upload 域。
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS cosdrive_registry_version (
    id              TEXT        PRIMARY KEY,
    version_no      INT         NOT NULL DEFAULT 1,
    status          TEXT        NOT NULL DEFAULT 'draft'
                                CHECK (status IN ('draft', 'published', 'archived')),
    config_json     JSONB       NOT NULL DEFAULT '{}'::jsonb,

    created_by      TEXT        NOT NULL DEFAULT '',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    published_by    TEXT        NOT NULL DEFAULT '',
    published_at    TIMESTAMPTZ,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE cosdrive_registry_version IS
  'CosDrive 文件分类注册表版本。config_json 包含 team_aliases / task_classification / '
  'description_mapping / mapping_config / suffix_priority / suffix_fallback / ignored_filenames。';

CREATE INDEX IF NOT EXISTS idx_cosdrive_reg_status
    ON cosdrive_registry_version (status, version_no DESC);

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_cosdrive_reg_updated') THEN
        CREATE TRIGGER trg_cosdrive_reg_updated BEFORE UPDATE ON cosdrive_registry_version
            FOR EACH ROW EXECUTE FUNCTION trigger_set_updated_at();
    END IF;
END $$;


-- ─────────────────────────────────────────────────────────────────────────────
-- 11. cosdrive_upload_task — 腾讯企业网盘 / SMH 上传任务
-- 必须独立于 MinIO Upload；独立 worker / deployment / 状态机 / 队列
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS cosdrive_upload_task (
    task_id               TEXT        PRIMARY KEY,
    task_status           TEXT        NOT NULL DEFAULT 'draft'
                                        CHECK (task_status IN (
                                            'draft',
                                            'classifying',
                                            'classified',
                                            'confirmed',
                                            'queued',
                                            'uploading',
                                            'partial_failed',
                                            'completed',
                                            'failed',
                                            'cancelled'
                                        )),
    classification_status TEXT        NOT NULL DEFAULT 'pending'
                                        CHECK (classification_status IN ('pending','running','classified','failed','cancelled')),
    delivery_status       TEXT        NOT NULL DEFAULT 'pending'
                                        CHECK (delivery_status IN ('pending','queued','uploading','partial_failed','completed','failed','cancelled')),
    registry_version_id   TEXT        NOT NULL DEFAULT '',
    idempotency_key       TEXT        NOT NULL,
    request_id            TEXT        NOT NULL DEFAULT '',
    trace_id              TEXT        NOT NULL DEFAULT '',
    source_event_id       TEXT        NOT NULL DEFAULT '',
    input_archive_name    TEXT        NOT NULL DEFAULT '',
    input_archive_uri     TEXT        NOT NULL DEFAULT '',
    temp_dir              TEXT        NOT NULL DEFAULT '',
    team_snapshot_json    JSONB       NOT NULL DEFAULT '[]'::jsonb,
    classification_summary_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    external_summary_json JSONB       NOT NULL DEFAULT '{}'::jsonb,
    submitted_by          TEXT        NOT NULL DEFAULT '',
    current_attempt_no    INT         NOT NULL DEFAULT 0,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    confirmed_at          TIMESTAMPTZ,
    completed_at          TIMESTAMPTZ,
    last_error_code       TEXT        NOT NULL DEFAULT '',
    last_error_message    TEXT        NOT NULL DEFAULT ''
);

COMMENT ON TABLE cosdrive_upload_task IS
  'CosDrive 任务主表。Portal 只发起任务；实际执行由独立 CosDrive worker 完成。';

CREATE UNIQUE INDEX IF NOT EXISTS uq_cosdrive_upload_task_idempotency
    ON cosdrive_upload_task (idempotency_key);
CREATE INDEX IF NOT EXISTS idx_cosdrive_upload_task_status
    ON cosdrive_upload_task (task_status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_cosdrive_upload_task_user
    ON cosdrive_upload_task (submitted_by, created_at DESC);

CREATE TABLE IF NOT EXISTS cosdrive_upload_attempt (
    attempt_id            TEXT        PRIMARY KEY,
    task_id               TEXT        NOT NULL REFERENCES cosdrive_upload_task(task_id) ON DELETE CASCADE,
    attempt_no            INT         NOT NULL,
    attempt_status        TEXT        NOT NULL DEFAULT 'pending'
                                        CHECK (attempt_status IN (
                                            'pending',
                                            'dispatched',
                                            'running',
                                            'rate_limited',
                                            'succeeded',
                                            'failed',
                                            'cancelled'
                                        )),
    worker_key            TEXT        NOT NULL DEFAULT '',
    queue_name            TEXT        NOT NULL DEFAULT 'cosdrive-upload',
    request_id            TEXT        NOT NULL DEFAULT '',
    trace_id              TEXT        NOT NULL DEFAULT '',
    source_event_id       TEXT        NOT NULL DEFAULT '',
    started_at            TIMESTAMPTZ,
    finished_at           TIMESTAMPTZ,
    error_code            TEXT        NOT NULL DEFAULT '',
    error_message         TEXT        NOT NULL DEFAULT '',
    metrics_json          JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_cosdrive_upload_attempt_no UNIQUE (task_id, attempt_no)
);

CREATE INDEX IF NOT EXISTS idx_cosdrive_upload_attempt_status
    ON cosdrive_upload_attempt (task_id, attempt_status, attempt_no DESC);

CREATE TABLE IF NOT EXISTS cosdrive_upload_event (
    event_id              TEXT        PRIMARY KEY,
    task_id               TEXT        NOT NULL REFERENCES cosdrive_upload_task(task_id) ON DELETE CASCADE,
    attempt_id            TEXT        REFERENCES cosdrive_upload_attempt(attempt_id) ON DELETE SET NULL,
    sequence_no           BIGINT      NOT NULL,
    event_type            TEXT        NOT NULL,
    from_status           TEXT        NOT NULL DEFAULT '',
    to_status             TEXT        NOT NULL DEFAULT '',
    request_id            TEXT        NOT NULL DEFAULT '',
    trace_id              TEXT        NOT NULL DEFAULT '',
    source_event_id       TEXT        NOT NULL DEFAULT '',
    payload_json          JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_cosdrive_upload_event_seq UNIQUE (task_id, sequence_no)
);

CREATE INDEX IF NOT EXISTS idx_cosdrive_upload_event_task
    ON cosdrive_upload_event (task_id, sequence_no DESC);

CREATE TABLE IF NOT EXISTS cosdrive_target (
    target_id             TEXT        PRIMARY KEY,
    task_id               TEXT        NOT NULL REFERENCES cosdrive_upload_task(task_id) ON DELETE CASCADE,
    target_type           TEXT        NOT NULL DEFAULT 'team_folder'
                                        CHECK (target_type IN ('team_folder','task_folder','direct_path')),
    team_name_raw         TEXT        NOT NULL DEFAULT '',
    team_name_matched     TEXT        NOT NULL DEFAULT '',
    team_space_id         TEXT        NOT NULL DEFAULT '',
    team_org_id           TEXT        NOT NULL DEFAULT '',
    task_name             TEXT        NOT NULL DEFAULT '',
    category_name         TEXT        NOT NULL DEFAULT '',
    drive_dir             TEXT        NOT NULL DEFAULT '',
    drive_path            TEXT        NOT NULL DEFAULT '',
    mapping_source        TEXT        NOT NULL DEFAULT '',
    target_snapshot_json  JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_cosdrive_target_task
    ON cosdrive_target (task_id, created_at DESC);

CREATE TABLE IF NOT EXISTS cosdrive_delivery_record (
    record_id             TEXT        PRIMARY KEY,
    task_id               TEXT        NOT NULL REFERENCES cosdrive_upload_task(task_id) ON DELETE CASCADE,
    attempt_id            TEXT        REFERENCES cosdrive_upload_attempt(attempt_id) ON DELETE SET NULL,
    target_id             TEXT        REFERENCES cosdrive_target(target_id) ON DELETE SET NULL,
    source_item_key       TEXT        NOT NULL DEFAULT '',
    filename              TEXT        NOT NULL DEFAULT '',
    relative_path         TEXT        NOT NULL DEFAULT '',
    file_size             BIGINT      NOT NULL DEFAULT 0,
    checksum              TEXT        NOT NULL DEFAULT '',
    severity              TEXT        NOT NULL DEFAULT 'ok'
                                        CHECK (severity IN ('ok','warning','error','ignored')),
    delivery_status       TEXT        NOT NULL DEFAULT 'pending'
                                        CHECK (delivery_status IN (
                                            'pending',
                                            'uploading',
                                            'delivered',
                                            'failed',
                                            'skipped',
                                            'rate_limited'
                                        )),
    external_request_id   TEXT        NOT NULL DEFAULT '',
    external_file_id      TEXT        NOT NULL DEFAULT '',
    external_receipt_json JSONB       NOT NULL DEFAULT '{}'::jsonb,
    error_code            TEXT        NOT NULL DEFAULT '',
    error_message         TEXT        NOT NULL DEFAULT '',
    warning_message       TEXT        NOT NULL DEFAULT '',
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    delivered_at          TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_cosdrive_delivery_record_task
    ON cosdrive_delivery_record (task_id, delivery_status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_cosdrive_delivery_record_target
    ON cosdrive_delivery_record (target_id, created_at DESC);

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_cosdrive_upload_task_updated') THEN
        CREATE TRIGGER trg_cosdrive_upload_task_updated BEFORE UPDATE ON cosdrive_upload_task
            FOR EACH ROW EXECUTE FUNCTION trigger_set_updated_at();
