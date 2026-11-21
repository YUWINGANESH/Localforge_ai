# LocalForge-AI 2.0 Troubleshooting Guide

## 1. Backend Offline in VS Code Sidebar
- Ensure the FastAPI backend is running on port `8765`:
  ```powershell
  .\scripts\start_backend.ps1
  ```
- Verify by navigating to `http://127.0.0.1:8765/health` in your browser.

## 2. Ollama Offline or Model Missing
- Verify Ollama is running:
  ```powershell
  ollama list
  ```
- If `qwen2.5-coder:7b` or `qwen3:4b` is missing, pull it:
  ```powershell
  ollama pull qwen2.5-coder:7b
  ```
- If Ollama daemon isn't running, launch `ollama serve`.

## 3. Slow Generation or High VRAM Usage (4GB RTX 3050)
- Switch to `qwen3:4b` from the model selector dropdown in the VS Code sidebar (uses ~2.5 GB vs ~4.7 GB).
- Reduce `CONTEXT_WINDOW` in `backend/.env` (e.g., `2048` or `4096`).
- Close GPU-heavy applications (games, other local models, multiple browser video tabs).

## 4. Windows PowerShell Execution Policy Blocks `npm` or Scripts
- Run `npm.cmd` instead of `npm`, or run Python directly using `backend\.venv\Scripts\python.exe -m pytest`.
