# LocalForge-AI 2.0

**Local-First Autonomous AI Coding Agent for Visual Studio Code**

LocalForge-AI 2.0 is an independent, locally hosted AI software engineering agent designed to run directly within Visual Studio Code. Powered by local LLMs via Ollama (e.g. `qwen2.5-coder:7b`, `qwen3:4b`), LocalForge-AI can understand codebases, plan multi-step tasks, create and edit files with diff previews, run terminal commands, execute tests, diagnose errors, and self-repair code—all completely offline without cloud API subscriptions.

---

## Key Features

- **Local Inference Engine**: Connects to local Ollama instances (`http://127.0.0.1:11434`) using hybrid GPU/CPU offloading.
- **Hardware-Aware Design**: Optimized for 16GB RAM and 4GB VRAM (e.g., NVIDIA RTX 3050 Laptop GPU).
- **Codebase Understanding**: Project scanning, `.gitignore`-compliant file filtering, symbol and text search, and compact context management.
- **Bounded ReAct Agent**: Structured planning, controlled tool execution, loop detection, and autonomous error repair (configurable, default 4 attempts, max 16 iterations).
- **Interactive Settings & Tuning Panel**: In-extension gear icon opening full configuration for agent loop limits, repair attempts, context window size, timeouts, and auto-approval toggles with live hot-reload to the backend.
- **Security & Approval Policy**: Explicit user approval required for file edits (with visual diffs), file creations, deletions, and terminal executions.
- **3-Tier Local Memory**: SQLite-based conversation memory (L1), task memory (L2), and persistent project conventions/decisions (L3).
- **Native VS Code Integration**: Seamless sidebar chat, streaming responses, diff review panel, and status monitoring.

---

## Architecture Overview

```
VS Code Extension (TypeScript) <--- HTTP/SSE ---> FastAPI Local Backend (Python) <---> Ollama Local LLM
```

- **Backend**: Python 3.13+, FastAPI, Pydantic, HTTPX, SQLite, Pytest.
- **Extension**: TypeScript, VS Code Webview API, Native VS Code Theme Integration.

---

## Quick Start

### 1. Requirements
- Windows 11 / Linux / macOS
- Python 3.11+
- Node.js 18+ & npm
- Ollama installed and running with a coding model (e.g., `ollama run qwen2.5-coder:7b`)

### 2. Backend Setup
```bash
cd backend
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/macOS:
source .venv/bin/activate

pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8765 --reload
```

### 3. Extension Setup
```bash
cd extension
npm install
npm run compile
npm test
```

Open the repository root `c:\dev2` in VS Code, press `F5` to launch an Extension Development Host, and click the LocalForge-AI icon in the Activity Bar.

### 4. Running Automated Tests
```powershell
# Backend Tests (Mocked Unit Suite + Live Ollama Integration)
backend\.venv\Scripts\python.exe -m pytest backend\tests

# Extension Tests
cd extension
npm.cmd test
```

---

## Documentation
- [Architecture Overview](docs/architecture.md)
- [Installation Guide](docs/installation.md)
- [Development & Testing](docs/development.md)
- [Troubleshooting](docs/troubleshooting.md)

---

## License

MIT License. See [LICENSE](LICENSE) for details.
