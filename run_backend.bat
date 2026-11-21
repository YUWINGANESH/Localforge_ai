@echo off
title LocalForge-AI 2.0 Backend Service
echo Starting LocalForge-AI Backend on http://127.0.0.1:8765 ...
cd /d "c:\dev2\backend"
".venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8765 --reload
pause
