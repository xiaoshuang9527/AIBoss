-- ============================================================
-- 智能求职辅助工作台 · 表结构（MySQL 8.0+）
-- ============================================================
-- 说明：
--   * 后端默认用 SQLAlchemy 的 create_all 自动建表；
--     若要用 Alembic 或手工管理 schema，直接执行本脚本。
--   * 本工具是纯个人求职辅助，**不存在任何额度/计费/套餐字段**。
--   * 使用 utf8mb4，保证 emoji 与生僻字可存。
-- ============================================================

-- CREATE DATABASE boss_workbench DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
-- USE boss_workbench;

-- ---------- 授权档案 ----------
CREATE TABLE IF NOT EXISTS auth_profiles (
    id                 INT AUTO_INCREMENT PRIMARY KEY,
    name               VARCHAR(128) NOT NULL DEFAULT '默认账号',
    profile_dir        VARCHAR(512) NOT NULL,
    profile_hash       VARCHAR(64)  NOT NULL,
    browser            VARCHAR(16)  NOT NULL DEFAULT 'chrome',
    cdp_port           INT          NOT NULL DEFAULT 9222,
    cookie_blob_path   VARCHAR(512) NULL,
    cookie_fingerprint VARCHAR(64)  NULL,
    account_label      VARCHAR(128) NULL,
    status             VARCHAR(24)  NOT NULL DEFAULT 'unknown',
    last_verified_at   DATETIME(6)  NULL,
    last_error         TEXT         NULL,
    expires_hint       DATETIME(6)  NULL,
    is_default         TINYINT(1)   NOT NULL DEFAULT 1,
    created_at         DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at         DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    UNIQUE KEY uq_auth_profiles_hash (profile_hash),
    KEY ix_auth_profiles_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------- 抓取任务 ----------
CREATE TABLE IF NOT EXISTS scrape_tasks (
    id                INT AUTO_INCREMENT PRIMARY KEY,
    keyword           VARCHAR(128) NOT NULL,
    city              VARCHAR(64)  NOT NULL,
    salary            VARCHAR(64)  NULL,
    experience        VARCHAR(64)  NULL,
    degree            VARCHAR(64)  NULL,
    company_scale     VARCHAR(64)  NULL,
    company_stage     VARCHAR(64)  NULL,
    industry          VARCHAR(64)  NULL,
    max_pages         INT          NOT NULL DEFAULT 3,
    with_detail       TINYINT(1)   NOT NULL DEFAULT 1,
    note              VARCHAR(255) NULL,
    status            VARCHAR(24)  NOT NULL DEFAULT 'pending',
    progress          INT          NOT NULL DEFAULT 0,
    stage_text        VARCHAR(128) NULL,
    total_found       INT          NOT NULL DEFAULT 0,
    new_count         INT          NOT NULL DEFAULT 0,
    detail_count      INT          NOT NULL DEFAULT 0,
    attempts          INT          NOT NULL DEFAULT 0,
    error_message     TEXT         NULL,
    log_path          VARCHAR(512) NULL,
    result_path       VARCHAR(512) NULL,
    pid               INT          NULL,
    duration_seconds  DOUBLE       NULL,
    auth_profile_id   INT          NULL,
    created_at        DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at        DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    started_at        DATETIME(6)  NULL,
    finished_at       DATETIME(6)  NULL,
    KEY ix_scrape_tasks_status (status),
    KEY ix_scrape_tasks_keyword (keyword),
    KEY ix_scrape_tasks_city (city),
    KEY ix_scrape_tasks_auth_profile_id (auth_profile_id),
    KEY ix_scrape_tasks_status_created (status, created_at),
    CONSTRAINT fk_scrape_tasks_auth_profile
        FOREIGN KEY (auth_profile_id) REFERENCES auth_profiles (id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------- 岗位明细 ----------
CREATE TABLE IF NOT EXISTS job_records (
    id                 INT AUTO_INCREMENT PRIMARY KEY,
    job_id             VARCHAR(64)  NOT NULL,
    title              VARCHAR(255) NOT NULL DEFAULT '',
    company            VARCHAR(255) NOT NULL DEFAULT '',
    salary             VARCHAR(64)  NULL,
    salary_source      VARCHAR(32)  NULL,
    salary_min         INT          NULL,
    salary_max         INT          NULL,
    salary_avg         INT          NULL,
    city               VARCHAR(64)  NULL,
    location           VARCHAR(255) NULL,
    experience         VARCHAR(64)  NULL,
    degree             VARCHAR(64)  NULL,
    company_scale      VARCHAR(64)  NULL,
    company_stage      VARCHAR(64)  NULL,
    company_industry   VARCHAR(128) NULL,
    boss_name          VARCHAR(128) NULL,
    boss_title         VARCHAR(128) NULL,
    boss_active_status VARCHAR(64)  NULL,
    -- JSON 数组字符串；MySQL 8 也可改用原生 JSON 类型
    tags               TEXT         NULL,
    skills_raw         TEXT         NULL,
    welfare            TEXT         NULL,
    skill_tags         TEXT         NULL,
    skill_keywords     TEXT         NULL,
    jd                 LONGTEXT     NULL,
    job_duty           LONGTEXT     NULL,
    job_requirement    LONGTEXT     NULL,
    job_link           VARCHAR(512) NULL,
    company_link       VARCHAR(512) NULL,
    security_id        VARCHAR(255) NULL,
    lid                VARCHAR(128) NULL,
    first_task_id      INT          NULL,
    last_task_id       INT          NULL,
    is_new             TINYINT(1)   NOT NULL DEFAULT 1,
    is_favorite        TINYINT(1)   NOT NULL DEFAULT 0,
    detail_fetched     TINYINT(1)   NOT NULL DEFAULT 0,
    raw_payload        LONGTEXT     NULL,
    scraped_at         DATETIME(6)  NULL,
    created_at         DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at         DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    UNIQUE KEY uq_job_records_job_id (job_id),
    KEY ix_job_records_job_id (job_id),
    KEY ix_job_records_title (title),
    KEY ix_job_records_company (company),
    KEY ix_job_records_city (city),
    KEY ix_job_records_first_task_id (first_task_id),
    KEY ix_job_records_last_task_id (last_task_id),
    KEY ix_job_records_city_salary (city, salary_avg),
    KEY ix_job_records_created (created_at),
    CONSTRAINT fk_job_records_first_task
        FOREIGN KEY (first_task_id) REFERENCES scrape_tasks (id) ON DELETE SET NULL,
    CONSTRAINT fk_job_records_last_task
        FOREIGN KEY (last_task_id) REFERENCES scrape_tasks (id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------- 系统日志 ----------
CREATE TABLE IF NOT EXISTS system_logs (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    level           VARCHAR(16) NOT NULL DEFAULT 'info',
    category        VARCHAR(32) NOT NULL DEFAULT 'system',
    message         TEXT        NOT NULL,
    detail          TEXT        NULL,
    task_id         INT         NULL,
    auth_profile_id INT         NULL,
    created_at      DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    KEY ix_system_logs_level (level),
    KEY ix_system_logs_category (category),
    KEY ix_system_logs_task_id (task_id),
    KEY ix_system_logs_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------- 运行时设置 ----------
CREATE TABLE IF NOT EXISTS app_settings (
    key        VARCHAR(64) PRIMARY KEY,
    value      TEXT        NULL,
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
