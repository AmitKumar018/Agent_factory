import os

SKIP_DIRS = {"__pycache__", ".venv", "venv", ".gitignore", "node_modules", "data", ".idea", ".mypy_cache", "dist", "build", ".pytest_cache"}
SKIP_EXTS = {".pyc", ".pyo", ".egg-info"}

for root, dirs, files in os.walk("."):
    dirs[:] = sorted([d for d in dirs if d not in SKIP_DIRS])
    level = root.replace(".", "").count(os.sep)
    indent = "    " * level
    print(f"{indent}{os.path.basename(root)}/")
    for file in sorted(files):
        if not any(file.endswith(ext) for ext in SKIP_EXTS):
            print(f"    {indent}{file}")
