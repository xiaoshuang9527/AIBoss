@echo off
chcp 65001 >nul
setlocal

REM ============================================================
REM  BOSS 直聘专用浏览器 —— 双击启动（批处理入口）
REM ------------------------------------------------------------
REM  真正的逻辑放在同目录的 start-boss-browser.ps1，
REM  批处理只负责调用它 —— 这样避免 cmd.exe 对引号/竖线/括号的
REM  解析陷阱（上一版就是被 `|` 咬到了，直接静默退出）。
REM
REM  如果本文双击没反应，请改用：
REM    powershell -ExecutionPolicy Bypass -File scripts\start-boss-browser.ps1
REM ============================================================

set "PS1=%~dp0start-boss-browser.ps1"

if not exist "%PS1%" (
    echo [X] 找不到 %PS1%
    pause
    exit /b 1
)

echo 正在调用 %PS1% ...
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%PS1%" %*

echo.
echo 脚本已结束。
pause
