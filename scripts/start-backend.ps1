# ============================================================
# 启动后端（开发模式，带热重载）
# 用法：pwsh -File scripts/start-backend.ps1 [-Port 8000]
# ============================================================
param(
    [int]$Port = 8000,
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
