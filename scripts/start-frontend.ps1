# ============================================================
# 启动前端开发服务器（Vite）
# 用法：powershell -ExecutionPolicy Bypass -File scripts\start-frontend.ps1 [-ApiTarget http://127.0.0.1:18080]
#
# 关于 -ApiTarget 的默认值
#   8000 是"约定俗成"的端口，但很容易被别的应用占用。一旦被占用，
#   Vite 会把 /api 代理到那个应用上并收到 404「资源不存在」——
#   现象上非常像"后端没启动"或"接口没注册"，排查方向会被完全带偏。
#   因此这里与后端启动脚本保持一致，默认 18080。
# ============================================================
param(
    [string]$ApiTarget = 'http://127.0.0.1:18080'
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
