@echo off
REM VocoType - 无窗口启动（系统托盘模式）
REM 使用批处理所在目录的相对路径

cd /d "%~dp0"
start "" /b ".venv\Scripts\pythonw.exe" "main.py" --config "config.json"
