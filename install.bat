@echo off
REM ============================================
REM  RailBuddy 环境安装脚本
REM  创建 Python 虚拟环境并安装依赖
REM ============================================

echo ============================================
echo  RailBuddy 环境安装
echo ============================================
echo.

REM 尝试找到 Python
set PYTHON_CMD=python

REM 检查 Python 是否可用
%PYTHON_CMD% --version >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo [错误] 未找到 Python，请先安装 Python 3.9+
    echo 下载地址: https://www.python.org/downloads/
    pause
    exit /b 1
)

echo Python 版本:
%PYTHON_CMD% --version
echo.

REM 创建虚拟环境
echo 正在创建虚拟环境...
%PYTHON_CMD% -m venv venv
if %ERRORLEVEL% neq 0 (
    echo [错误] 虚拟环境创建失败
    pause
    exit /b 1
)

REM 激活虚拟环境并安装依赖
echo 正在安装依赖...
call venv\Scripts\activate.bat
pip install -r requirements.txt
if %ERRORLEVEL% neq 0 (
    echo [错误] 依赖安装失败
    pause
    exit /b 1
)

echo.
echo ============================================
echo  安装完成！
echo ============================================
echo.
echo 使用方法:
echo   1. 编辑 config.yaml 配置邮箱和数据源
echo   2. 测试运行: python -m railbuddy --once
echo   3. 测试邮件: python -m railbuddy --test-email
echo   4. 启动服务: python -m railbuddy
echo   5. 安装为Windows服务: install_service.bat
echo.
pause
