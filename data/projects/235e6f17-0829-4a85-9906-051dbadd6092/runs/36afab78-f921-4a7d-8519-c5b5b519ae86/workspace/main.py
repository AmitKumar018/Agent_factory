```python
"""Main module for the FastAPI application."""

from fastapi import FastAPI, HTTPException, Depends
from sqlalchemy.orm import Session
from models import Plan, RunState, SessionLocal, init_db
from planning import create_plan, execute_plan
from pydantic import BaseModel, constr

app = FastAPI()

# Dependency to get DB session
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.on_event("startup")
def on_startup():
    """Initialize the database on startup."""
    init_db()

class PlanCreateRequest(BaseModel):
    """Request model for creating a new plan."""
    name: constr(strip_whitespace=True, min_length=1, max_length=100)

@app.post("/plans/", response_model=int)
async def create_new_plan(request: PlanCreateRequest, db: Session = Depends(get_db)):
    """Create a new plan."""
    plan = create_plan(request.name)
    db.add(plan)
    db.commit()
    db.refresh(plan)
    return plan.id

@app.post("/execute/{plan_id}")
async def execute_existing_plan(plan_id: int, db: Session = Depends(get_db)):
    """Execute an existing plan."""
    plan = db.query(Plan).filter(Plan.id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    execute_plan(plan)
    return {"status": "Execution started"}
```