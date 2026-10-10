-- ============================================================
-- 智能求职辅助工作台 · 表结构（PostgreSQL）
-- ============================================================
-- 说明：
--   * 后端默认用 SQLAlchemy 的 create_all 自动建表；
--     若要用 Alembic 或手工管理 schema，直接执行本脚本。
--   * 本工具是纯个人求职辅助，**不存在任何额度/计费/套餐字段**。
--   * 字符集统一 UTF-8；时间戳统一带时区（timestamptz）。
-- ============================================================

-- ---------- 授权档案：BOSS 直聘登录态 ----------
CREATE TABLE IF NOT EXISTS auth_profiles (
    id                SERIAL PRIMARY KEY,
    name              VARCHAR(128)  NOT NULL DEFAULT '默认账号',
    -- Chrome --user-data-dir（隔离 profile，不碰用户主浏览器）
    profile_dir       VARCHAR(512)  NOT NULL,
    -- 档案指纹：用于目录命名与前端展示，不含明文凭据
    profile_hash      VARCHAR(64)   NOT NULL UNIQUE,
    browser           VARCHAR(16)   NOT NULL DEFAULT 'chrome',
    cdp_port          INTEGER       NOT NULL DEFAULT 9222,
    -- 登录态加密快照路径 + 指纹（数据库里不留明文 Cookie）
    cookie_blob_path  VARCHAR(512),
    cookie_fingerprint VARCHAR(64),
    account_label     VARCHAR(128),
    status            VARCHAR(24)   NOT NULL DEFAULT 'unknown',
    last_verified_at  TIMESTAMPTZ,
    last_error        TEXT,
    expires_hint      TIMESTAMPTZ,
    is_default        BOOLEAN       NOT NULL DEFAULT TRUE,
    created_at        TIMESTAMPTZ   NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ   NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_auth_profiles_status ON auth_profiles (status);

-- ---------- 抓取任务 ----------
CREATE TABLE IF NOT EXISTS scrape_tasks (
    id                  SERIAL PRIMARY KEY,
    -- 用户输入
    keyword             VARCHAR(128) NOT NULL,
    city                VARCHAR(64)  NOT NULL,
    salary              VARCHAR(64),
    experience          VARCHAR(64),
    degree              VARCHAR(64),
    company_scale       VARCHAR(64),
    company_stage       VARCHAR(64),
    industry            VARCHAR(64),
    max_pages           INTEGER      NOT NULL DEFAULT 3,
    with_detail         BOOLEAN      NOT NULL DEFAULT TRUE,
    note                VARCHAR(255),
    -- 执行状态
    status              VARCHAR(24)  NOT NULL DEFAULT 'pending',
    progress            INTEGER      NOT NULL DEFAULT 0,
    stage_text          VARCHAR(128),
    -- 结果统计
    total_found         INTEGER      NOT NULL DEFAULT 0,
    new_count           INTEGER      NOT NULL DEFAULT 0,
    detail_count        INTEGER      NOT NULL DEFAULT 0,
    -- 运行元信息
    attempts            INTEGER      NOT NULL DEFAULT 0,
    error_message       TEXT,
    log_path            VARCHAR(512),
    result_path         VARCHAR(512),
    pid                 INTEGER,
    duration_seconds    DOUBLE PRECISION,
    auth_profile_id     INTEGER REFERENCES auth_profiles (id) ON DELETE SET NULL,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    started_at          TIMESTAMPTZ,
    finished_at         TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS ix_scrape_tasks_status ON scrape_tasks (status);
CREATE INDEX IF NOT EXISTS ix_scrape_tasks_keyword ON scrape_tasks (keyword);
CREATE INDEX IF NOT EXISTS ix_scrape_tasks_city ON scrape_tasks (city);
CREATE INDEX IF NOT EXISTS ix_scrape_tasks_auth_profile_id ON scrape_tasks (auth_profile_id);
CREATE INDEX IF NOT EXISTS ix_scrape_tasks_status_created ON scrape_tasks (status, created_at);

-- ---------- 岗位明细（按 job_id 全局去重） ----------
CREATE TABLE IF NOT EXISTS job_records (
    id                  SERIAL PRIMARY KEY,
    job_id              VARCHAR(64)  NOT NULL UNIQUE,
    -- 列表字段
    title               VARCHAR(255) NOT NULL DEFAULT '',
    company             VARCHAR(255) NOT NULL DEFAULT '',
    salary              VARCHAR(64),
    salary_source       VARCHAR(32),
    salary_min          INTEGER,
    salary_max          INTEGER,
    salary_avg          INTEGER,
    city                VARCHAR(64),
    location            VARCHAR(255),
    experience          VARCHAR(64),
    degree              VARCHAR(64),
    company_scale       VARCHAR(64),
    company_stage       VARCHAR(64),
    company_industry    VARCHAR(128),
    boss_name           VARCHAR(128),
    boss_title          VARCHAR(128),
    boss_active_status  VARCHAR(64),
    -- 标签（skills / skill_tags / skill_keywords 均为 JSON 数组字符串）
    tags                TEXT,
    skills_raw          TEXT,
    welfare             TEXT,
    skill_tags          TEXT,
    skill_keywords      TEXT,
    -- 详情全文
    jd                  TEXT,
    job_duty            TEXT,
    job_requirement     TEXT,
    -- 跳转链接
    job_link            VARCHAR(512),
    company_link        VARCHAR(512),
    security_id         VARCHAR(255),
    lid                 VARCHAR(128),
    -- 归属与时间
    first_task_id       INTEGER REFERENCES scrape_tasks (id) ON DELETE SET NULL,
    last_task_id        INTEGER REFERENCES scrape_tasks (id) ON DELETE SET NULL,
    is_new              BOOLEAN      NOT NULL DEFAULT TRUE,
    is_favorite         BOOLEAN      NOT NULL DEFAULT FALSE,
    detail_fetched      BOOLEAN      NOT NULL DEFAULT FALSE,
    raw_payload         TEXT,
    scraped_at          TIMESTAMPTZ,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_job_records_job_id ON job_records (job_id);
CREATE INDEX IF NOT EXISTS ix_job_records_title ON job_records (title);
CREATE INDEX IF NOT EXISTS ix_job_records_company ON job_records (company);
CREATE INDEX IF NOT EXISTS ix_job_records_city ON job_records (city);
CREATE INDEX IF NOT EXISTS ix_job_records_first_task_id ON job_records (first_task_id);
CREATE INDEX IF NOT EXISTS ix_job_records_last_task_id ON job_records (last_task_id);
CREATE INDEX IF NOT EXISTS ix_job_records_city_salary ON job_records (city, salary_avg);
CREATE INDEX IF NOT EXISTS ix_job_records_created ON job_records (created_at);

-- ---------- 系统日志 ----------
CREATE TABLE IF NOT EXISTS system_logs (
    id                SERIAL PRIMARY KEY,
    level             VARCHAR(16) NOT NULL DEFAULT 'info',
    category          VARCHAR(32) NOT NULL DEFAULT 'system',
    message           TEXT        NOT NULL,
    detail            TEXT,
    task_id           INTEGER,
    auth_profile_id   INTEGER,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_system_logs_level ON system_logs (level);
CREATE INDEX IF NOT EXISTS ix_system_logs_category ON system_logs (category);
CREATE INDEX IF NOT EXISTS ix_system_logs_task_id ON system_logs (task_id);
CREATE INDEX IF NOT EXISTS ix_system_logs_created ON system_logs (created_at);

-- ---------- 运行时设置（键值） ----------
CREATE TABLE IF NOT EXISTS app_settings (
    key         VARCHAR(64) PRIMARY KEY,
    value       TEXT,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
