@echo off
chcp 65001 >nul 2>nul
REM ============================================
REM  RailBuddy Windows 服务安装/管理脚本
REM  使用 NSSM (Non-Sucking Service Manager)
REM  安装两个服务：
REM    1. RailBuddy        - 抓取调度服务（定时抓取+邮件推送）
REM    2. RailBuddyWeb     - Web 管理面板服务（可视化配置）
REM
REM  用法：
REM    install_service.bat              安装并启动两个服务
REM    install_service.bat uninstall    停止并删除两个服务
REM    install_service.bat start        启动两个服务
REM    install_service.bat stop         停止两个服务
REM    install_service.bat restart      重启两个服务
REM    install_service.bat status       查看两个服务状态
REM ============================================

REM ---- 配置区 ----
set PYTHON_EXE=%~dp0venv\Scripts\python.exe
set APP_DIR=%~dp0
set APP_DIR=%APP_DIR:~0,-1%

REM 抓取服务
set SVC_FETCH=RailBuddy
set SVC_FETCH_DISPLAY=RailBuddy 城轨招标监控服务
set SVC_FETCH_DESC=自动监控城市轨道交通招标信息，定时抓取并通过邮件推送

REM Web 管理面板服务
set SVC_WEB=RailBuddyWeb
set SVC_WEB_DISPLAY=RailBuddy Web 管理面板
set SVC_WEB_DESC=RailBuddy 可视化管理面板，配置数据源/邮箱/调度
set WEB_PORT=5210

REM 日志目录
set LOG_DIR=%APP_DIR%\logs

REM ============================================
REM  参数处理
REM ============================================
set ACTION=%1
if "%ACTION%"=="" set ACTION=install

REM 检查 NSSM
where nssm >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo [错误] 未找到 nssm 命令！
    echo.
    echo 请先安装 NSSM:
    echo   1. 下载: https://nssm.cc/download
    echo   2. 解压后将 nssm.exe 放到系统 PATH 中
    echo   或直接放到 C:\Windows\System32\
    echo.
    pause
    exit /b 1
)

REM 检查 Python（仅安装时需要）
if /i "%ACTION%"=="install" (
    if not exist "%PYTHON_EXE%" (
        echo [错误] Python 解释器不存在: %PYTHON_EXE%
        echo 请先运行 install.bat 创建虚拟环境
        pause
        exit /b 1
    )
)

REM 确保日志目录存在
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

REM ============================================
REM  执行操作
REM ============================================
if /i "%ACTION%"=="install" goto :do_install
if /i "%ACTION%"=="uninstall" goto :do_uninstall
if /i "%ACTION%"=="start" goto :do_start
if /i "%ACTION%"=="stop" goto :do_stop
if /i "%ACTION%"=="restart" goto :do_restart
if /i "%ACTION%"=="status" goto :do_status

echo [错误] 未知操作: %ACTION%
echo 用法: %~nx0 [install^|uninstall^|start^|stop^|restart^|status]
exit /b 1

REM ============================================
REM  安装服务
REM ============================================
:do_install
echo ============================================
echo  RailBuddy 服务安装
echo ============================================
echo.
echo Python:   %PYTHON_EXE%
echo 工作目录: %APP_DIR%
echo Web 端口: %WEB_PORT%
echo.

REM --- 安装抓取服务 ---
echo [1/2] 安装抓取调度服务 (%SVC_FETCH%)...

nssm status %SVC_FETCH% >nul 2>nul
if %ERRORLEVEL% equ 0 (
    echo   服务已存在，先停止并删除...
    nssm stop %SVC_FETCH% >nul 2>nul
    nssm remove %SVC_FETCH% confirm >nul 2>nul
)

nssm install %SVC_FETCH% "%PYTHON_EXE%" "-m railbuddy"
nssm set %SVC_FETCH% AppDirectory "%APP_DIR%"
nssm set %SVC_FETCH% AppParameters "-m railbuddy"
nssm set %SVC_FETCH% AppStdout "%LOG_DIR%\fetch_stdout.log"
nssm set %SVC_FETCH% AppStderr "%LOG_DIR%\fetch_stderr.log"
nssm set %SVC_FETCH% AppRotateFiles 1
nssm set %SVC_FETCH% AppRotateOnline 1
nssm set %SVC_FETCH% AppRotateBytes 10485760
nssm set %SVC_FETCH% DisplayName "%SVC_FETCH_DISPLAY%"
nssm set %SVC_FETCH% Description "%SVC_FETCH_DESC%"
nssm set %SVC_FETCH% Start SERVICE_AUTO_START
nssm set %SVC_FETCH% AppExit Default Restart
nssm set %SVC_FETCH% AppRestartDelay 10000
echo   [完成]
echo.

REM --- 安装 Web 管理面板服务 ---
echo [2/2] 安装 Web 管理面板服务 (%SVC_WEB%)...

nssm status %SVC_WEB% >nul 2>nul
if %ERRORLEVEL% equ 0 (
    echo   服务已存在，先停止并删除...
    nssm stop %SVC_WEB% >nul 2>nul
    nssm remove %SVC_WEB% confirm >nul 2>nul
)

