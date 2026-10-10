# ============================================================
# 启动前端开发服务器（Vite）
# 用法：pwsh -File scripts/start-frontend.ps1 [-ApiTarget http://127.0.0.1:8000]
# ============================================================
param(
    [string]$ApiTarget = 'http://127.0.0.1:8000'
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$frontend = Join-Path $root 'frontend'

if (-not (Test-Path (Join-Path $frontend 'node_modules'))) {
    Write-Host "前端依赖未安装，正在安装（使用 npmmirror 加速）..." -ForegroundColor Yellow
    Push-Location $frontend
    try {
        $env:npm_config_registry = 'https://registry.npmmirror.com/'
        # --ignore-scripts：部分受限环境禁止 postinstall 启动子进程，
        # esbuild 的平台二进制来自 optionalDependencies，无需 postinstall
        npm install --no-audit --no-fund --ignore-scripts
    } finally {
        Pop-Location
    }
}

$env:VITE_API_TARGET = $ApiTarget

Write-Host "启动前端：http://127.0.0.1:5173" -ForegroundColor Green
Write-Host "API 代理目标：$ApiTarget" -ForegroundColor DarkGray
Write-Host ""

Push-Location $frontend
try {
    npx vite
} finally {
    Pop-Location
}
