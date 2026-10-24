@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 实时日志（按 Ctrl+C 退出）
docker compose logs -f --tail 120