nssm install %SVC_WEB% "%PYTHON_EXE%" "-m railbuddy --web --port %WEB_PORT%"
nssm set %SVC_WEB% AppDirectory "%APP_DIR%"
nssm set %SVC_WEB% AppParameters "-m railbuddy --web --port %WEB_PORT%"
nssm set %SVC_WEB% AppStdout "%LOG_DIR%\web_stdout.log"
nssm set %SVC_WEB% AppStderr "%LOG_DIR%\web_stderr.log"
nssm set %SVC_WEB% AppRotateFiles 1
nssm set %SVC_WEB% AppRotateOnline 1
nssm set %SVC_WEB% AppRotateBytes 10485760
nssm set %SVC_WEB% DisplayName "%SVC_WEB_DISPLAY%"
nssm set %SVC_WEB% Description "%SVC_WEB_DESC%"
nssm set %SVC_WEB% Start SERVICE_AUTO_START
nssm set %SVC_WEB% AppExit Default Restart
nssm set %SVC_WEB% AppRestartDelay 10000
echo   [完成]
echo.

REM --- 启动服务 ---
echo 正在启动服务...
nssm start %SVC_FETCH%
if %ERRORLEVEL% equ 0 (
    echo   %SVC_FETCH% 启动成功
) else (
    echo   [警告] %SVC_FETCH% 启动失败，请检查日志: %LOG_DIR%\fetch_stderr.log
)

nssm start %SVC_WEB%
if %ERRORLEVEL% equ 0 (
    echo   %SVC_WEB% 启动成功
) else (
    echo   [警告] %SVC_WEB% 启动失败，请检查日志: %LOG_DIR%\web_stderr.log
)

echo.
echo ============================================
echo  安装完成！
echo ============================================
echo.
echo 服务列表:
echo   %SVC_FETCH%  - 抓取调度服务（定时抓取+邮件推送）
echo   %SVC_WEB%     - Web 管理面板（http://localhost:%WEB_PORT%）
echo.
echo 日志文件:
echo   %LOG_DIR%\fetch_stdout.log
echo   %LOG_DIR%\fetch_stderr.log
echo   %LOG_DIR%\web_stdout.log
echo   %LOG_DIR%\web_stderr.log
echo.
echo 管理命令:
echo   安装:  %~nx0 install
echo   卸载:  %~nx0 uninstall
echo   启动:  %~nx0 start
echo   停止:  %~nx0 stop
echo   重启:  %~nx0 restart
echo   状态:  %~nx0 status
echo.
echo 也可使用 services.msc 图形化管理
echo.
pause
exit /b 0

REM ============================================
REM  卸载服务
REM ============================================
:do_uninstall
echo ============================================
echo  RailBuddy 服务卸载
echo ============================================
echo.

echo [1/2] 停止并删除 %SVC_FETCH%...
nssm status %SVC_FETCH% >nul 2>nul
if %ERRORLEVEL% equ 0 (
    nssm stop %SVC_FETCH% >nul 2>nul
    nssm remove %SVC_FETCH% confirm >nul 2>nul
    echo   [完成]
) else (
    echo   服务不存在，跳过
)
echo.

echo [2/2] 停止并删除 %SVC_WEB%...
nssm status %SVC_WEB% >nul 2>nul
if %ERRORLEVEL% equ 0 (
    nssm stop %SVC_WEB% >nul 2>nul
    nssm remove %SVC_WEB% confirm >nul 2>nul
    echo   [完成]
) else (
    echo   服务不存在，跳过
)

echo.
echo 卸载完成。
echo.
pause
exit /b 0

REM ============================================
REM  启动服务
REM ============================================
:do_start
echo 正在启动 RailBuddy 服务...
nssm start %SVC_FETCH% 2>nul
if %ERRORLEVEL% equ 0 (echo   %SVC_FETCH% 已启动) else (echo   [失败] %SVC_FETCH%)
nssm start %SVC_WEB% 2>nul
if %ERRORLEVEL% equ 0 (echo   %SVC_WEB% 已启动) else (echo   [失败] %SVC_WEB%)
echo.
pause
exit /b 0

REM ============================================
REM  停止服务
REM ============================================
:do_stop
echo 正在停止 RailBuddy 服务...
nssm stop %SVC_FETCH% 2>nul
if %ERRORLEVEL% equ 0 (echo   %SVC_FETCH% 已停止) else (echo   [跳过] %SVC_FETCH%)
nssm stop %SVC_WEB% 2>nul
if %ERRORLEVEL% equ 0 (echo   %SVC_WEB% 已停止) else (echo   [跳过] %SVC_WEB%)
echo.
pause
exit /b 0

REM ============================================
REM  重启服务
REM ============================================
:do_restart
echo 正在重启 RailBuddy 服务...
nssm restart %SVC_FETCH% 2>nul
if %ERRORLEVEL% equ 0 (echo   %SVC_FETCH% 已重启) else (echo   [失败] %SVC_FETCH%)
nssm restart %SVC_WEB% 2>nul
if %ERRORLEVEL% equ 0 (echo   %SVC_WEB% 已重启) else (echo   [失败] %SVC_WEB%)
echo.
pause
exit /b 0

REM ============================================
REM  查看状态
REM ============================================
:do_status
echo ============================================
echo  RailBuddy 服务状态
echo ============================================
echo.

echo [%SVC_FETCH%] 抓取调度服务
nssm status %SVC_FETCH% >nul 2>nul
if %ERRORLEVEL% equ 0 (
    for /f "tokens=*" %%i in ('nssm status %SVC_FETCH% 2^>nul') do set ST=%%i
    echo   状态: %ST%
) else (
    echo   状态: 未安装
)
echo.

echo [%SVC_WEB%] Web 管理面板
nssm status %SVC_WEB% >nul 2>nul
if %ERRORLEVEL% equ 0 (
    for /f "tokens=*" %%i in ('nssm status %SVC_WEB% 2^>nul') do set ST=%%i
    echo   状态: %ST%
    echo   地址: http://localhost:%WEB_PORT%
) else (
    echo   状态: 未安装
)
echo.
echo 也可使用 services.msc 查看详细信息
echo.
pause
exit /b 0
