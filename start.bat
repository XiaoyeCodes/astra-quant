@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title ASTRA 星枢 - 启动

where docker >nul 2>nul
if errorlevel 1 (
  echo [X] 没有找到 Docker。请先安装 Docker Desktop: https://www.docker.com/products/docker-desktop/
  pause
  exit /b 1
)
docker info >nul 2>nul
if errorlevel 1 (
  echo [X] Docker Desktop 没有运行。请先打开 Docker Desktop，等左下角显示 Engine running 后再双击本文件。
  pause
  exit /b 1
)

if not exist ".env" (
  copy ".env.example" ".env" >nul
  echo [i] 已生成 .env 配置文件，可以用记事本修改。
)
if not exist "data" mkdir "data"

echo [1/2] 正在启动 OctoBot 和驾驶舱（第一次需要下载镜像，可能要几分钟）...
docker compose up -d --build
if errorlevel 1 (
  echo [X] 启动失败。常见原因：网络无法下载镜像。请看 README 的“常见问题”。
  pause
  exit /b 1
)

echo [2/2] 等待服务就绪...
timeout /t 8 /nobreak >nul
start "" "http://localhost:8080"
start "" "http://localhost:5001"

echo.
echo  驾驶舱:       http://localhost:8080
echo  OctoBot 控制台: http://localhost:5001
echo  停止全部服务: 双击 stop.bat
echo.
pause
