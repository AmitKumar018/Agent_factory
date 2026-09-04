import pathlib, re, sys, textwrap

# ══════════════════════════════════════════════════════════════════
# PART A — Fix Settings: add DATA_ROOT (and any other missing attrs)
# ══════════════════════════════════════════════════════════════════
settings_file = None
for f in pathlib.Path('app').rglob('*.py'):
    txt = f.read_text(encoding='utf-8', errors='ignore')
    if 'class Settings' in txt:
        settings_file = f
        print(f'[INFO] Settings class found in: {f}')
        # Print existing fields so we can see what is/isn't there
        for line in txt.splitlines():
            if re.match(r'\s+[A-Z_]+\s*[:=]', line):
                print(f'       {line.strip()}')
        break

if not settings_file:
    print('[ERROR] Settings class not found'); sys.exit(1)

settings_txt = settings_file.read_text(encoding='utf-8')

# Build list of attributes to inject if missing
attrs_to_add = {
    'DATA_ROOT':       'DATA_ROOT: str = "./data"',
    'ARTIFACTS_ROOT':  'ARTIFACTS_ROOT: str = "./data/artifacts"',
    'CHECKPOINTS_DIR': 'CHECKPOINTS_DIR: str = "./data/checkpoints"',
    'CHROMADB_PATH':   'CHROMADB_PATH: str = "./data/chromadb"',
    'UPLOADS_DIR':     'UPLOADS_DIR: str = "./data/uploads"',
}

injections = []
for attr, definition in attrs_to_add.items():
    if attr not in settings_txt:
        injections.append(f'    {definition}')
        print(f'[ADD] {attr}')
    else:
        print(f'[SKIP] {attr} already present')

if injections:
    # Insert after 'class Settings' line
    lines = settings_txt.splitlines()
    new_lines = []
    inserted = False
    for i, line in enumerate(lines):
        new_lines.append(line)
        if not inserted and 'class Settings' in line:
            # Find the line after the class declaration (skip docstring/pass)
            new_lines.extend(injections)
            inserted = True
    settings_file.write_text('\n'.join(new_lines), encoding='utf-8')
    print(f'[FIXED] {settings_file} — {len(injections)} attribute(s) added')
else:
    print('[OK] Settings already has all required attributes')

# ══════════════════════════════════════════════════════════════════
# PART B — Diagnose graph.py: print all imports to catch next error
# ══════════════════════════════════════════════════════════════════
graph_path = pathlib.Path('app/workflows/requirements/graph.py')
if graph_path.exists():
    graph_txt = graph_path.read_text(encoding='utf-8', errors='ignore')
    print('\n[INFO] Imports in graph.py:')
    for line in graph_txt.splitlines():
        if line.strip().startswith(('import ', 'from ')):
            print(f'  {line.strip()}')
else:
    print('[WARN] graph.py not found at expected path')

# ══════════════════════════════════════════════════════════════════
# PART C — Syntax-check ALL app .py files right now
# ══════════════════════════════════════════════════════════════════
import ast
print('\n[INFO] Syntax-checking all app .py files...')
errors = []
for f in sorted(pathlib.Path('app').rglob('*.py')):
    try:
        ast.parse(f.read_text(encoding='utf-8', errors='replace'))
    except SyntaxError as e:
        errors.append(f'  SYNTAX ERROR in {f}: {e}')

if errors:
    print('[WARN] Files with syntax errors:')
    for e in errors:
        print(e)
else:
    print('[OK] All files pass syntax check')

print('\nAll done. Restart uvicorn now.')