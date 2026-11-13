@echo off
chcp 65001 >nul 2>&1
title Claude Code 桌面版中文汉化
echo ============================================
echo   Claude Code 桌面版 中文汉化安装程序
echo ============================================
echo.

:: 检查 Python
python3 --version >nul 2>&1
if errorlevel 1 (
    python --version >nul 2>&1
    if errorlevel 1 (
        echo [错误] 未检测到 Python，请先安装 Python 3:
        echo   https://www.python.org/downloads/
        echo.
        pause
        exit /b 1
    )
    set PYTHON=python
) else (
    set PYTHON=python3
)

:: 检查管理员权限
net session >nul 2>&1
if errorlevel 1 (
    echo 正在请求管理员权限...
    powershell -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)

echo 以管理员权限运行
echo.

:: 检测安装布局: 新版 (5.11492.x+) 打包为 ion-dist.asar 单文件, 旧版是 ion-dist\ 解包目录
set "LAYOUT="
for /f "delims=" %%i in ('powershell -NoProfile -Command "$p = Get-AppxPackage -Name '*Claude*' | Sort-Object Version -Descending | Select-Object -First 1; if ($p) { if (Test-Path (Join-Path $p.InstallLocation 'app\resources\ion-dist.asar')) { 'asar' } else { 'dir' } }"') do set "LAYOUT=%%i"

if /i "%LAYOUT%"=="asar" (
    echo 检测到 ion-dist.asar 单文件布局 (5.11492.x+), 使用 asar 适配脚本...
    %PYTHON% "%~dp0install_asar.py" %*
) else if /i "%LAYOUT%"=="dir" (
    echo 检测到 ion-dist 解包目录布局, 使用原版脚本...
    %PYTHON% "%~dp0install.py" %*
) else (
    echo 未检测到已安装的 Claude, 尝试原版脚本 (会给出具体提示)...
    %PYTHON% "%~dp0install.py" %*
)

echo.
echo 按任意键退出...
pause >nul
