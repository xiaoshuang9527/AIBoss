# ============================================================
# 启动后端（开发模式，带热重载）
# 用法：powershell -ExecutionPolicy Bypass -File scripts\start-backend.ps1 [-Port 18080]
#
# 关于默认端口
#   刻意不用 8000：它是"约定俗成"的端口，很多机器上已被别的应用占用。
#   一旦被占用，后端会启动失败（或更糟：前端把请求代理到那个应用，
#   收到一堆 404「资源不存在」），现象上很像"接口没注册"，很难查。
#   18080 与前端脚本 scripts\start-frontend.ps1 的默认代理目标保持一致。
# ============================================================
param(
    [int]$Port = 18080,
    [switch]$NoReload
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root 'backend'
$python = Join-Path $root '.venv-backend/Scripts/python.exe'

if (-not (Test-Path $python)) {
    Write-Host "未找到虚拟环境：$python" -ForegroundColor Red
    Write-Host "请先执行：" -ForegroundColor Yellow
    Write-Host "  python -m venv .venv-backend"
    Write-Host "  .\.venv-backend\Scripts\pip install -r backend\requirements.txt"
    exit 1
}

if (-not (Test-Path (Join-Path $backend '.env'))) {
    Copy-Item (Join-Path $backend '.env.example') (Join-Path $backend '.env')
    Write-Host "已从 .env.example 生成 backend/.env，请按需修改。" -ForegroundColor Yellow
}

# 让爬虫子进程的中文输出在 Windows 控制台不乱码
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'

$arguments = @('-m', 'uvicorn', 'bzp_workbench.main:app', '--host', '127.0.0.1', '--port', "$Port")
if (-not $NoReload) { $arguments += '--reload' }

Write-Host "启动后端：http://127.0.0.1:$Port" -ForegroundColor Green
Write-Host "接口文档：http://127.0.0.1:$Port/docs" -ForegroundColor DarkGray
Write-Host ""

Push-Location $backend
try {
    & $python @arguments
} finally {
    Pop-Location
}
