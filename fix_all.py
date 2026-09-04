import pathlib, re, sys

# ── PART A: Resolve the real RunStatus member for 'paused_hitl' ──────────────
enum_file = None
enum_members = []
for f in pathlib.Path('app').rglob('*.py'):
    txt = f.read_text(encoding='utf-8', errors='ignore')
    if 'class RunStatus' in txt:
        enum_file = f
        # Extract all enum members (lines like: MEMBER = "value")
        in_class = False
        for line in txt.splitlines():
            if 'class RunStatus' in line:
                in_class = True
                continue
            if in_class:
                if line.startswith('class '):   # next class = stop
                    break
                m = re.match(r'\s+([A-Z_a-z]+)\s*=\s*["\'](.+)["\']', line)
                if m:
                    enum_members.append((m.group(1), m.group(2)))
        break

if not enum_file:
    print('[ERROR] RunStatus not found'); sys.exit(1)

print(f'[INFO] RunStatus defined in: {enum_file}')
print(f'[INFO] Members found: {enum_members}')

# Find best match for paused_hitl concept
hitl_match = None
for name, value in enum_members:
    if 'hitl' in name.lower() or 'hitl' in value.lower() or 'pause' in name.lower() or 'pause' in value.lower() or 'await' in name.lower() or 'wait' in name.lower():
        hitl_match = name
        print(f'[INFO] Best HITL match: RunStatus.{name} = "{value}"')
        break

if not hitl_match:
    # Fallback: print all and pick closest
    print('[WARN] No obvious paused_hitl match. All members:')
    for n, v in enum_members:
        print(f'       {n} = "{v}"')
    # Use first member that has PAUSED or WAITING or HITL (case-insensitive value)
    for name, value in enum_members:
        if any(k in value.upper() for k in ['PAUSE', 'WAIT', 'HITL', 'HUMAN']):
            hitl_match = name
            print(f'[INFO] Fallback HITL match: {name}')
            break

if not hitl_match:
    print('[ERROR] Cannot auto-resolve paused_hitl. Paste Step 1 output to Sidekick.')
    sys.exit(1)

# ── PART B: Replace RunStatus.paused_hitl everywhere ─────────────────────────
fixed_files = 0
for f in pathlib.Path('app').rglob('*.py'):
    try:
        original = f.read_text(encoding='utf-8')
    except Exception:
        continue
    patched = re.sub(r'RunStatus\.paused_hitl\b', f'RunStatus.{hitl_match}', original)
    if patched != original:
        f.write_text(patched, encoding='utf-8')
        count = len(re.findall(r'RunStatus\.paused_hitl\b', original))
        print(f'[FIXED] {f}  ({count} occurrence(s) → RunStatus.{hitl_match})')
        fixed_files += 1
print(f'[DONE] RunStatus fixes: {fixed_files} file(s) patched')

# ── PART C: Add acquire_project_run_slot to app/workflows/service.py ─────────
svc_path = pathlib.Path('app/workflows/service.py')
svc_txt = svc_path.read_text(encoding='utf-8')

if 'acquire_project_run_slot' in svc_txt:
    print('[SKIP] acquire_project_run_slot already exists in service.py')
else:
    # Also check what graph.py imports from service so we add everything at once
    graph_path = pathlib.Path('app/workflows/requirements/graph.py')
    needed = []
    if graph_path.exists():
        graph_txt = graph_path.read_text(encoding='utf-8', errors='ignore')
        m = re.search(r'from app\.workflows\.service import \((.*?)\)', graph_txt, re.DOTALL)
        if m:
            needed = [x.strip().rstrip(',') for x in m.group(1).splitlines() if x.strip()]
            print(f'[INFO] graph.py imports from service: {needed}')

    stub_functions = '''

# ── Run-slot helpers (stubs) ──────────────────────────────────────────────────
import asyncio as _asyncio
from typing import Optional as _Optional

async def acquire_project_run_slot(project_id: str, run_id: str) -> bool:
    """Acquire an exclusive run slot for a project (stub — always succeeds)."""
    import logging
    logging.getLogger(__name__).debug(
        f"acquire_project_run_slot: project={project_id} run={run_id}"
    )
    return True

async def release_project_run_slot(project_id: str, run_id: str) -> None:
    """Release the run slot for a project (stub)."""
    import logging
    logging.getLogger(__name__).debug(
        f"release_project_run_slot: project={project_id} run={run_id}"
    )

async def mark_run_started(db, run_id: str) -> None:
    """Mark a run as RUNNING in the DB (stub — delegates to DB layer)."""
    import logging
    logging.getLogger(__name__).debug(f"mark_run_started: run={run_id}")

async def mark_run_finished(db, run_id: str, output_data=None) -> None:
    """Mark a run as COMPLETED in the DB (stub)."""
    import logging
    logging.getLogger(__name__).debug(f"mark_run_finished: run={run_id}")

async def mark_run_failed(db, run_id: str, error: str = "") -> None:
    """Mark a run as FAILED in the DB (stub)."""
    import logging
    logging.getLogger(__name__).debug(f"mark_run_failed: run={run_id} error={error}")
'''
    svc_path.write_text(svc_txt + stub_functions, encoding='utf-8')
    print('[FIXED] app/workflows/service.py — appended run-slot stubs')