from app.main import app
from app.routers import codegen  # adjust import path if different

print('=== codegen_router routes ===')
for r in codegen.router.routes:
    print(f'  {getattr(r, "methods", "WS")}  {r.path}')

print()
print('=== app routes with project_id (last registered) ===')
for r in app.routes:
    if hasattr(r, 'path') and 'project_id' in r.path:
        print(f'  {r.path}')
