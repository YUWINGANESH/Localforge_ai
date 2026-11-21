# LocalForge-AI 2.0 Installation & Setup Guide

## Prerequisites
1. **Windows 11, Linux, or macOS**
2. **Python 3.11+** (Tested on Python 3.13.17)
3. **Node.js 18+** and **npm**
4. **Ollama**: Download and install from [ollama.com](https://ollama.com).
   - Start Ollama:
     ```powershell
     ollama serve
     ```
   - Pull recommended model:
     ```powershell
     ollama pull qwen2.5-coder:7b
     ```
     Or for lower memory usage:
     ```powershell
     ollama pull qwen3:4b
     ```

## Backend Installation
1. Navigate to the backend directory:
   ```powershell
   cd backend
   ```
2. Create and activate a virtual environment:
   ```powershell
   python -m venv .venv
   .venv\Scripts\activate
   ```
3. Install dependencies:
   ```powershell
   pip install -r requirements.txt
   ```
4. Start the backend:
   ```powershell
   python -m uvicorn app.main:app --host 127.0.0.1 --port 8765 --reload
   ```
5. Verify health:
   Navigate to `http://127.0.0.1:8765/health` in your browser.

## VS Code Extension Installation
1. Navigate to the extension directory:
   ```powershell
   cd extension
   ```
2. Install dependencies:
   ```powershell
   npm install
   ```
3. Compile the extension:
   ```powershell
   npm run compile
   ```
4. Run in VS Code:
   - Open the repository root `c:\dev2` in VS Code.
   - Press `F5` to open the Extension Development Host window.
   - Click the LocalForge-AI anvil icon in the Activity Bar to open the agent sidebar.
