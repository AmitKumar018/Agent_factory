from app.main import app

print('=== Codegen routes on app object ===')
codegen = [r for r in app.routes if hasattr(r, 'path') and 'codegen' in r.path]
print(f'Count: {len(codegen)}')
for r in codegen:
    print(f'  {getattr(r, "methods", "?")}  {r.path}')

print()
print('=== All routes containing project_id ===')
for r in app.routes:
    if hasattr(r, 'path') and 'project_id' in r.path:
        print(f'  {r.path}')
