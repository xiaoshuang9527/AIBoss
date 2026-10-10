# ============================================================
# 智能求职辅助工作台 · 一键环境自检
# 用法（在项目根目录）：
#   pwsh -File scripts/check-env.ps1
# ============================================================
$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
$ok = 0
$warn = 0
$bad = 0

function Write-Check($status, $label, $detail) {
    switch ($status) {
        'OK'   { $script:ok++;   Write-Host "[ OK ]  $label" -ForegroundColor Green }
        'WARN' { $script:warn++; Write-Host "[WARN]  $label" -ForegroundColor Yellow }
        'FAIL' { $script:bad++;  Write-Host "[FAIL]  $label" -ForegroundColor Red }
    }
    if ($detail) { Write-Host "        $detail" -ForegroundColor DarkGray }
}

Write-Host "`n=== 智能求职辅助工作台 · 环境自检 ===`n" -ForegroundColor Cyan

# ---------- 目录结构 ----------
$scraperDir = Join-Path $root 'boss-zhipin-scraper'
$scraperEntry = Join-Path $scraperDir 'scripts/boss_cdp_raw.py'
if (Test-Path $scraperEntry) {
    Write-Check 'OK' '爬虫主脚本存在' $scraperEntry
} else {
    Write-Check 'FAIL' '未找到爬虫主脚本' "期望路径: $scraperEntry（请先拉取 boss-zhipin-scraper）"
}

if (Test-Path (Join-Path $scraperDir 'data/city_codes.json')) {
    Write-Check 'OK' '城市码表存在' ''
} else {
    Write-Check 'FAIL' '缺少城市码表 data/city_codes.json' ''
}

# ---------- Python ----------
$venvPython = Join-Path $root '.venv-backend/Scripts/python.exe'
if (Test-Path $venvPython) {
    $version = & $venvPython --version 2>&1
    Write-Check 'OK' '后端虚拟环境存在' "$venvPython ($version)"

    # 关键依赖
    $deps = @('fastapi', 'uvicorn', 'sqlalchemy', 'aiosqlite', 'cryptography', 'pydantic', 'requests', 'websocket')
    $missing = @()
    foreach ($dep in $deps) {
        & $venvPython -c "import $dep" 2>$null
        if ($LASTEXITCODE -ne 0) { $missing += $dep }
    }
    if ($missing.Count -eq 0) {
        Write-Check 'OK' '后端依赖完整' ($deps -join ', ')
    } else {
        Write-Check 'FAIL' '缺少后端依赖' (($missing -join ', ') + "  ->  .\.venv-backend\Scripts\pip install -r backend\requirements.txt")
    }
} else {
    Write-Check 'FAIL' '未找到后端虚拟环境' '请执行: python -m venv .venv-backend'
}

# ---------- 前端 ----------
$nodeModules = Join-Path $root 'frontend/node_modules'
if (Test-Path $nodeModules) {
    Write-Check 'OK' '前端依赖已安装' ''
} else {
    Write-Check 'WARN' '前端依赖未安装' '请执行: cd frontend; npm install --ignore-scripts'
}
if (Test-Path (Join-Path $root 'frontend/dist/index.html')) {
    Write-Check 'OK' '前端已构建（dist 存在）' ''
} else {
    Write-Check 'WARN' '前端尚未构建' '开发模式可忽略；生产请执行 npm run build'
}

# ---------- 浏览器 ----------
$chromePaths = @(
    "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe",
    "$env:PROGRAMFILES\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
    "$env:LOCALAPPDATA\Microsoft\Edge\Application\msedge.exe",
    "$env:PROGRAMFILES\Microsoft\Edge\Application\msedge.exe"
)
$browser = $chromePaths | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($browser) {
    Write-Check 'OK' '检测到浏览器（抓取与扫码登录需要）' $browser
} else {
    Write-Check 'WARN' '未检测到 Chrome / Edge' '抓取前需要安装浏览器并完成一次扫码登录'
}

# ---------- CDP 端口 ----------
$cdpOpen = Test-NetConnection 127.0.0.1 -Port 9222 -InformationLevel Quiet -WarningAction SilentlyContinue
if ($cdpOpen) {
    Write-Check 'OK' 'CDP 调试端口 9222 已监听' '专用浏览器正在运行'
} else {
    Write-Check 'WARN' 'CDP 调试端口 9222 未监听' '到「账号授权」页点击「开始授权」即可启动'
}

# ---------- 配置文件 ----------
$envFile = Join-Path $root 'backend/.env'
if (Test-Path $envFile) {
    Write-Check 'OK' '后端配置文件存在' $envFile
    $secret = Select-String -Path $envFile -Pattern '^BZP_SECRET_KEY=(.+)$' |
        Select-Object -First 1
    if ($secret -and $secret.Matches[0].Groups[1].Value -match 'change|please|dev-only') {
        Write-Check 'WARN' 'BZP_SECRET_KEY 仍是示例值' '生产环境请改成随机值'
    }
} else {
    Write-Check 'WARN' '缺少 backend/.env' '请从 backend/.env.example 复制一份'
}

# ---------- 汇总 ----------
Write-Host "`n=== 自检结果：通过 $ok 项，警告 $warn 项，失败 $bad 项 ===`n" -ForegroundColor Cyan
if ($bad -gt 0) {
    Write-Host "存在必须修复的问题，请先处理上面标记为 FAIL 的条目。" -ForegroundColor Red
    exit 1
}
Write-Host "环境基本就绪。启动后端：pwsh -File scripts/start-backend.ps1" -ForegroundColor Green
exit 0
