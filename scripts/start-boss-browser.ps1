# ============================================================
#  启动 BOSS 直聘「专用浏览器」（带 CDP 远程调试端口）
# ------------------------------------------------------------
#  为什么需要它
#    抓取与登录态判定都依赖 Chrome 的远程调试端口（默认 9222）。
#    在某些受限运行环境里，Chrome 会因为无法启动崩溃收集子进程
#    而立刻自杀退出（crashpad "OpenProcess 拒绝访问" 后
#    self-terminating），端口永远打不开。
#    本脚本额外加上 --disable-crash-reporter 规避这一点，
#    并在启动后自动验证端口是否真的就绪。
#
#  用法
#    右键本文件 -> 「使用 PowerShell 运行」
#    或在普通 PowerShell 窗口里执行：
#        powershell -ExecutionPolicy Bypass -File scripts\start-boss-browser.ps1
#
#  启动后：在弹出的窗口里登录 https://www.zhipin.com
#         然后回到工作台点「账号授权」->「校验登录态」
# ============================================================

param(
    [int]$Port = 9222,
    [switch]$UseEdge,
    [switch]$Force   # 端口已被占用时，强制结束旧实例后重启
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot

$profileDir = Join-Path $root 'backend\storage\chrome-profiles\.boss-zhipin-scraper\chrome-profile'

function Write-Step($msg) { Write-Host $msg -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "[OK]  $msg" -ForegroundColor Green }
function Write-Bad($msg)  { Write-Host "[X]   $msg" -ForegroundColor Red }
function Write-Warn2($msg){ Write-Host "[!]   $msg" -ForegroundColor Yellow }

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  BOSS 直聘专用浏览器启动器" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

# ---------- 1. 找浏览器 ----------
function Resolve-Browser {
    param([switch]$PreferEdge)
    $chrome = @(
        "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe",
        "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
        "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe"
    )
    $edge = @(
        "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
        "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe"
    )
    $candidates = if ($PreferEdge) { $edge + $chrome } else { $chrome + $edge }
    foreach ($path in $candidates) {
        if (Test-Path $path) { return $path }
    }
    return $null
}

$browser = Resolve-Browser -PreferEdge:$UseEdge
if (-not $browser) {
    Write-Bad "没有找到 Chrome 或 Edge，请先安装浏览器。"
    Read-Host "按回车键退出"
    exit 1
}
Write-Ok "浏览器：$browser"

# ---------- 2. 检查端口是否已被占用 ----------
function Test-CdpPort {
    param([int]$P)
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $client.Connect('127.0.0.1', $P)
        return $true
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

function Get-CdpInfo {
    param([int]$P)
    try {
        $response = Invoke-WebRequest -Uri "http://127.0.0.1:$P/json/version" -UseBasicParsing -TimeoutSec 5
        return ($response.Content | ConvertFrom-Json)
    } catch {
        return $null
    }
}

if (Test-CdpPort -P $Port) {
    $info = Get-CdpInfo -P $Port
    if ($info) {
        Write-Warn2 "端口 $Port 已经有一个可用的 CDP 实例在跑：$($info.Browser)"
        Write-Host "      直接用它即可 —— 回到工作台点「校验登录态」。" -ForegroundColor Gray
        Read-Host "按回车键退出"
        exit 0
    }
    Write-Warn2 "端口 $Port 被其他程序占用，且不是 Chrome 调试端口。"
    if (-not $Force) {
        Write-Host "      请换一个端口（例如 -Port 9333），或在系统设置里把 CDP 端口改成同一个值。" -ForegroundColor Gray
        Read-Host "按回车键退出"
        exit 1
    }
}

# ---------- 3. 准备隔离 profile ----------
if (-not (Test-Path $profileDir)) {
    New-Item -ItemType Directory -Force -Path $profileDir | Out-Null
    Write-Ok "已创建隔离 profile 目录"
} else {
    Write-Ok "复用已有隔离 profile"
}
Write-Host "      $profileDir" -ForegroundColor Gray

# ---------- 4. 启动 ----------
$arguments = @(
    "--remote-debugging-port=$Port",
    "--user-data-dir=$profileDir",
    "--no-first-run",
    "--no-default-browser-check",
    "--remote-allow-origins=*",
    # 关键：受限环境里 crashpad 起不来会让 Chrome 直接自杀，
    # 关掉崩溃收集器即可正常启动。
    "--disable-crash-reporter",
    "--disable-breakpad",
    "https://www.zhipin.com"
)

Write-Step ""
Write-Step "正在启动浏览器（调试端口 $Port）..."
try {
    Start-Process -FilePath $browser -ArgumentList $arguments | Out-Null
} catch {
    Write-Bad "启动失败：$($_.Exception.Message)"
    Read-Host "按回车键退出"
    exit 1
}

# ---------- 5. 等待端口就绪 ----------
Write-Step "等待调试端口就绪..."
$ready = $false
$info = $null
for ($i = 1; $i -le 30; $i++) {
    Start-Sleep -Seconds 1
    if (Test-CdpPort -P $Port) {
        $info = Get-CdpInfo -P $Port
        if ($info) { $ready = $true; break }
    }
    Write-Host "." -NoNewline
}
Write-Host ""

if ($ready) {
    Write-Host ""
    Write-Ok "调试端口已就绪：$($info.Browser)"
    Write-Host ""
    Write-Host "下一步：" -ForegroundColor Yellow
    Write-Host "  1) 在弹出的浏览器窗口里登录 https://www.zhipin.com" -ForegroundColor Gray
    Write-Host "  2) 回到工作台「账号授权」页，点「校验登录态」" -ForegroundColor Gray
    Write-Host "  3) 这个浏览器窗口请保持开启，关闭后抓取会失败" -ForegroundColor Gray
    Write-Host ""
    Write-Host "登录态保存在隔离 profile 里，重启电脑后仍然有效。" -ForegroundColor Gray
} else {
    Write-Host ""
    Write-Bad "等待 30 秒，调试端口 $Port 仍未就绪。"
    Write-Host ""
    Write-Host "排查建议：" -ForegroundColor Yellow
    Write-Host "  · 确认浏览器窗口是否真的打开了（可能被安全软件拦截）" -ForegroundColor Gray
    Write-Host "  · 端口 $Port 是否被其他程序占用：netstat -ano | findstr :$Port" -ForegroundColor Gray
    Write-Host "  · 换端口重试：powershell -ExecutionPolicy Bypass -File scripts\start-boss-browser.ps1 -Port 9333" -ForegroundColor Gray
    Write-Host "    （换端口后需同步修改 backend\.env 的 BZP_CDP_PORT）" -ForegroundColor Gray
    Write-Host "  · 若浏览器一闪而过，通常是安全软件/管控策略阻止了调试模式" -ForegroundColor Gray
}

Write-Host ""
Read-Host "按回车键关闭本窗口（浏览器会继续运行）"
