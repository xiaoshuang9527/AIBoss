# ============================================================
#  启动 BOSS 直聘「专用浏览器」（带 CDP 远程调试端口）
# ------------------------------------------------------------
#  为什么需要它
#    抓取与登录态判定都依赖 Chrome 的远程调试端口（默认 9222）。
#
#  两个必须注意的点
#    1) **profile 目录不能写死**
#       工作台给每个授权档案分配一个按 profile_hash 命名的目录
#       （chrome-profiles/<hash>）。如果脚本用别的目录启动浏览器，
#       登录态会存到那个目录，而后端去自己的目录里读 —— 症状是
#       「扫码看起来成功了，但校验登录态永远说无效」。所以本脚本
#       一律向后端查询实际路径（见下面第 0 步）。
#
#    2) **必须在普通 Windows 环境里运行**
#       在受限环境（沙箱、高权限管控终端）中，Chrome 会因无法创建
#       崩溃收集子进程而立刻自杀退出（crashpad "OpenProcess 拒绝访问"
#       后 self-terminating），端口永远打不开。
#       实测 --disable-crash-reporter / --disable-breakpad **无法**绕过
#       这一点（参数仍保留，但别指望它解决受限环境的问题）。
#
#  用法
#    右键 .bat -> 「以管理员身份运行」不需要；双击即可：
#        scripts\start-boss-browser.bat
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

# 后端地址：用于查询「当前授权档案真正的 profile 目录」。
# 本机 8000 端口常被别的应用占用，工作台后端默认用 18080。
$backendBase = if ($env:BZP_BACKEND_BASE) { $env:BZP_BACKEND_BASE } else { 'http://127.0.0.1:18080' }

# ⚠️ 这个回退值与后端 settings.profile_dir() 的推导方式一致：
#       <storage>/chrome-profiles/<profile_hash>
#    正常流程下会被下面「向后端查询」的结果覆盖，这里只是后端没起来时的兜底。
$profileDir = Join-Path $root 'backend\storage\chrome-profiles\default'

function Write-Step($msg) { Write-Host $msg -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "[OK]  $msg" -ForegroundColor Green }
function Write-Bad($msg)  { Write-Host "[X]   $msg" -ForegroundColor Red }
function Write-Warn2($msg){ Write-Host "[!]   $msg" -ForegroundColor Yellow }

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  BOSS 直聘专用浏览器启动器" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

# ---------- 0. 向后端查询真正的 profile 目录 ----------
# 为什么必须查后端
#   工作台给每个授权档案分配一个按 profile_hash 命名的独立目录
#   （chrome-profiles/<hash>），而早期版本的脚本写死成
#   chrome-profiles/.boss-zhipin-scraper/chrome-profile。
#   两者是**不同的目录** —— 用写死的那个启动浏览器，登录态存进去后
#   后端去自己的目录里读，结果永远是「登录态无效」。这个 bug 的症状
#   很像"扫码没成功"，非常难查，所以这里一律以后端返回的路径为准。
function Resolve-ProfileDirFromBackend {
    try {
        $resp = Invoke-WebRequest -Uri "$backendBase/api/auth/status" -UseBasicParsing -TimeoutSec 6
        $json = $resp.Content | ConvertFrom-Json
        $dir = $json.data.profile.profile_dir
        if ($dir) {
            return @{ dir = $dir; ok = $true; hash = $json.data.profile.profile_hash }
        }
    } catch {
        # 后端没启动不应该阻断启动浏览器 —— 用户可以稍后再起后端
    }
    return @{ dir = $null; ok = $false; hash = $null }
}

Write-Step "正在向后端查询授权档案目录（$backendBase）..."
$resolved = Resolve-ProfileDirFromBackend
if ($resolved.ok) {
    $profileDir = $resolved.dir
    Write-Ok "已从后端获取 profile 目录（hash=$($resolved.hash)）"
} else {
    Write-Warn2 "查询后端失败，改用默认目录。"
    Write-Host "      后端可能还没启动。若浏览器里登录后仍显示「登录态无效」，" -ForegroundColor Gray
    Write-Host "      请先启动后端，再重新运行本脚本。" -ForegroundColor Gray
}

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

