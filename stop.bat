@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 正在停止 OctoBot 和驾驶舱...
echo 注意：停止程序不会撤销 Bitget 上已经挂出的订单（实盘时请先在 OctoBot 里停止策略或撤单）。
docker compose down
echo 已停止。
pause
