@echo off
REM VocoType - 调试模式启动（有控制台窗口，能看到日志输出）

cd /d "E:\Programs\vocotype-cli"
.venv\Scripts\python.exe main.py --config config.json
pause
