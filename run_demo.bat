@echo off
rem LiaraPilot - one-click DEMO launcher for Windows (cmd or double-click).
rem DEMO_MODE=1 forces FakeLLM + MockDeployer: fully offline, zero external calls.
set DEMO_MODE=1
cd /d "%~dp0"
python -m uvicorn app.main:app --host 0.0.0.0 --port 3000
