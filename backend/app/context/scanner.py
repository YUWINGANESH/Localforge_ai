"""Workspace scanner for repository understanding and stack detection."""
import json
import logging
import os
from pathlib import Path

from app.schemas.context import DependencyInfo, ProjectSummary

logger = logging.getLogger(__name__)

DEFAULT_IGNORE_DIRS: set[str] = {
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    "dist",
    "build",
    ".pytest_cache",
    ".ruff_cache",
    ".idea",
    ".vscode",
    "coverage",
    "out",
    ".mypy_cache",
    ".next",
    ".nuxt",
}

DEFAULT_IGNORE_EXTENSIONS: set[str] = {
    ".exe",
    ".dll",
    ".so",
    ".dylib",
    ".bin",
    ".iso",
    ".tar",
    ".gz",
    ".zip",
    ".7z",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".ico",
    ".pdf",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
    ".mp4",
    ".mp3",
    ".wav",
    ".db",
    ".db-wal",
    ".db-shm",
    ".db-journal",
    ".sqlite",
    ".sqlite3",
    ".pyc",
    ".pyo",
    ".pyd",
    ".map",
}

EXTENSION_TO_LANGUAGE: dict[str, str] = {
    ".py": "Python",
    ".ts": "TypeScript",
    ".tsx": "TypeScript (React)",
    ".js": "JavaScript",
    ".jsx": "JavaScript (React)",
    ".rs": "Rust",
    ".go": "Go",
    ".java": "Java",
    ".c": "C",
    ".h": "C/C++ Header",
    ".cpp": "C++",
    ".hpp": "C++ Header",
    ".cs": "C#",
    ".html": "HTML",
    ".css": "CSS",
    ".scss": "SCSS",
    ".json": "JSON",
    ".yaml": "YAML",
    ".yml": "YAML",
    ".md": "Markdown",
    ".sql": "SQL",
    ".sh": "Shell",
    ".bash": "Shell",
    ".ps1": "PowerShell",
}

KNOWN_ENTRY_POINT_NAMES = {
    "main.py",
    "app.py",
    "run.py",
    "index.ts",
    "main.ts",
    "index.js",
    "main.js",
    "server.js",
    "main.go",
    "main.rs",
    "index.html",
}

KNOWN_CONFIG_NAMES = {
    "package.json",
    "tsconfig.json",
    "requirements.txt",
    "pyproject.toml",
    "setup.py",
    "Cargo.toml",
    "go.mod",
    "Dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
    ".env.example",
    "ruff.toml",
    "pytest.ini",
}


