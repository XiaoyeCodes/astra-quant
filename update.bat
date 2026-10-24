@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 正在更新 OctoBot 镜像并重建驾驶舱...
docker compose pull octobot
docker compose up -d --build
echo 更新完成。
pause
