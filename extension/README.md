# LocalForge-AI 2.0 VS Code Extension

Native Visual Studio Code frontend for **LocalForge-AI 2.0**, providing an offline autonomous software engineering sidebar powered by Ollama and FastAPI.

## Features
- Real-time SSE streaming chat & autonomous agent workflow (`Chat` vs `Agent (Auto)` mode).
- Live activity phase indicator (`UNDERSTAND`, `PLAN`, `IMPLEMENT`, `EXECUTE`, `DEBUG`, `VALIDATE`, `REPORT`).
- Interactive human-in-the-loop Approval Cards with inline unified diffs and native `vscode.diff` side-by-side comparison.
- Dynamic model switching (`qwen2.5-coder:7b`, `qwen3:4b`) and RAM telemetry indicator.

## Development & Testing
```bash
npm install
npm run compile
npm test
```
Press `F5` in VS Code to launch an Extension Development Host.
