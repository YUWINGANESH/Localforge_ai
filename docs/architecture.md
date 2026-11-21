# LocalForge-AI 2.0 System Architecture

## Overview
LocalForge-AI 2.0 is an autonomous, local-first software engineering assistant for Visual Studio Code. It provides an offline alternative to cloud-dependent coding agents by connecting directly to local LLMs managed by Ollama.

```
+-----------------------------------------------------------------------------------+
|                            VS CODE EXTENSION                                      |
|  +-----------------------------+  +---------------------------------------------+ |
|  | Webview Provider (Sidebar)  |  | VS Code API Bridge                          | |
|  | - Chat & Stream Renderer    |  | - Workspace Tracker                         | |
|  | - Task & Plan Tracker       |  | - Native Diff Editor Provider               | |
|  | - Interactive Approval Cards|  | - Diagnostics & Terminal Integration        | |
|  +-----------------------------+  +---------------------------------------------+ |
+----------------------------------------|------------------------------------------+
                                         | HTTP / Server-Sent Events (SSE)
+----------------------------------------v------------------------------------------+
|                       FASTAPI LOCAL BACKEND (Python 3.13)                         |
|                                                                                   |
|  +-----------------------------------------------------------------------------+  |
|  | API Layer: /health, /api/models, /api/chat, /api/tasks, /api/approvals       |  |
|  +-----------------------------------------------------------------------------+  |
|                                        |                                          |
|  +-------------------------------------v---------------------------------------+  |
|  | ReAct AI Orchestrator                                                       |  |
|  | - Bounded Reasoning Loop (Configurable 4-64 turns, default 16) |  |
|  | - Task State Machine                     - Repair & Self-Correction Engine  |  |
|  +-------------------|----------------------------------|----------------------+  |
|                      |                                  |                         |
|  +-------------------v-------------------+  +-----------v----------------------+  |
|  | Context & Codebase Engine             |  | Extensible Tool Registry         |  |
|  | - Workspace Scanner & Ignore Filters  |  | - File Ops (read/write/edit/diff)|  |
|  | - Compact Context Builder             |  | - Dev Ops (test/run/dep install) |  |
|  | - Exact & Token Search Filter         |  | - Git Ops (status/diff/log)      |  |
|  +-------------------|-------------------+  +-----------|----------------------+  |
|                      |                                  |                         |
|  +-------------------v-------------------+  +-----------v----------------------+  |
|  | 3-Tier Memory System (SQLite)         |  | Security & Sandbox Gate          |  |
|  | - L1: Live Conversation Buffer        |  | - Workspace Path Normalization   |  |
|  | - L2: Task & Plan State               |  | - Explicit Permission Policy     |  |
|  | - L3: Persistent Project Memory       |  | - Credential & Secret Redactor   |  |
|  +---------------------------------------+  +----------------------------------+  |
|                                        |                                          |
|  +-------------------------------------v---------------------------------------+  |
|  | Local LLM Gateway (HTTPX Async Client)                                      |  |
|  | - Ollama REST API (http://127.0.0.1:11434)                                  |  |
|  | - VRAM Budget Watcher & Memory Guard (4GB VRAM / 16GB RAM)                   |  |
|  | - Model Fallbacks: Primary: qwen2.5-coder:7b | Alternative: qwen3:4b         |  |
|  +-----------------------------------------------------------------------------+  |
+----------------------------------------|------------------------------------------+
                                         | Local HTTP
+----------------------------------------v------------------------------------------+
|                         OLLAMA LOCAL INFERENCE ENGINE                             |
|          Runs Qwen 2.5-Coder 7B / Qwen3 4B on RTX 3050 (Hybrid Offload)          |
+-----------------------------------------------------------------------------------+
```

## Hardware Profile & Memory Optimization
- **Operating System**: Windows 11
- **CPU**: AMD Ryzen 7 5800H (8 Cores, 16 Threads)
- **GPU**: NVIDIA GeForce RTX 3050 Laptop GPU (4GB VRAM)
- **RAM**: 16GB System Memory
- **Quantization**: 4-bit (Q4_K_M) models are recommended. Ollama splits model layers across the 4GB GPU and system RAM automatically.
- **Context Management**: Context windows are bounded (default 4096 tokens) to prevent VRAM allocation spikes.