# 判断某个 CDP 实例用的 profile 是不是我们要的那个。
# /json/version 不报 user-data-dir，所以要从进程命令行里读。
#
# 两条路径都试：
#   1) Get-NetTCPConnection 拿监听进程（最快，但受限环境里该 cmdlet 会失败）
#   2) 退回到遍历 chrome/msedge 进程，按 --remote-debugging-port 匹配
# 只用前者的话，某些环境里会静默返回空，导致"看着有实例、却判断不出
# profile 是否一致"，从而漏掉那个最坑的情况。
function Get-CdpProfileDir {
    param([int]$P)

    $commandLines = @()

    try {
        $conn = Get-NetTCPConnection -LocalPort $P -State Listen -ErrorAction Stop |
            Select-Object -First 1
        if ($conn) {
            $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$($conn.OwningProcess)" -ErrorAction SilentlyContinue
            if ($proc -and $proc.CommandLine) { $commandLines += $proc.CommandLine }
        }
    } catch {
        # 受限环境里 Get-NetTCPConnection 不可用，走下面的回退路径
    }

    if ($commandLines.Count -eq 0) {
        try {
            $commandLines = @(Get-CimInstance Win32_Process -Filter "Name='chrome.exe' OR Name='msedge.exe'" -ErrorAction Stop |
                Where-Object { $_.CommandLine -and $_.CommandLine -match "--remote-debugging-port=$P(\s|$)" } |
                ForEach-Object { $_.CommandLine })
        } catch {
            return $null
        }
    }

    foreach ($line in $commandLines) {
        $m = [regex]::Match($line, '--user-data-dir=(?:"([^"]+)"|([^\s]+))')
        if ($m.Success) {
            $value = if ($m.Groups[1].Success) { $m.Groups[1].Value } else { $m.Groups[2].Value }
            return $value.TrimEnd('"')
        }
    }
    return $null
}

if (Test-CdpPort -P $Port) {
    $info = Get-CdpInfo -P $Port
    if ($info) {
        $runningDir = Get-CdpProfileDir -P $Port
        # 已经有一个 CDP 实例 —— 但要确认它用的是不是后端认的那个 profile。
        # 如果用一个"看起来对但其实是旧路径"的实例，登录态会存到别处，
        # 表现为「扫码成功但校验登录态永远无效」，非常难查。所以这里主动纠正。
        $sameDir = $false
        if ($runningDir) {
            try {
                $sameDir = ([System.IO.Path]::GetFullPath($runningDir).TrimEnd('\') -ieq
                            [System.IO.Path]::GetFullPath($profileDir).TrimEnd('\'))
            } catch { $sameDir = $false }
        }

        if ($sameDir) {
            Write-Ok "端口 $Port 已有 CDP 实例，且 profile 与后端一致：$($info.Browser)"
            Write-Host "      直接用即可 —— 回到工作台点「校验登录态」。" -ForegroundColor Gray
            Read-Host "按回车键退出"
            exit 0
        }

        Write-Warn2 "端口 $Port 上的 CDP 实例用的 profile 与后端不一致："
        Write-Host "        实际: $runningDir" -ForegroundColor Gray
        Write-Host "        应为: $profileDir" -ForegroundColor Gray
        Write-Host "      这个不一致会导致「扫码成功但登录态一直显示无效」。" -ForegroundColor Yellow
        Write-Host "      将关闭该实例并用正确 profile 重启..." -ForegroundColor Yellow
        Get-CimInstance Win32_Process -Filter "Name='chrome.exe' OR Name='msedge.exe'" |
            Where-Object { $_.CommandLine -and $_.CommandLine -match [regex]::Escape("--user-data-dir=$runningDir") } |
            ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
        Start-Sleep -Seconds 3
    } else {
        Write-Warn2 "端口 $Port 被其他程序占用，且不是 Chrome 调试端口。"
        if (-not $Force) {
            Write-Host "      请换一个端口（例如 -Port 9333），或在系统设置里把 CDP 端口改成同一个值。" -ForegroundColor Gray
            Read-Host "按回车键退出"
            exit 1
        }
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
