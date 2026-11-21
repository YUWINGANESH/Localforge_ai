"""
Section 16 Final Acceptance Test Runner for LocalForge-AI 2.0.

Executes the exact end-to-end user request using the live local Ollama model
(qwen2.5-coder:7b) inside an isolated workspace (c:/dev2/acceptance_demo):

"Create a simple full-stack task management application using React, Flask and SQLite.
Include CRUD operations, a clean frontend, input validation and automated tests."
"""
import asyncio
import json
import shutil
import sys
from pathlib import Path

# Allow importing backend app package
BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from app.config.settings import Settings  # noqa: E402
from app.database.sqlite_store import SQLiteStore  # noqa: E402
from app.llm.ollama_client import OllamaClient  # noqa: E402
from app.memory.project_memory import MemoryService  # noqa: E402
from app.orchestrator.agent import AgentOrchestrator  # noqa: E402
from app.security.approval_manager import ApprovalManager  # noqa: E402

ACCEPTANCE_PROMPT = (
    "Create a simple full-stack task management application using React, Flask and SQLite. "
    "Include CRUD operations, a clean frontend, input validation and automated tests.\n\n"
    "Architecture and requirements:\n"
    "1. Flask REST API (app.py):\n"
    "   - Uses sqlite3 directly with 'tasks.db'. Initialize the 'tasks' table at startup with (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, description TEXT).\n"
    "   - GET /: serves static/index.html.\n"
    "   - GET /tasks: returns JSON list of all tasks.\n"
    "   - POST /tasks: validates that 'title' is present and not empty (returns jsonify({'error': 'Title is required'}), 400 if missing or empty). Use cursor = conn.cursor(); cursor.execute('INSERT INTO tasks (title, description) VALUES (?, ?)', (title, desc)); task_id = cursor.lastrowid; conn.commit(); and return jsonify({'id': task_id, 'title': title, 'description': desc}), 201.\n"
    "   - GET /tasks/<int:id>: returns task by id, or jsonify({'error': 'Task not found'}), 404 if not found.\n"
    "   - PUT /tasks/<int:id>: validates 'title' (returns jsonify({'error': 'Title is required'}), 400 if empty), updates task, returns 200, or jsonify({'error': 'Task not found'}), 404 if not found.\n"
    "   - DELETE /tasks/<int:id>: checks if task exists (returns jsonify({'error': 'Task not found'}), 404 if not found), deletes task, returns jsonify({'message': 'Task deleted successfully'}), 200.\n"
    "   - (Important: always return JSON for errors using jsonify; do NOT use abort()).\n"
    "2. Frontend (static/index.html):\n"
    "   - Clean responsive React UI loaded via CDN with form, input validation, and task list with delete buttons.\n"
    "3. Automated Tests (test_app.py):\n"
    "   - Import the Flask application from app.py: from app import app (do NOT instantiate a new Flask app in test_app.py).\n"
    "   - In the pytest 'client' fixture, yield app.test_client(), and run conn.execute('DELETE FROM tasks'); conn.commit() so each test starts with an isolated empty database.\n"
    "   - Write pytest tests for all CRUD operations, 400 on invalid input, and 404 on nonexistent tasks. Check response.json['error'] on error responses.\n"
    "4. Execution:\n"
    "   - Install Flask via install_dependencies if needed.\n"
    "   - Run run_tests to verify 100% test pass before concluding with final_answer."
)


async def main():
    demo_dir = Path(__file__).resolve().parent.parent / "acceptance_demo"
    if demo_dir.exists():
        shutil.rmtree(demo_dir)
    demo_dir.mkdir(parents=True, exist_ok=True)

    settings = Settings(
        workspace_root=str(demo_dir),
        request_timeout_seconds=180.0,
        context_window=6144,
    )
    ollama = OllamaClient(settings=settings)
    online, ver, err = await ollama.check_health()
    if not online:
        print(f"ERROR: Ollama is offline ({err})")
        sys.exit(1)

    print(f"[INFO] Ollama connected (v{ver}) | Active Model: {settings.active_model}")
    print(f"[INFO] Acceptance Workspace: {demo_dir}")
    print("-" * 72)

    store = SQLiteStore(demo_dir / ".localforge_acceptance.db")
    mem_svc = MemoryService(store=store)
    mgr = ApprovalManager()

    orchestrator = AgentOrchestrator(
        workspace_root=demo_dir,
        ollama_client=ollama,
        approval_manager=mgr,
        settings=settings,
        memory_service=mem_svc,
        auto_approve=False,  # Exercise real interactive approval workflow!
    )

    approvals_granted = 0
    plan_seen = False
    report_data = None

    async for sse in orchestrator.run_task_stream(
        prompt=ACCEPTANCE_PROMPT,
        session_id="final-acceptance-session",
        max_iterations=16,
        max_repair_attempts=5,
    ):
        lines = sse.strip().split("\n")
        if len(lines) < 2:
            continue
        ev_type = lines[0].replace("event: ", "").strip()
        ev_data = json.loads(lines[1].replace("data: ", ""))

        if ev_type == "activity":
            print(f"[PHASE: {ev_data['phase']}] {ev_data['message']}")
        elif ev_type == "plan":
            plan_seen = True
            print("\n=== STRUCTURED EXECUTION PLAN ===")
            print(json.dumps(ev_data, indent=2))
            print("=================================\n")
        elif ev_type == "thought":
            print(f"[THOUGHT #{ev_data['iteration']}] {ev_data['thought']}")
        elif ev_type == "tool_call":
            args_preview = json.dumps(ev_data["arguments"])
            if len(args_preview) > 120:
                args_preview = args_preview[:120] + "..."
            print(f"[TOOL CALL #{ev_data['iteration']}] {ev_data['tool']}({args_preview})")
        elif ev_type == "approval_required":
            approvals_granted += 1
            print(f"[APPROVAL #{approvals_granted}] Reviewing {ev_data['operation']} -> {ev_data.get('relative_path') or ev_data.get('command')}")
            print(f"  Diff stats: +{ev_data.get('additions', 0)} / -{ev_data.get('deletions', 0)}")
            # Grant human approval asynchronously
            asyncio.create_task(mgr.resolve(ev_data["id"], approved=True))
        elif ev_type == "tool_result":
            status_tag = "OK" if ev_data["success"] else "FAIL"
            out_preview = ev_data["output"][:300].replace("\n", " | ")
            print(f"[TOOL RESULT: {status_tag}] {ev_data['tool']} -> {out_preview}")
        elif ev_type == "report":
            report_data = ev_data
            print("\n=== FINAL COMPLETION REPORT ===")
            print(json.dumps(ev_data, indent=2))
            print("===============================\n")
        elif ev_type == "error":
            print(f"[ERROR] {ev_data['error']}")

    print(f"[SUMMARY] Approvals granted: {approvals_granted} | Plan generated: {plan_seen} | Has report: {report_data is not None}")
    store.close()


if __name__ == "__main__":
    asyncio.run(main())
