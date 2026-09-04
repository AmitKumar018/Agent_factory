import pathlib, re, sys

# ── Step 1: Find where RunStatus is defined and print its members ─────────────
def find_enum_members():
    for f in pathlib.Path('app').rglob('*.py'):
        text = f.read_text(encoding='utf-8', errors='ignore')
        if 'class RunStatus' in text:
            print(f'[INFO] RunStatus defined in: {f}')
            for line in text.splitlines():
                if '=' in line and 'class' not in line and 'RunStatus' not in line:
                    stripped = line.strip()
                    if stripped and not stripped.startswith('#'):
                        # Print only lines inside the enum block (heuristic: short assignment lines)
                        if len(stripped) < 80:
                            print(f'       {stripped}')
            return text
    print('[ERROR] RunStatus class not found!')
    sys.exit(1)

find_enum_members()

# ── Step 2: Build the wrong->right mapping (lowercase → UPPER) ───────────────
# Common status names used in router.py with wrong casing
replacements = {
    r'RunStatus\.pending\b':    'RunStatus.PENDING',
    r'RunStatus\.running\b':    'RunStatus.RUNNING',
    r'RunStatus\.completed\b':  'RunStatus.COMPLETED',
    r'RunStatus\.failed\b':     'RunStatus.FAILED',
    r'RunStatus\.cancelled\b':  'RunStatus.CANCELLED',
    r'RunStatus\.paused\b':     'RunStatus.PAUSED',
    r'RunStatus\.waiting\b':    'RunStatus.WAITING',
    r'RunStatus\.success\b':    'RunStatus.SUCCESS',
    r'RunStatus\.error\b':      'RunStatus.ERROR',
}

# ── Step 3: Apply to ALL .py files under app/ ─────────────────────────────────
total_fixes = 0
for f in pathlib.Path('app').rglob('*.py'):
    try:
        original = f.read_text(encoding='utf-8')
    except Exception:
        continue
    patched = original
    for pattern, replacement in replacements.items():
        patched = re.sub(pattern, replacement, patched)
    if patched != original:
        f.write_text(patched, encoding='utf-8')
        fixes = sum(len(re.findall(p, original)) for p in replacements)
        print(f'[FIXED] {f}  ({fixes} replacement(s))')
        total_fixes += 1

print(f'\nDone — {total_fixes} file(s) patched.')