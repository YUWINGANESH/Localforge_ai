# LocalForge-AI 2.0 Development & Testing Guide

## Running Backend Automated Tests
From the `backend` directory:
```powershell
.venv\Scripts\python.exe -m pytest tests -v
```

## Running Code Quality Linters
To check formatting and static typing compliance:
```powershell
.venv\Scripts\python.exe -m ruff check . --config ruff.toml
```

## Compiling Extension Code
From the `extension` directory:
```powershell
npm run compile
```
Or for live continuous compilation:
```powershell
npm run watch
```

## Architecture Principles
1. **Never send entire repositories to the LLM**: Use the Codebase Engine to extract compact context.
2. **Never execute destructive actions silently**: File edits, file creations, file deletions, and terminal executions must be gated through approval.
3. **No Infinite Loops**: ReAct reasoning loops are strictly bounded (max 8 iterations, max 3 repair attempts).
