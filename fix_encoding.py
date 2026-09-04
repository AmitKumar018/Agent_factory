import pathlib
files = list(pathlib.Path('app').rglob('*.py'))
fixed = 0
for f in files:
    try:
        f.read_text(encoding='utf-8')
    except UnicodeDecodeError:
        content = f.read_text(encoding='cp1252')
        f.write_text(content, encoding='utf-8')
        print(f'Re-encoded: {f}')
        fixed += 1
print(f'Done — {fixed} file(s) re-encoded to UTF-8')
