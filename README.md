# 智能求职辅助工作台

一个**纯个人求职辅助**的动态 Web 工作台：输入求职方向、城市、薪资要求，后端调度
[`boss-zhipin-scraper`](./boss-zhipin-scraper)（基于 Chrome DevTools Protocol 的
BOSS 直聘爬虫）抓取岗位，前端以企业级 SaaS 仪表盘的形式浏览、筛选结果，并可一键精准
跳转到 BOSS 直聘对应岗位页面进行沟通。

> ⚠️ **免责声明**
> 本工具仅供个人学习与求职分析使用，数据来源于公开职位页面。严禁用于商业倒卖、批量转售
> 或任何违反 [BOSS 直聘用户协议](https://www.zhipin.com/about/protocol.html) 的用途。
> 请务必控制抓取频率。使用产生的一切后果由使用者自行承担。
>
> 📌 **本工具不涉及任何商业计费：没有额度、套餐、充值、会员等概念，数据库与 UI 中都不存在相关字段。**

---

## 目录

1. [功能总览](#1-功能总览)
2. [项目目录结构规划](#2-项目目录结构规划)
3. [数据库表结构设计](#3-数据库表结构设计)
4. [FastAPI 核心路由设计](#4-fastapi-核心路由设计)
5. [封装 boss-zhipin-scraper 的调度逻辑](#5-封装-boss-zhipin-scraper-的调度逻辑)
6. [Vue 3 前端核心组件](#6-vue-3-前端核心组件)
6A. [AI 对话模块](#6a-ai-对话模块)
7. [部署与运行说明](#7-部署与运行说明)
8. [关键设计权衡与注意事项](#8-关键设计权衡与注意事项)
9. [测试](#9-测试)

---

## 1. 功能总览

### 界面布局

| 区域 | 内容 |
|------|------|
| 左侧导航栏 | 工作台、账号授权、创建抓取任务、岗位结果、历史记录、系统设置、免责声明 |
| 顶部状态栏 | 欢迎语、系统运行状态（服务正常 / 待授权 / 浏览器未启动）、WebSocket 通道状态、队列情况、刷新与快捷入口 |
| 主内容区 | 4 张数据概览卡片 → 快捷入口 → 图表面板 → 高频技能 / 热门公司 → 最近任务记录表格 |

### 数据概览（顶部 4 张卡片）

| 卡片 | 统计口径 |
|------|----------|
| 累计抓取岗位数 | 成功入库且按 `job_id` 去重后的岗位总数 |
| 今日新增岗位数 | 按东八区自然日统计的今日入库岗位数 |
| 监控任务数 | `pending` + `running` 状态的任务数 |
| 账号授权状态 | 授权档案状态（已授权 / 等待扫码 / 已失效 / 未校验）|

### 快捷入口

「开始抓取岗位」「授权 BOSS 账号」「查看历史记录」「系统设置」

### 任务创建

表单字段：求职方向/关键词（必填）、目标城市（必填，可搜索可自定义）、薪资要求（选填）、
经验要求（选填）、学历要求（选填）、公司规模（选填）、抓取数量上限（页数，硬上限 10 页）、
是否抓取岗位详情、备注。提交后进入后台队列异步执行。

### 岗位结果展示

表格 / 卡片双视图，字段包含：岗位名称、公司名称、薪资范围、经验与学历要求、
技能标签（从 JD 中提取的高频词）、**岗位详情（岗位职责与岗位要求全文）**。

**核心交互**：每条岗位都有「去 BOSS 沟通」按钮，通过爬虫保留的
`https://www.zhipin.com/job_detail/{encryptJobId}.html` 精准跳转到该岗位详情页。

结果页支持：关键词 / 城市 / 来源任务 / 经验 / 学历 / 薪资区间 / 技能标签 / 只看新增 /
只看收藏 / 只看有 JD 的多维筛选，以及排序、收藏、批量删除、导出 CSV / JSON。

### 历史任务记录

字段：生成时间、目标城市、搜索关键词、筛选条件、抓取数量、页数、耗时、状态。
操作列：查看详情（含实时日志与任务统计图表）、重试、取消、导出 CSV / JSON、删除。

### 实时性

- WebSocket 通道 `/api/ws/tasks`（任务进度、队列变化）与 `/api/ws/auth`（扫码授权进度）；
- 断线自动重连（指数退避），并自动降级为 HTTP 轮询，用户不会看到"卡住"的界面。

---

## 2. 项目目录结构规划

```
D:\Project\Boss\
├── boss-zhipin-scraper/              # 上游爬虫项目（原样拉取，只通过 CLI 调用，不修改）
│   ├── scripts/boss_cdp_raw.py       #   抓取主脚本 + CLI 入口
│   ├── scripts/job_summary.py        #   抓取后摘要
│   └── data/city_codes.json          #   全量城市码表（300+ 城市）
│
├── backend/                          # ===== FastAPI 后端 =====
│   ├── bzp_workbench/
│   │   ├── __init__.py
│   │   ├── main.py                   # 应用入口：lifespan、CORS、异常处理、路由挂载
│   │   ├── config.py                 # pydantic-settings 配置（BZP_* 环境变量 / .env）
│   │   ├── db.py                     # 异步引擎、SessionLocal、建表、get_db 依赖
│   │   ├── enums.py                  # TaskStatus / AuthStatus / LogLevel / LogCategory
│   │   ├── models.py                 # ORM 模型（4 张业务表 + 1 张设置表）
│   │   ├── schemas.py                # Pydantic v2 请求/响应模型
│   │   ├── parsing.py                # 薪资解析、JD 拆分、技能词提取
│   │   ├── utils.py                  # 时间归一化、JSON 原子读写、日志尾部读取
│   │   ├── api/                      # ---- 路由层 ----
│   │   │   ├── helpers.py            #   统一响应信封 ok()/fail()/page()
│   │   │   ├── system.py             #   概览、健康检查、设置、日志、维护
│   │   │   ├── auth.py               #   授权档案、扫码会话、登录态校验
│   │   │   ├── tasks.py              #   任务创建/查询/日志/取消/重试/删除/统计
│   │   │   ├── jobs.py               #   岗位列表/详情/筛选面板/收藏/删除
│   │   │   ├── export.py             #   CSV / JSON 导出
│   │   │   └── ws.py                 #   WebSocket 实时通道
│   │   └── services/                 # ---- 业务层 ----
│   │       ├── scraper_runner.py     #   ★ 爬虫调度封装（子进程 + 结果解析）
│   │       ├── queue_service.py      #   ★ 异步任务队列、并发控制、重试
│   │       ├── auth_service.py       #   ★ 扫码授权会话、登录态校验
│   │       ├── crypto_store.py       #   登录态加密打包（AES-GCM / Fernet）
│   │       ├── repository.py         #   数据访问层（SQL 集中于此）
│   │       ├── export_service.py     #   导出格式化
│   │       ├── events.py             #   WebSocket 事件总线
│   │       └── logging_service.py    #   系统日志落库 + 文件日志
│   ├── tests/
│   │   ├── conftest.py               #   隔离环境夹具
│   │   ├── fake_scraper/             #   假爬虫（复刻真实 CLI 契约，无需 Chrome）
│   │   ├── test_parsing.py           #   35 个解析单元测试
│   │   ├── test_scraper_runner.py    #   22 个爬虫适配层单元测试
│   │   └── test_api.py               #   23 个接口与调度集成测试
│   ├── requirements.txt
│   ├── pytest.ini
│   ├── .env.example
│   └── storage/                      # 运行时数据（不进版本库）
│       ├── chrome-profiles/          #   隔离 Chrome profile（每个授权档案一个子目录）
│       ├── job-result/task-{id}/     #   每个任务的原始 JSON 结果
│       ├── auth/{hash}/              #   登录态加密快照
│       └── logs/                     #   任务日志、系统日志
│
├── frontend/                         # ===== Vue 3 前端 =====
│   ├── src/
│   │   ├── main.ts                   # 入口：Element Plus + 图标 + Pinia + Router
│   │   ├── App.vue
│   │   ├── router/index.ts           # 路由表（导航项由路由元信息推导）
│   │   ├── layouts/
│   │   │   ├── AppLayout.vue         # 整体布局 + 免责声明条
│   │   │   ├── AppSidebar.vue        # 左侧导航
│   │   │   └── AppHeader.vue         # 顶部状态栏
│   │   ├── views/
│   │   │   ├── DashboardView.vue     # ★ 工作台（卡片 + 快捷入口 + 图表 + 最近任务）
│   │   │   ├── CreateTaskView.vue    # ★ 任务表单 + 实时进度
│   │   │   ├── JobsView.vue          # ★ 结果列表 + 详情 + 去 BOSS 沟通
│   │   │   ├── HistoryView.vue       # ★ 历史记录 + 详情/日志/导出
│   │   │   ├── AuthView.vue          # 扫码授权
│   │   │   ├── SettingsView.vue      # 运行参数 + 环境信息 + 操作日志
│   │   │   └── DisclaimerView.vue    # 免责声明与合规
│   │   ├── components/
│   │   │   ├── StatCard.vue          # 概览卡片
│   │   │   └── ChartPanel.vue        # ECharts 封装（生命周期 + 自适应）
│   │   ├── composables/
│   │   │   ├── useTaskSocket.ts      # 任务 WebSocket（自动重连）
│   │   │   └── useAuthSocket.ts      # 授权 WebSocket
│   │   ├── stores/                   # Pinia：workbench / task / job
│   │   ├── api/                      # http.ts（信封拆封、错误提示）+ index.ts（接口定义）
│   │   ├── types/index.ts            # 与后端 schema 一一对应的 TS 类型
│   │   ├── utils/format.ts           # 时间/字节/耗时/tag 类型格式化
│   │   └── styles/main.css           # 设计令牌与通用面板样式
│   ├── vite.config.ts                # /api 与 /api/ws 代理到后端
│   ├── package.json
│   └── tsconfig.json
│
├── deploy/
│   ├── backend.Dockerfile            # 含 Chromium 与中文字体
│   ├── frontend.Dockerfile           # Node 构建 → Nginx 托管
│   ├── nginx.conf                    # 静态资源 + API/WS 反向代理
│   └── sql/
│       ├── schema_postgresql.sql     # 建表语句（PostgreSQL）
│       └── schema_mysql.sql          # 建表语句（MySQL 8）
│
├── scripts/
│   ├── check-env.ps1                 # 一键环境自检
│   ├── start-backend.ps1             # 启动后端（开发模式）
│   └── start-frontend.ps1            # 启动前端（Vite）
│
├── docker-compose.yml                # PostgreSQL + 后端 + 前端
├── .gitignore
└── README.md
```

---

## 3. 数据库表结构设计

完整建表语句见 [`deploy/sql/schema_postgresql.sql`](./deploy/sql/schema_postgresql.sql)
与 [`deploy/sql/schema_mysql.sql`](./deploy/sql/schema_mysql.sql)；ORM 定义见
[`backend/bzp_workbench/models.py`](./backend/bzp_workbench/models.py)。

### 3.1 `auth_profiles` — 授权档案（登录态）

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | PK | |
| `name` | varchar(128) | 档案显示名 |
| `profile_dir` | varchar(512) | Chrome `--user-data-dir`（隔离 profile，不碰主浏览器） |
| `profile_hash` | varchar(64) UK | 档案指纹，用于目录命名与前端展示 |
| `browser` | varchar(16) | `chrome` / `edge` |
| `cdp_port` | int | CDP 调试端口，默认 9222 |
| `cookie_blob_path` | varchar(512) | **加密**登录态快照文件路径（不存明文） |
| `cookie_fingerprint` | varchar(64) | 快照指纹，用于判断登录态是否变化 |
| `account_label` | varchar(128) | 脱敏账号展示信息 |
| `status` | varchar(24) | `unknown`/`active`/`expired`/`authorizing`/`invalid` |
| `last_verified_at` / `last_error` | | 最近校验时间与错误 |
| `is_default` | bool | 是否默认档案 |

> 安全考虑：真正的 Cookie 内容加密落盘（Fernet / AES-GCM，密钥由 `BZP_SECRET_KEY` 派生），
> 数据库只记录路径与指纹——数据库泄露不等于账号泄露。

### 3.2 `scrape_tasks` — 抓取任务

| 分组 | 字段 |
|------|------|
| 用户输入 | `keyword`、`city`、`salary`、`experience`、`degree`、`company_scale`、`company_stage`、`industry`、`max_pages`、`with_detail`、`note` |
| 执行状态 | `status`（`pending`/`running`/`succeeded`/`failed`/`cancelled`/`needs_attention`）、`progress`、`stage_text` |
| 结果统计 | `total_found`、`new_count`、`detail_count` |
| 运行元信息 | `attempts`、`error_message`、`log_path`、`result_path`、`pid`、`duration_seconds`、`auth_profile_id`(FK) |
| 时间 | `created_at`、`updated_at`、`started_at`、`finished_at` |

### 3.3 `job_records` — 岗位明细

按 `job_id`（爬虫侧 md5 指纹）**全局唯一**，因此同一岗位被多个任务命中时不会重复计数，
`first_task_id` 记录首次发现者、`last_task_id` 记录最近一次命中。

- 列表字段：`title`、`company`、`salary`、`salary_min/max/avg`（归一化到「千元/月」）、
  `city`、`location`、`experience`、`degree`、`company_scale/stage/industry`、
  `boss_name`、`boss_title`、`boss_active_status`
- 标签：`tags`、`skills_raw`、`welfare`、`skill_tags`（详情页给的）、
  `skill_keywords`（从 JD 提取并归一化的高频技能词，均为 JSON 数组字符串）
- 详情全文：`jd`、`job_duty`（岗位职责）、`job_requirement`（岗位要求）
- 跳转：`job_link`、`company_link`、`security_id`、`lid`（详情页导航需要）
- 其他：`is_new`、`is_favorite`、`detail_fetched`、`raw_payload`、`scraped_at`

### 3.4 `system_logs` — 系统操作日志

`level`、`category`（`system`/`auth`/`task`/`scraper`/`export`）、`message`、`detail`、
`task_id`、`auth_profile_id`、`created_at`。

### 3.5 `app_settings` — 运行时设置（键值）

用于持久化系统设置页保存的运行参数（并发数、抓取间隔、重试次数、页数上限等）。

> ❌ **明确不存在**：`quota`、`credit`、`balance`、`plan`、`subscription`、`billing` 等任何
> 与额度/计费/套餐相关的表或字段。

---

## 4. FastAPI 核心路由设计

所有接口统一前缀 `/api`，统一响应信封：

```json
{ "code": 0, "message": "ok", "data": { } }
```

分页响应：`data = { "items": [...], "meta": { "total", "page", "page_size", "pages" } }`

### 4.1 系统 / 概览

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/system/dashboard` | **工作台首页聚合数据**：4 张卡片 + 4 组图表 + 高频技能/公司 + 最近任务 + 免责声明 |
| GET | `/api/system/health` | 轻量健康检查（探针用） |
| GET | `/api/system/scraper-health` | 爬虫集成检查：脚本、版本、Python、CDP 端口、profile |
| GET | `/api/system/settings` | 读取运行参数 |
| PUT | `/api/system/settings` | 更新运行参数（自动夹取到安全范围）|
| GET | `/api/system/logs` | 系统日志分页查询（按级别/分类/任务过滤）|
| DELETE | `/api/system/logs` | 清理 N 天前日志 |
| GET | `/api/system/queue` | 队列实时状态 |
| GET | `/api/system/storage` | 存储占用统计 |
| POST | `/api/system/maintenance/scan-stale` | 手动扫描并收尾"卡死"任务 |

### 4.2 账号授权

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/auth/profiles` | 授权档案列表 |
| GET | `/api/auth/status` | 当前授权状态（含 CDP 是否监听）|
| POST | `/api/auth/verify` | 校验登录态（跑一次爬虫 `--check`）|
| POST | `/api/auth/sessions` | **启动扫码授权会话**（打开专用浏览器）|
| GET | `/api/auth/sessions/current` | 查询授权进度（轮询兜底）|
| DELETE | `/api/auth/sessions/current` | 取消授权 |
| POST | `/api/auth/logout` | 停用登录态 |

### 4.3 抓取任务

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/tasks` | **提交抓取任务**（入队）|
| GET | `/api/tasks` | 任务分页列表（支持状态/关键词/城市过滤）|
| GET | `/api/tasks/queue` | 队列状态 |
| GET | `/api/tasks/{id}` | 任务详情 |
| GET | `/api/tasks/{id}/logs` | **任务运行日志尾部**（实时刷新）|
| GET | `/api/tasks/{id}/summary` | 任务级聚合统计（薪资分布、技能词频、公司分布）|
| POST | `/api/tasks/{id}/cancel` | 取消任务 |
| POST | `/api/tasks/{id}/retry` | 重试任务 |
| DELETE | `/api/tasks/{id}` | 删除任务记录（岗位数据保留）|

### 4.4 岗位结果与导出

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/jobs` | **岗位列表**（关键词/城市/任务/经验/学历/薪资/技能/只看新增/只看收藏/只看有 JD + 排序 + 分页）|
| GET | `/api/jobs/facets` | 筛选面板可选值（城市、经验、学历、技能、薪资区间）|
| GET | `/api/jobs/top-skills` | 全局技能词频排行 |
| GET | `/api/jobs/{id}` | 岗位详情（含职责/要求全文与跳转链接）|
| POST | `/api/jobs/{id}/favorite` | 收藏 / 取消收藏 |
| DELETE | `/api/jobs` | 批量删除岗位 |
| GET | `/api/export/jobs` | **导出 CSV / JSON**（带 UTF-8 BOM，Excel 直接打开不乱码）|
| POST | `/api/export/preview` | 导出前预览条数 |

### 4.5 WebSocket

| 路径 | 事件 |
|------|------|
| `/api/ws/tasks` | `connected`、`queue.started`、`queue.updated`、`queue.delay`、`task.created`、`task.started`、`task.progress`、`task.retrying`、`task.finished`、`browser.starting` |
| `/api/ws/auth` | `connected`、`auth.progress` |
| `/api/ws/chat/{会话id}` | `connected`、`chat.task_progress`（只含该会话发起的任务，见 6A.10） |

交互式文档：启动后访问 `http://127.0.0.1:18080/docs`。

---

## 5. 封装 `boss-zhipin-scraper` 的调度逻辑

核心文件：[`backend/bzp_workbench/services/scraper_runner.py`](./backend/bzp_workbench/services/scraper_runner.py)
与 [`queue_service.py`](./backend/bzp_workbench/services/queue_service.py)。

### 5.1 集成原则

1. **只用公开 CLI**：一律通过 `scripts/boss_cdp_raw.py` 的命令行接口调用，
   不 import 其内部函数——上游重构不会把工作台带崩。
2. **登录态复用靠环境变量重定向**：把子进程的 `HOME` / `USERPROFILE` 指向
   `<storage>/chrome-profiles`，爬虫于是使用
   `<storage>/chrome-profiles/<hash>` 作为隔离 profile，既复用登录态又完全不碰主浏览器。

```python
env = os.environ.copy()
env["HOME"] = str(settings.storage_path / "chrome-profiles")
env["USERPROFILE"] = env["HOME"]      # 决定 profile 落点
env["PYTHONUNBUFFERED"] = "1"         # 让输出及时落盘
env["PYTHONUTF8"] = "1"               # 规避 Windows GBK 编码崩溃
```

3. **输出重定向到文件而非管道**：`asyncio` 在 Windows 上的管道基于命名管道，
   受限环境会直接拒绝（`WinError 5`）；重定向到文件还额外避免了长任务把管道缓冲区
   写满导致子进程阻塞。工作台按 0.3s 增量读取日志文件产出进度事件。

### 5.2 任务执行流程

```
POST /api/tasks
   └─ 写库（status=pending）→ task_queue.notify() → WebSocket 广播 task.created
        │
        ▼
TaskQueue._run_worker（常驻协程，2s 周期兜底扫描）
   └─ _dispatch_available：有空闲槽位就 claim pending 任务（FIFO）并置 running
        └─ _apply_start_delay：任务间随机延迟 3-5s（可配置）
             └─ _execute（带重试）
                  └─ _execute_once
                       ├─ 取授权档案 → 组装 payload（关键词/城市/筛选码/profile_hash）
                       ├─ CDP 端口未监听 → _try_autostart_browser（有限等待后放行）
                       ├─ run_scrape：启动子进程 → 增量读日志 → 解析 JSON 结果
                       ├─ 成功：merge_jobs_with_details → sync_jobs（按 job_id upsert）
                       └─ 失败：classify_failure 归类 → 决定重试或终结
```

提交给爬虫的实际命令（示例）：

```bash
<python> <scraper>/scripts/boss_cdp_raw.py \
  --keyword "Python后端" --city 深圳 --pages 2 \
  --output   <storage>/job-result/task-12/boss_jobs_20261010_1704.json \
  --detail-output <storage>/job-result/task-12/boss_details_20261010_1704.json \
  --cdp-port 9222 --format json --salary 406 --experience 105 --detail
```

### 5.3 并发与反爬节奏控制

| 机制 | 实现 |
|------|------|
| 并发上限 | `BZP_MAX_CONCURRENT_TASKS`，默认 2，硬上限 4（配置层夹取）|
| 任务间隔 | 每个任务启动前 `random.uniform(delay_min, delay_max)`，默认 3-5 秒 |
| 页数上限 | `BZP_MAX_PAGES_PER_TASK`，默认 10；Pydantic 校验层同样夹取 |
| 单任务超时 | `BZP_TASK_TIMEOUT_SECONDS`，默认 1800s；超时优雅终止并保留已抓数据 |
| 定时维护 | 每 120s 扫描"标记 running 但实际卡死"的任务并收尾 |

### 5.4 失败归类与重试策略

`classify_failure()` 解析爬虫输出，把失败分成 6 类：

| 归类 | 触发特征 | 是否重试 | 任务状态 |
|------|----------|----------|----------|
| `login_required` | 「登录查看完整内容」「未登录」 | ❌ 不重试 | `needs_attention` + 授权档案标记失效 |
| `risk_control` | 「环境存在异常」「访问频繁」「安全校验」`code:31/37` | ❌ 不重试 | `needs_attention` |
| `timeout` | 超出单任务超时 | ❌ 不重试 | `failed` |
| `cdp_unavailable` | CDP 连接失败 / 端口未监听 | ✅ 可重试 | `failed`（重试耗尽后）|
| `city_unresolved` | 城市无法识别 | ✅ 可重试 | `failed` |
| `unknown` | 其他 | ✅ 可重试 | `failed` |

> **为什么登录失效与风控不重试**：重试只会让账号更快被更严格地限制。
> 这两类失败会立刻停止并把任务标成"待处理"，等用户重新授权。

### 5.5 登录态复用（扫码授权）

```
POST /api/auth/sessions
   └─ 启动子进程：<python> boss_cdp_raw.py --setup-chrome --no-wait-login
        │            （HOME 指向隔离 profile 目录）
        ├─ 输出重定向到 auth-<session>.log，前端经 /api/ws/auth 实时看到日志
        ├─ 后台每 3s 轮询：CDP 端口是否监听 → 跑 --check 判断登录态
        │     ├─ 检测到登录态 → 抓取 profile 内的 Cookie 文件 → 加密打包 → 落库
        │     └─ 命中风控 → 立即失败并提示，避免密集重试
        └─ 超时（默认 300s）→ 标记失败，提示重新发起
```

登录态快照只打包 Cookie 相关文件（`Local State`、`Default/Cookies*`、
`Default/Network/Cookies*`），**不打包密码库、历史记录、扩展或完整 profile**。
快照加密后以 `0600` 权限落盘，并只保留最近 3 份。

> ⚠️ 扫码登录必须有图形界面。纯无头服务器请通过 X11 转发 / VNC，
> 或在宿主机完成一次扫码后把 `storage/chrome-profiles` 挂载进容器。
> 也支持 `copy_login_state=true` 从本机主浏览器导入 Cookie（需用户知情）。

---

## 6. Vue 3 前端核心组件

技术栈：Vue 3.5 + TypeScript 5.6 + Element Plus 2.9 + ECharts 5.5 + Pinia 2 + Vite 6。

### 6.1 概览卡片 `components/StatCard.vue`

```vue
<StatCard
  title="累计抓取岗位数"
  :value="cards.total_jobs"
  suffix="条"
  icon="Briefcase"
  tone="primary"
  :hint="`收藏 ${cards.favorite_jobs} 条`"
  :loading="store.loading"
  clickable
  @click="router.push('/jobs')"
/>
```

组件只负责展示统计口径，不含任何额度/计费类指标；`tone` 控制左侧色条与图标配色，
`clickable` 打开后支持点击跳转。

### 6.2 任务表单 `views/CreateTaskView.vue`

- 关键词（必填）、城市（可搜索可自定义的下拉）、薪资要求、经验/学历/公司规模（对应爬虫筛选码）、
  页数滑块（上限来自系统设置）、是否抓取详情、备注；
- 提交前若登录态无效会二次确认；
- 提交后同一页面变为**实时进度面板**：进度条 + 阶段文案 + 命中/新增/详情统计 + 滚动日志终端；
- 优先用 WebSocket 更新，同时保留 2s 轮询兜底。

### 6.3 结果列表与跳转 `views/JobsView.vue`

**跳转 BOSS 原页面的核心逻辑**：爬虫在列表接口里保留了 `encryptJobId`，据此生成
`https://www.zhipin.com/job_detail/{encryptJobId}.html`；工作台原样入库并在前端渲染按钮。

```ts
/** 精准跳转到 BOSS 直聘岗位详情页 */
function goBoss(job: Job) {
  const url = job.job_link
  if (!url) {
    ElMessage.warning('该岗位缺少原始链接，无法跳转')
    return
  }
  window.open(url, '_blank', 'noopener,noreferrer')
}
```

详情弹窗把 JD 全文分段渲染（`job_duty` → 「岗位职责」，`job_requirement` → 「岗位要求」，
缺失时回退整段 `jd`）：

```ts
const detailSections = computed(() => {
  const job = detailJob.value
  if (!job) return []
  const sections: { title: string; content: string }[] = []
  if (job.job_duty) sections.push({ title: '岗位职责', content: job.job_duty })
  if (job.job_requirement) sections.push({ title: '岗位要求', content: job.job_requirement })
  if (!sections.length && job.jd) sections.push({ title: '岗位详情', content: job.jd })
  return sections
})
```

### 6.4 图表封装 `components/ChartPanel.vue`

统一处理 `echarts.init` / `dispose` / `setOption`、`ResizeObserver` 自适应、
空态占位与 loading，业务侧只需传 `option`：

```vue
<ChartPanel
  :option="salaryOption"
  :empty="!(summary?.salary_distribution?.categories?.length)"
  empty-text="抓取岗位后展示薪资分布"
/>
```

工作台首页共 4 张图表：薪资分布（柱）、近 14 天新增趋势（面积折线）、
经验要求分布（环形）、城市分布 Top10（横向柱）。

### 6.5 实时通道 `composables/useTaskSocket.ts`

```ts
const { connected } = useTaskSocket((event) => {
  if (event.type === 'task.progress') {
    store.patchTask(event.data.task_id, { progress: event.data.progress, stage_text: ... })
  }
  if (event.type === 'task.finished') {
    store.loadTasks(); workbench.loadDashboard()
  }
})
```

`connected` 为 `false` 时页面降级为 HTTP 轮询；重连采用指数退避，最长 15s。

### 6.6 API 层 `api/http.ts`

自动拆封后端信封，业务代码直接拿 `data`；集中处理错误提示（422 参数校验、
404、5xx、后端未启动），并在 1.2s 内去重避免并发请求刷屏。文件下载单独处理
`Content-Disposition`，支持中文文件名（RFC 5987）。

---

## 6A. AI 对话模块

一个能**真正动手**的求职助手，而不是只会聊天的对话框。核心在于它可以调用
工作台自身的能力：检索本地岗位库、发起 BOSS 抓取任务、查询任务进度。

### 6A.1 三种意图模式

| 模式 | 用途 | 可用工具 |
|------|------|----------|
| `job_hunting` | 找岗位、发起抓取 | `search_existing_jobs`、`get_job_detail`、`trigger_boss_scraper`、`check_task_status`、`analyze_match` |
| `interview_coach` | 简历诊断、面试准备 | `get_job_detail`、`analyze_match` |
| `chat` | 普通问答 | `search_existing_jobs`、`get_job_detail` |

> **为什么抓取类工具只在 `job_hunting`**：它是有副作用的重操作（10-20 分钟，
> 且高频抓取会触发风控）。而读 JD 与生成诊断报告都是只读的，
> 所以在需要的模式里都放开。
>
> `analyze_match` 同时出现在 `job_hunting` 与 `interview_coach`：
> 「这个岗位适合我吗」很容易被路由成 `job_hunting`（因为提到了"岗位"），
> 如果只在面试辅导模式暴露，模型在那个模式下就只能凭摘要硬编分析。

模式由一次轻量的**意图路由**判定（`services/ai/router.py`）。这里有一个
刻意的成本取舍：**惰性路由** —— 只在首轮或模式未定时调模型判断，之后沿用。

> 每轮都路由会让 1.5s 延迟的模型明显变慢。代价是话题跳转时不会自动重判，
> 所以补了两个用户入口：对话页顶部的模式标签可点击切换；发送消息时可带
> `mode` 参数强制指定（传 `auto` 则清空重新判断）。

### 6A.2 工具调用循环

`services/ai/runner.py` 跑「模型 → 工具 → 模型」循环，最多 3 轮后强制收尾
（最后一轮不再把工具交给模型）。设计要点：

- **熔断**：实测模型确实会主动调工具，没有上限时可能反复查同一份数据
- **去重**：同一「工具 + 参数」重复调用直接跳过
- **失败不中断**：工具失败会把原因与建议回填给模型换策略
  （「库里没数据」应该促使它改用抓取，而不是抛异常给用户）
- **能力守卫前置**：模型缺 `vision` 但用户传了图 → 本地拦截并给出修复建议，
  而不是等 API 返回难懂的 400
- **token 跨轮累加**：否则多轮时只记下最后一轮的用量

### 6A.3 五个工具与实际行为

| 工具 | 说明 | 关键约束 |
|------|------|----------|
| `search_existing_jobs` | 查本地岗位库，毫秒级 | 城市名归一化（「深圳市」→「深圳」）；返回 0 条**不算错误**，让模型自己决定是否抓取 |
| `get_job_detail` | 读某个岗位的 JD 全文 | 摘要常驻、全文按需拉取；没有 JD 时明确禁止模型编造职责 |
| `trigger_boss_scraper` | 发起真实抓取（10-20 分钟） | 见下 |
| `check_task_status` | 查任务进度 | 用户问「好了吗」时模型必须用它，不许凭记忆答 |
| `analyze_match` | 简历 × JD 匹配度诊断 + STAR 面试题 | 见 6A.11 |

> **为什么摘要和全文要分开**：给模型看的岗位文本会进入对话历史、每轮重发，
> 所以列表里 JD 只保留 60 字；但用户问「这个岗位具体要什么」时需要原文。
> `get_job_detail` 就是这个按需拉取通道。注意它**在面试辅导模式下也可用**
> —— 分析简历与岗位匹配度时必须能读到目标 JD。

`trigger_boss_scraper` 是**有副作用**的重操作，因此约束最多：

1. **强制去重**：30 分钟内相同「关键词 + 城市」的活跃任务不重复创建。
   重复提交只会拿到重复数据，还白白增加触发风控的概率。
2. **页数硬上限 10 页**：模型很容易说"我抓 50 页给你"，那要跑几小时。
3. **登录态前置检查**：未登录时抓取必然失败，当场拒绝并提示去授权，
   而不是让用户等 20 分钟才看到失败。
4. **耗时明确告知**：任务卡与模型文本都会说明预计时长。
5. **只在 `job_hunting` 模式暴露**：避免在闲聊里被误调用。

### 6A.4 薪资只有粗档位（必须如实告知）

BOSS 直聘的薪资筛选不是自由区间，而是 6 个粗档位：
`3K以下 / 3-5K / 5-10K / 10-20K / 20-50K / 50K以上`。

用户说「15-25K」时无法精确表达。`services/ai/salary.py` 的策略是
**按用户给的下限落档**（宁可稍宽，不要漏掉他想要的机会），并把差异作为
明确提示交给用户：

> BOSS 直聘的薪资筛选只有粗档位……已按你给的下限 15K 选择「10-20K」档，
> 因此会包含低于 15K 的岗位。

这个提示同时出现在**任务卡片**和**给模型的文本**里，确保用户一定会看到 ——
否则他会疑惑"为什么给我推 45K 的岗位"。

### 6A.5 推理模型的两路输出

`deepseek-flash` 是推理模型，输出分两路：`reasoning_content`（思考过程）+
`content`（正式回答）。实测一次回答约产生 **68 个思考分片 + 9 个正文分片**
—— 思考占了大部分时间。因此：

- 适配器把两路分别成流（`StreamEventType.REASONING` / `TEXT`）
- 前端思考区**思考时展开、正文开始后自动折叠**，可点击回看
- 思考过程也落库，刷新页面后仍能展开

### 6A.6 SSE 流式

用 SSE 而非 WebSocket：单向下行、可复用现成的 Nginx 代理配置。

```bash
POST /api/chat/conversations/{id}/messages   # 返回 text/event-stream
```

事件类型：`meta` → `reasoning*` → `text*` → (`tool_call` → `tool_result`)*
→ `usage` → `done`；任何失败都转成 `error` 事件。

> **必须设置 `X-Accel-Buffering: no`**：少了这个响应头，Nginx 会把整个流
> 攒起来再一次性下发，首字延迟形同虚设。
>
> 前端**不能用 `EventSource`**（它只支持 GET，无法带 JSON body 和附件），
> 改用 `fetch` + `ReadableStream` 手工解析 SSE。

### 6A.7 附件处理

支持图片（简历截图）、PDF、Word、纯文本；图片 8MB / 文档 20MB / 单条最多 5 个。

两个关键设计：

- **按文件内容嗅探类型**，不信扩展名与声明的 MIME。实测把 PNG 改名成
  `resume.pdf` 仍被正确识别为图片 —— 按扩展名判断会让用户收到
  "PDF 解析失败"这种莫名错误。
- **上传时就抽取文本并缓存**。这是**必需而非优化**：图片只能放在 `user`
  消息里，多轮对话若每轮重传原图，请求体会迅速膨胀
  （base64 还要再膨胀约 33%，而上限是 48 MiB）。

删除会话时会**连带清理磁盘上的附件文件**（简历属敏感文件）。

### 6A.8 上下文瘦身（实测有效）

给模型看的岗位文本会进入对话历史、**每轮重发**。第一版把字段全部展开，
导致追问轮 prompt 从 1971 涨到 **5063 tokens、首字延迟 11 秒**。
改为只保留组织语言必需的字段（JD 摘要 60 字、技能取前 5、**保留链接**）后
降到 **3826 tokens、首字 5.4 秒**。

链接必须保留：用户问「给我链接」时模型要能直接给出，
否则它会编造一个 URL —— 这是最容易被忽视的幻觉来源。

### 6A.9 模型管理

支持多模型并存与动态切换。`services/llm/` 用适配器模式：
`LLMAdapter` 抽象基类 → `OpenAICompatAdapter` → `DeepSeekAdapter`。

- API Key 用 `cryptography` 加密存储，接口**只返回提示串**（`sk-****abcd`）
- 客户端 LRU 缓存，键含 `updated_at` 与密钥指纹，改配置立即失效
- 8 类错误各自带可操作的修复建议（不是"请求失败"这种无用信息）
- 模型下拉框显示**能力标签与实测延迟**：实测两个模型延迟差 8 倍
  （1.5s vs 12.4s），只显示名字会让用户切到慢模型时以为程序卡死

### 6A.10 任务进度实时推送

抓取要跑 10-20 分钟，用户会一直盯着对话看进度。所以新增了一条
**对话级** WebSocket 通道：

```
/ws/chat/{会话 id}    # 只推该会话发起的任务
```

与 `/ws/tasks`（全局广播）的区别：用户可能开着多个对话，
全局广播会让他在每个对话里都收到不相干的任务消息。

**关键问题：任务事件里只有 `task_id`，没有 `conversation_id`。**
两种解法：

1. 在 `queue_service` 的 15 处 `publish_task_event` 调用里都带上会话 id
   —— 侵入性太大，且那些调用点大多拿不到会话上下文
2. **在中继里按 task_id 反查**（本模块采用）

选 2：`ScrapeTask.conversation_id` 一旦写入就不变，所以每个任务只需查一次，
之后走内存缓存。中继也就与 `queue_service` 解耦了 —— 它不需要知道有哪些
事件类型，原样转发即可。

> ⚠️ **踩过的坑：不要缓存 `None`**
>
> 缓存「查不到归属」看似能省一次查询，实际会**永久屏蔽**之后任何归属变更：
> 如果任务在首次被查到归属前还没有 `conversation_id`，缓存下来的 `None`
> 就再也不会刷新 —— 表现为「任务明明归属这个对话，却永远收不到推送」，
> 而且日志里显示 `归属会话=None`，极容易误判成路由逻辑写错。
>
> 现在只缓存**正向结果**（确实是某个对话发起的），负结果每次重查。
> 代价是非对话发起的任务每次事件多一次轻量查询 —— 这类任务本来就少，
> 而且事件有节流，可以接受。

其余设计：

- **节流**：进度类事件至少间隔 3 秒；`task.started` / `task.finished` 不节流
  （用户必须立刻看到状态变化）
- **推的是快照而非事件载荷**：收到事件后重新查库，下发"现在是什么状态"。
  这样前端拿到的状态一定与数据库一致，不会因为节流丢事件而错乱
- **慢客户端丢最旧的**：队列满时丢最旧一条而不是无限增长，保证它还能看到最新状态
- **中继绝不因单个事件退出**：否则之后所有会话都收不到推送，
  而且用户完全看不出问题在哪里
- **前端断线指数退避重连**（最多 15s），连接失败时进度卡片仍可手动跳转历史页

### 6A.11 匹配度诊断与 STAR 面试题（模式二）

`analyze_match` 的产出是一份**结构化报告**：四个维度的结论、风险点、
5 道带 STAR 四段结构的面试题。前端渲染成可逐题准备的卡片。

**为什么这个工具要自己在内部再调一次模型**：普通工具只做数据读写，
但报告的固定结构是"能用"的前提 —— 让主模型用自由文本输出的话，
每轮格式都不一样，用户没法逐题准备，也没法一眼看出哪个维度是短板。
所以这里用一次**带 JSON 约束的独立调用**产出结构化结果，
主模型拿到后只做人话导读（prompt 里明确要求"不要逐条复述"）。

简历按优先级查找，找不到就明确让模型去问用户，**不要猜**：

1. 本轮消息附带的文档附件
2. 会话历史里最近一份已抽取文本的简历附件
3. 用户粘贴的简历文本（由主模型直接处理，不走此工具）

> 图片简历**不适用**：其内容不在 `extracted_text` 里（走多模态输入），
> 工具会明确提示改用文档或粘贴文本。

#### ⚠️ 踩过的坑：报告被 max_tokens 截断

实测一份完整报告（4 维度 + 风险点 + 5 道带完整 STAR 的题）很容易超过
6000 token。第一版把上限设成 4096，结果是 `finish_reason=length`、
**JSON 被硬截断、整份报告解析失败** —— 用户看到的是"格式异常"，而不是报告。

两道防线：

1. **上限提到 8192**，并在 prompt 里做**篇幅控制**（每段字数上限、
   题目数固定 5 道）。实测这一项把输出压到上限以内，报告正常完成。
2. **解析时按截断修复**（`_repair_truncated`）：回退到最后一个完整的
   逗号分隔点、丢掉那个写了一半的元素、补齐括号。
   实测能从被截断的输出里救回四个维度、全部风险点、前面几道
   STAR 完整的题 —— 远好过"什么都没有"。

另外：解析彻底失败时 `overall_score` 用 **`null` 而不是 0**。
0 是一个真实可能的评分（完全不匹配），显示出来会让用户以为模型真给了 0 分 ——
实测主模型不得不主动纠正这一点（"卡片的 0 分是解析失败的占位值"），
说明这个占位值本身就在误导人。前端对 `null` 显示「无评分」。

---

## 7. 部署与运行说明

### 7.1 环境要求

| 组件 | 版本 | 说明 |
|------|------|------|
| Python | ≥ 3.10（推荐 3.12）| 后端与爬虫共用 |
| Node.js | ≥ 18（推荐 22）| 前端构建 |
| Chrome 或 Edge | 最新版 | 抓取与扫码登录必需 |
| 数据库 | SQLite（默认）/ PostgreSQL 14+ / MySQL 8+ | 见下方切换说明 |
| Redis | 可选 | 单机部署不需要；多实例横向扩展时用于共享状态 |

> `scripts/` 下的脚本用 Windows PowerShell 5.1 与 PowerShell 7 均可运行
> （`powershell` 或 `pwsh` 命令皆可，本机若只装了 5.1 就用 `powershell`）。

### 7.2 快速开始（本地开发，5 步）

```powershell
# 0) 进入项目根目录
cd D:\Project\Boss

# 1) 拉取爬虫（如尚未拉取）
git clone https://github.com/eatmoreduck/boss-zhipin-scraper.git

# 2) 创建后端虚拟环境并安装依赖
python -m venv .venv-backend
.\.venv-backend\Scripts\python.exe -m pip install -r backend\requirements.txt

# 3) 准备配置
Copy-Item backend\.env.example backend\.env
#    按需修改；生产环境务必替换 BZP_SECRET_KEY

# 4) 环境自检（会告诉你缺什么）
powershell -ExecutionPolicy Bypass -File scripts\check-env.ps1

# 5) 启动后端（新终端）与前端（另一个新终端）
powershell -ExecutionPolicy Bypass -File scripts\start-backend.ps1
powershell -ExecutionPolicy Bypass -File scripts\start-frontend.ps1
```

> **为什么要加 `-ExecutionPolicy Bypass`**：Windows 默认执行策略（`Restricted`）会拒绝运行
> 未签名的 `.ps1` 脚本，报「未对文件进行数字签名」。加这个参数只对本次进程生效，
> 不会永久改动你的系统策略。
>
> 如果不方便改策略，也可以绕开脚本直接起服务（等价写法见
> [7.2.1 手动启动](#721-不使用脚本手动启动)）。

访问 <http://127.0.0.1:5173> 打开工作台；后端接口文档在
<http://127.0.0.1:18080/docs>。

> **为什么默认端口是 18080 而不是 8000**
>
> 8000 是"约定俗成"的端口，很多机器上已经被其他应用占用。一旦冲突，
> 后端会起不来；更麻烦的情况是后端起来了但前端代理打到了那个应用上，
> 于是**每个接口都返回 404「资源不存在」**——现象上极像"接口没注册"或
> "后端没启动"，排查方向会被完全带偏。
>
> 前后端脚本的默认值已经统一到 18080，`backend/.env` 的 `BZP_PORT` 也一并改好。
> 如果你想换端口，**三处必须一起改**：
> 1. `scripts\start-backend.ps1 -Port <端口>`
> 2. `scripts\start-frontend.ps1 -ApiTarget http://127.0.0.1:<端口>`
> 3. `backend\.env` 的 `BZP_PORT=<端口>`
>
> 端口占用情况可以先探测一下：
> ```powershell
> Test-NetConnection 127.0.0.1 -Port 18080 -InformationLevel Quiet
> ```

#### 7.2.1 不使用脚本手动启动

脚本只是把环境变量和参数拼好，完全等价的手动写法：

```powershell
# 后端（在 backend 目录下）
cd backend
$env:PYTHONIOENCODING = 'utf-8'   # 让爬虫的中文输出在 Windows 控制台不乱码
$env:PYTHONUTF8 = '1'
..\.venv-backend\Scripts\python.exe -m uvicorn bzp_workbench.main:app --host 127.0.0.1 --port 18080 --reload

# 前端（在 frontend 目录下）
cd frontend
$env:VITE_API_TARGET = 'http://127.0.0.1:18080'
npx vite
```

生产建议把 `--host` 保持在内网地址，由 Nginx 对外；并去掉 `--reload`。

### 7.3 首次使用：完成一次扫码授权

推荐用仓库自带的启动器，它会用正确的参数（远程调试端口 + 隔离 profile）拉起浏览器
并自动校验端口是否就绪：

```
双击运行  scripts\start-boss-browser.bat
```

<details>
<summary>启动器具体做了什么 / 手动命令</summary>

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start-boss-browser.ps1 [-Port 9222] [-UseEdge]
```

它等价于下面这条手工命令：

```powershell
# 先问后端要「当前授权档案真正的 profile 目录」——
# 千万不要自己拼一个路径，原因见下方说明。
$dir = (Invoke-WebRequest http://127.0.0.1:18080/api/auth/status -UseBasicParsing |
        ConvertFrom-Json).data.profile.profile_dir

& "C:\Program Files\Google\Chrome\Application\chrome.exe" `
  --remote-debugging-port=9222 `
  --user-data-dir="$dir" `
  --no-first-run --no-default-browser-check --remote-allow-origins=* `
  --disable-crash-reporter `
  "https://www.zhipin.com"
```

> ⚠️ **`--user-data-dir` 必须用后端返回的那个目录**。
>
> 工作台给每个授权档案分配一个按 `profile_hash` 命名的独立目录
> （`chrome-profiles/<hash>`），而不是固定的名字。如果手工写死成别的路径
> （例如 `chrome-profiles\.boss-zhipin-scraper\chrome-profile`），
> Chrome 会把登录态存到那个目录，而后端去自己的目录里读 —— 结果是
> **扫码看起来成功了，但「校验登录态」永远显示「登录态无效」**。
> 这个症状极像扫码失败，实际上问题在路径不一致。
>
> `scripts\start-boss-browser.ps1` 已经改成自动向后端查询该路径，用脚本就不会踩到。
>
> ⚠️ 必须在**普通 Windows 环境**（双击 / 普通终端）里启动。若在受限运行环境
> （沙箱、高权限管控终端）里启动，Chrome 会因无法创建崩溃收集进程而立刻自杀，
> 调试端口永远打不开，工作台就会一直显示「浏览器未启动」。

</details>

然后在弹出的浏览器窗口里登录 BOSS 直聘，回到工作台：

1. 左侧「账号授权」→ 点 **「校验登录态」**；
2. 状态变为绿色「已授权」即成功；
3. 回到「创建抓取任务」提交第一个任务。

专用浏览器使用隔离 profile（目录名形如
`backend/storage/chrome-profiles/<profile_hash>`，可调用
`/api/auth/status` 的 `profile_dir` 字段查到确切路径），
重启机器后登录态依然保留，也不影响你日常使用的浏览器。用完可执行：

```bash
<python> boss-zhipin-scraper/scripts/boss_cdp_raw.py --stop-chrome
```

### 7.4 数据库切换

默认 SQLite，开箱即用。切换到生产数据库只需改一个环境变量：

```dotenv
# PostgreSQL
BZP_DATABASE_URL=postgresql+asyncpg://boss:password@127.0.0.1:5432/boss_workbench

# MySQL 8
BZP_DATABASE_URL=mysql+aiomysql://boss:password@127.0.0.1:3306/boss_workbench
```

并安装对应驱动（`pip install asyncpg` 或 `pip install aiomysql`）。
建表有两种方式：

- **自动**：后端启动时 `Base.metadata.create_all` 自动建表（默认）；
- **手工 / Alembic**：执行 `deploy/sql/schema_postgresql.sql` 或 `schema_mysql.sql`。

### 7.5 Docker Compose 部署

```bash
docker compose up -d --build
# 前端：http://localhost:8080
# 后端文档（仅本机）：http://127.0.0.1:18080/docs
```

Compose 会拉起 PostgreSQL 16（并自动执行建表脚本）、后端（含 Chromium 与中文字体）、
前端 Nginx。

**容器内扫码登录**需要图形界面，两种做法：

1. Linux 桌面：取消 `docker-compose.yml` 中 `DISPLAY` 与 `/tmp/.X11-unix` 的注释；
2. 更推荐：**在宿主机完成一次扫码**，然后把 profile 目录挂载进容器：

```yaml
volumes:
  - ./backend/storage/chrome-profiles:/app/backend/storage/chrome-profiles
```

### 7.6 生产部署要点

| 项目 | 建议 |
|------|------|
| `BZP_SECRET_KEY` | 必须替换：`python -c "import secrets;print(secrets.token_urlsafe(32))"` |
| `BZP_DEBUG` | `false` |
| `BZP_CORS_ORIGINS` | 收紧到实际前端域名 |
| `BZP_HOST` | 生产建议只监听内网，由 Nginx 对外 |
| 进程管理 | `uvicorn` 单进程即可（任务队列与 WebSocket 是进程内状态）；多 worker 会导致队列重复消费 |
| 反向代理 | 必须为 `/api/ws/` 开启 `Upgrade`/`Connection` 头并放大 `proxy_read_timeout` |
| 数据目录 | `backend/storage` 需要持久化；`chrome-profiles` 含登录态，注意权限 |
| 备份 | 备份数据库 + `storage/chrome-profiles`（登录态）+ `storage/auth` |

### 7.7 Git 推送故障排查（Windows / 受限网络）

按下面顺序排查，**先定位到底是哪一层坏了**，不要盲目改配置。

#### 第 1 步：确认这个 Git 构建支持哪些 TLS 后端

```powershell
git -c http.sslBackend=bogus ls-remote origin   # 故意传错值
# 输出会列出可用后端，例如：
#   fatal: Unsupported SSL backend 'bogus'. Supported SSL backends:
#           schannel
```

如果你的 Git **只支持 `schannel`**（Git for Windows 默认如此），那么
任何 `http.sslBackend=openssl` 的写法都是无效的 —— 它会被拒绝或忽略。
网上大量"换成 openssl"的建议在这个构建上并不成立。

#### 第 2 步：区分是 DNS 问题还是 TLS 问题

```powershell
# a) 看解析到哪个 IP
Resolve-DnsName github.com -Type A | Select-Object IPAddress

# b) 换一个已知可用的 IP 再试（绕过 DNS）
git -c "http.curloptResolve=github.com:443:140.82.113.3" ls-remote origin
```

- 报 `Could not connect ... after 21s` → **IP 被阻断**，换 IP 可能有效；
- 报 `Recv failure: Connection was reset` → **TLS 层被拦截**（按 SNI 特征），
  换 IP 无效，跳到第 3 步。

判断技巧：用 Python 的 SSL 栈访问同一个地址。若能通而 git 不通，
基本可以确认是**针对 git TLS 指纹的拦截**，而不是网络整体不可用：

```powershell
python -c "import urllib.request; print(urllib.request.urlopen('https://github.com/xiaoshuang9527/AIBoss.git/info/refs?service=git-upload-pack', timeout=20).status)"
```

#### 第 3 步：改用 SSH（TLS 被拦截时的唯一出路）

`github.com:22` 走的是 SSH 协议，不经过 TLS，通常不会被同样的规则拦掉。

```powershell
# 1) 把远端切到 SSH
git remote set-url origin git@github.com:<用户名>/<仓库>.git

# 2) 确认 SSH 可达（这一步不需要认证）
ssh -T git@github.com
```

**⚠️ 密钥文件名不是默认值时，git 不会自动选用**，必须显式指定：

```powershell
# 症状：ssh -T git@github.com 能成功，但 git push 报 Permission denied (publickey)
# 原因：git 只自动尝试 id_rsa / id_ed25519，不会尝试 id_ed25519_agent 这类自定义名
git config core.sshCommand "ssh -i ~/.ssh/id_ed25519_agent -o IdentitiesOnly=yes"
```

把 `core.sshCommand` 写进**仓库本地配置**（不加 `--global`）即可，
这样只影响这个仓库，不动你的全局设置。

#### 附：HTTPS 凭据相关的报错

| 报错 | 含义与处理 |
|------|-----------|
| `schannel: AcquireCredentialsHandle failed: SEC_E_NO_CREDENTIALS` | 系统证书存储/凭据异常，或运行在受限环境里。优先按上面第 1–3 步排查 |
| `could not read Username for 'https://github.com'` | 本机没有该站点的凭据。`cmdkey /list \| findstr github` 应有 `git:https://<用户名>@github.com` 条目；否则在能弹浏览器的终端里执行一次 `git push` 走 Git Credential Manager 完成授权 |
| `terminal prompts disabled` | 设置了 `GIT_TERMINAL_PROMPT=0` 且没有已存凭据 |

### 7.8 常见问题

| 现象 | 原因 / 处理 |
|------|-------------|
| 运行 `.ps1` 报「未对文件进行数字签名」 | Windows 默认执行策略禁止未签名脚本 → 用 `powershell -ExecutionPolicy Bypass -File scripts\start-backend.ps1`，或按 7.2.1 手动启动 |
| `pwsh : 无法将"pwsh"项识别为...` | 本机只装了 Windows PowerShell 5.1，没有 PowerShell 7 → 用 `powershell` 代替 `pwsh`；脚本本身兼容 5.1 |
| `.ps1` 报一堆「Missing closing '}'」「字符串缺少终止符」，中文变成乱码 | **文件缺少 UTF-8 BOM**，Windows PowerShell 5.1 会按 GBK 解析导致中文被截断、连接符被吞。用「另存为 UTF-8 with BOM」重新保存；本仓库的脚本已带 BOM，编辑后请勿丢 |
| `.bat` 双击后一闪而过、什么都没发生 | 批处理里 `\|`、`()`、引号容易被 cmd.exe 误解析 → 本仓库的 `.bat` 只做转发，真正逻辑在 `.ps1`；自己写批处理时把复杂逻辑放 `.ps1` |
| 双击 `.bat` 弹「无法验证发布者」安全警告 | 未签名的本地脚本都会触发，点「运行」即可；介意的话可右键文件 → 属性 → 勾选「解除锁定」 |
| 启动 Vite 报 `Error: spawn EPERM` | 沙箱或安全软件禁止 Node 创建子进程，而 Vite 需要启动 esbuild 加载配置。改用生产构建 `npm run build` 后由静态服务器托管，或在允许子进程的终端里运行 |
| 构建报 `esbuild-xxx: Access is denied` | esbuild 的临时目录不在可写范围 → 把 `TEMP`/`TMP` 指向可写目录后再构建，例如 `$env:TEMP='D:\Project\Boss\.tmp'` |
| `npm install` 报 `EPERM ... npm-cache` | 全局 npm 缓存在受限目录 → 指定工作区内缓存：`npm install --cache .npm-cache` |
| 浏览器「打开后一闪而过」，CDP 端口起不来 | Chrome 无法启动崩溃收集子进程（`crashpad ... OpenProcess 拒绝访问` 后 `self-terminating`）→ 在**普通 Windows 环境**里运行 `scripts\start-boss-browser.bat`（双击即可），不要在受限终端里启动 |
| 「校验登录态」通过但实际没登录 | 早期版本先看 `--check` 返回码再看文本，会把「CDP 通了但未登录」误判成已登录（已修复并加回归测试）；若仍遇到请查看任务日志里的爬虫原始输出 |
| 任务报「无法连接 Chrome CDP 调试端口」 | 专用浏览器未启动或未登录 → 运行 `scripts\start-boss-browser.bat` 并登录 |
| 任务状态「待处理」并提示登录态失效 | Cookie 过期，重新扫码即可；系统不会盲目重试 |
| 提示「触发 BOSS 直聘风控校验」 | 抓取过频。停止 30 分钟以上，并把并发降到 1、间隔调到 8-15 秒 |
| 任务卡在「进行中」 | 服务被强杀导致状态未收尾；点「系统设置 → 扫描异常任务」或等定时维护（120s）|
| 前端能打开但接口 404 | 后端未启动或端口不一致，检查 `-ApiTarget` 与浏览器 Network |
| **每个接口都弹「资源不存在」，页面到处报错** | 这是 Vite **代理打到了别的应用**上：`VITE_API_TARGET` 没生效时配置会退回默认的 `127.0.0.1:8000`，而该端口常被其他服务占用，对未知路径一律返回 404。**注意它看起来极像"后端没启动"，但后端其实是好的**。三步确认：① `Test-NetConnection 127.0.0.1 -Port 18080` 应为 `True`；② 直接访问 `http://127.0.0.1:18080/api/system/health` 应返回 `{"code":0,...}`；③ 检查 `frontend/.env.local` 的 `VITE_API_TARGET`。另外 `vite.config.ts` **必须用 `loadEnv()` 读取**，Vite 不会把 `.env.local` 注入 `process.env`，写成 `process.env.VITE_API_TARGET` 会被静默忽略 |
| 扫码后「校验登录态」始终显示「登录态无效」 | 启动浏览器用的 `--user-data-dir` 与后端读取的目录不一致。profile 目录名是按 `profile_hash` 生成的（`chrome-profiles/<hash>`），**不要手工拼路径**；用 `scripts\start-boss-browser.ps1`（它会向后端查询），或读 `/api/auth/status` 的 `profile_dir` 字段 |
| 前端页面卡在"轮询模式" | WebSocket 被反代拦截，检查 Nginx 的 `Upgrade` 头配置 |
| 容器内无法启动浏览器 | 无图形界面，改在宿主机扫码后挂载 profile 目录 |
| Windows 控制台日志乱码 | 设置 `PYTHONIOENCODING=utf-8`（启动脚本已自动处理）|

---

## 8. 关键设计权衡与注意事项

### 8.1 登录态复用与安全隔离

- 每个授权档案对应一个**独立隔离 profile**，绝不读写用户主浏览器数据；
- Cookie 快照加密落盘 + 权限收紧，数据库只存路径与指纹；
- 提供 `copy_login_state` 显式导入通道，但默认关闭并在 UI 标注风险。

### 8.2 异步与并发控制

爬虫是耗时操作，必须异步执行。工作台用 **`asyncio` + 固定大小工作槽**，而不是 Celery：

- 抓取任务的本质是"启动一个浏览器 CDP 子进程并等待"，属 I/O 等待而非 CPU 密集；
- 单机部署无需额外 broker，减少运维面；
- 需要多机扩展时，把 `TaskQueue` 内部换成 Celery `send_task` 即可，**路由层不用改**。

同时最多执行 2 个任务（可配 1-4），任务之间插入 3-5 秒随机延迟，页数硬上限 10 页。

### 8.3 反爬对抗与失败重试

- 只做**被动捕获**（旁听页面自身发出的搜索接口响应），不注入额外请求，不绕过验证码；
- 登录失效与风控拦截**不重试**，直接标为"待处理"，避免加重限制；
- 网络抖动/超时才重试，退避 5s、10s…最长 30s。

### 8.4 前端交互体验

WebSocket 为主 + HTTP 轮询兜底，双保险保证用户始终能看到进度；
断线指数退避重连；图表、表格、日志终端都做了空态与加载态。

### 8.5 合规与提示

- 布局顶部常驻免责声明条（可"不再提示"，但侧边栏常驻入口随时可查）；
- 独立「免责声明与合规」页面，涵盖工具性质、数据用途、抓取频率、登录态隐私、责任划分五节；
- 系统设置页显式声明"不存在任何额度、套餐、充值或计费功能"。

---

## 9. 测试

后端测试**完全不需要 Chrome 和网络**：`tests/fake_scraper/` 复刻了真实爬虫的
命令行契约与 JSON 输出格式，因此调度链路（子进程 → 日志 → 结果解析 → 入库 →
导出）可以被完整验证。

```powershell
cd backend
..\.venv-backend\Scripts\python.exe -m pytest -v
```

当前共 **80 个测试**，全部通过（`80 passed`）：

- `test_parsing.py`（35 项）：薪资区间解析（`20-40K`、`15-25K·13薪`、`8-12万`、
  `300-500元/天`、`面议`、`20K以上`）、薪资分桶、用户期望薪资解析、JD 拆分
  （岗位职责 / 岗位要求）、技能词提取、时间与 JSON 工具函数；
- `test_scraper_runner.py`（22 项）：失败归类（登录失效 / 风控 / 超时 / CDP / 城市 /
  未知，以及风控优先于登录的判定）、结果归一化（薪资、城市、经验学历、技能标签、
  详情合并、`job_id` 兜底、空值处理）、`HOME` 重定向到隔离 profile、爬虫发现与版本读取；
- `test_api.py`（23 项）：概览接口结构、**禁用词扫描**（确保响应中不含额度/计费/套餐字样）、
  爬虫健康检查、任务创建校验与页数夹取、完整抓取流程（2 页 × 3 条 → 入库 6 条 →
  校验跳转链接 / JD 全文 / 技能标签 / 薪资归一化）、跨任务去重、登录失效与风控归类、
  空结果处理、重试与取消、授权接口、系统设置夹取、日志、筛选面板、导出。

前端质量检查：

```powershell
cd frontend
npx vue-tsc --noEmit     # 类型检查
npx vite build           # 生产构建
```

---

## 第三方依赖与来源说明

本仓库包含两套**相互独立**的代码，请务必区分：

| 组件 | 来源 | 许可 | 职责 |
|------|------|------|------|
| `boss-zhipin-scraper/` | [eatmoreduck/boss-zhipin-scraper](https://github.com/eatmoreduck/boss-zhipin-scraper) **v2.3.0** | MIT | **数据采集的全部实现**：CDP 连接、请求旁听、无限滚动翻页、JD 提取、登录态探测、请求节奏控制 |
| 工作台（本仓库其余部分） | 本项目 | 见下方 License | 任务调度、并发与重试、结果入库、界面展示、导出。**不含任何采集逻辑** |

> **为什么 `boss-zhipin-scraper/` 是内联副本（vendored）而不是 git submodule**
>
> 这样 `git clone` 下来即可直接运行，不需要额外初始化子模块，也不需要二次访问 GitHub。
> 它是上游代码，<u>请勿在本仓库里修改它</u>；升级方式是从上游重新拉取对应版本覆盖该目录。
>
> 工作台只通过它的**命令行接口**调用（`scripts/boss_cdp_raw.py --keyword ... --city ...`），
> 不 import 其内部函数，因此上游重构内部实现不会影响工作台，只要 CLI 参数与
> 输出 JSON 结构不变即可。

工作台的合规约束与上游保持一致：只做**被动请求旁听**，不做指纹伪造、验证码绕过
或代理池轮换；遇到验证码提示用户人工处理。

---

## License

本工作台代码仅供个人学习与求职辅助使用。上游 `boss-zhipin-scraper` 采用 MIT 协议，
其免责声明同样适用于本项目的抓取行为，详见
[boss-zhipin-scraper/README.md](./boss-zhipin-scraper/README.md)。