class WorkspaceScanner:
    """Scans and analyzes an entire repository workspace."""

    def __init__(
        self,
        workspace_root: Path,
        custom_ignore_dirs: set[str] | None = None,
        custom_ignore_extensions: set[str] | None = None,
    ):
        self.workspace_root = workspace_root.resolve()
        self.ignore_dirs = set(DEFAULT_IGNORE_DIRS)
        if custom_ignore_dirs:
            self.ignore_dirs.update(custom_ignore_dirs)

        self.ignore_extensions = set(DEFAULT_IGNORE_EXTENSIONS)
        if custom_ignore_extensions:
            self.ignore_extensions.update(custom_ignore_extensions)

    def scan(self) -> ProjectSummary:
        """Perform comprehensive scan of the workspace."""
        if not self.workspace_root.exists() or not self.workspace_root.is_dir():
            return ProjectSummary(
                workspace_root=str(self.workspace_root),
                primary_language=None,
                total_files_scanned=0,
            )

        languages: dict[str, int] = {}
        entry_points: list[str] = []
        config_files: list[str] = []
        test_dirs: list[str] = []
        dependencies: list[DependencyInfo] = []
        frameworks: set[str] = set()

        all_relative_files: list[str] = []

        # Recursively walk workspace
        for root, dirs, files in os.walk(self.workspace_root):
            # In-place directory filtering
            dirs[:] = [d for d in dirs if d not in self.ignore_dirs and not d.startswith(".git")]

            rel_root = os.path.relpath(root, self.workspace_root)
            if rel_root != ".":
                # Check for test directories
                dir_name = os.path.basename(root).lower()
                if dir_name in {"tests", "test", "__tests__", "spec"}:
                    test_dirs.append(rel_root.replace("\\", "/"))

            for file in files:
                ext = os.path.splitext(file)[1].lower()
                if ext in self.ignore_extensions:
                    continue

                full_path = Path(root) / file
                rel_path = full_path.relative_to(self.workspace_root).as_posix()
                all_relative_files.append(rel_path)

                # Track language
                if ext in EXTENSION_TO_LANGUAGE:
                    lang = EXTENSION_TO_LANGUAGE[ext]
                    languages[lang] = languages.get(lang, 0) + 1

                # Track entry points
                if file.lower() in KNOWN_ENTRY_POINT_NAMES or rel_path.startswith("src/index") or rel_path.startswith("src/main"):
                    entry_points.append(rel_path)

                # Track config files
                if file in KNOWN_CONFIG_NAMES:
                    config_files.append(rel_path)

        # Detect tech stacks and dependencies
        detected_deps, detected_fw = self._inspect_package_manifests()
        dependencies.extend(detected_deps)
        frameworks.update(detected_fw)

        # Determine primary language
        primary_lang = None
        if languages:
            primary_lang = max(languages.items(), key=lambda item: item[1])[0]

        # Build clean directory tree
        structure_tree = self._build_directory_tree(all_relative_files)

        return ProjectSummary(
            workspace_root=str(self.workspace_root),
            languages=languages,
            primary_language=primary_lang,
            frameworks=sorted(list(frameworks)),
            entry_points=sorted(entry_points),
            config_files=sorted(config_files),
            test_directories=sorted(test_dirs),
            dependencies=dependencies,
            total_files_scanned=len(all_relative_files),
            structure_tree=structure_tree,
        )

    def _inspect_package_manifests(self) -> tuple[list[DependencyInfo], set[str]]:
        """Inspect manifests like package.json and requirements.txt for dependencies & frameworks."""
        deps: list[DependencyInfo] = []
        frameworks: set[str] = set()

        # Check package.json
        pkg_json = self.workspace_root / "package.json"
        if not pkg_json.exists():
            pkg_json = self.workspace_root / "extension" / "package.json"

        if pkg_json.exists():
            try:
                data = json.loads(pkg_json.read_text(encoding="utf-8", errors="replace"))
                raw_deps = data.get("dependencies", {})
                raw_dev_deps = data.get("devDependencies", {})

                for name, ver in raw_deps.items():
                    deps.append(DependencyInfo(name=name, version=str(ver), category="runtime"))
                    self._check_js_framework(name, frameworks)

                for name, ver in raw_dev_deps.items():
                    deps.append(DependencyInfo(name=name, version=str(ver), category="dev"))
                    self._check_js_framework(name, frameworks)

                if "vscode" in data.get("engines", {}):
                    frameworks.add("VS Code Extension API")
            except Exception as exc:
                logger.warning("Failed parsing package.json: %s", exc)

        # Check Python requirements
        req_txt = self.workspace_root / "requirements.txt"
        if not req_txt.exists():
            req_txt = self.workspace_root / "backend" / "requirements.txt"

        if req_txt.exists():
            try:
                lines = req_txt.read_text(encoding="utf-8", errors="replace").splitlines()
                for line in lines:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    clean_name = line.split(">=")[0].split("==")[0].split("<")[0].split("[")[0].strip()
                    if clean_name:
                        deps.append(DependencyInfo(name=clean_name, version=line, category="runtime"))
                        self._check_py_framework(clean_name, frameworks)
            except Exception as exc:
                logger.warning("Failed parsing requirements.txt: %s", exc)

        return deps, frameworks

    def _check_js_framework(self, name: str, frameworks: set[str]):
        name_lower = name.lower()
        if "react" in name_lower:
            frameworks.add("React")
        elif "vue" in name_lower:
            frameworks.add("Vue")
        elif "angular" in name_lower:
            frameworks.add("Angular")
        elif "express" in name_lower:
            frameworks.add("Express")
        elif "next" in name_lower:
            frameworks.add("Next.js")
        elif "typescript" in name_lower:
            frameworks.add("TypeScript")

    def _check_py_framework(self, name: str, frameworks: set[str]):
        name_lower = name.lower()
        if "fastapi" in name_lower:
            frameworks.add("FastAPI")
        elif "flask" in name_lower:
            frameworks.add("Flask")
        elif "django" in name_lower:
            frameworks.add("Django")
        elif "pydantic" in name_lower:
            frameworks.add("Pydantic")
        elif "pytest" in name_lower:
            frameworks.add("Pytest")
        elif "sqlalchemy" in name_lower:
            frameworks.add("SQLAlchemy")
        elif "httpx" in name_lower:
            frameworks.add("HTTPX")

    def _build_directory_tree(self, files: list[str], max_entries: int = 60) -> str:
        """Create a clean ASCII summary tree of repository structure."""
        if not files:
            return "(Empty workspace)"

        sorted_files = sorted(files)[:max_entries]
        lines = []
        for f in sorted_files:
            lines.append(f"  ├── {f}")

        if len(files) > max_entries:
            lines.append(f"  └── ... (+{len(files) - max_entries} more files)")

        return "\n".join(lines)
