@echo off
REM VocoType - 无窗口启动（系统托盘模式）
REM 会自动使用项目目录下的 .venv

cd /d "E:\Programs\vocotype-cli"
start "" /b "E:\Programs\vocotype-cli\.venv\Scripts\pythonw.exe" "E:\Programs\vocotype-cli\main.py" --config "E:\Programs\vocotype-cli\config.json"
