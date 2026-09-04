```python
"""Planning and execution logic using LangGraph."""

import logging
import re
from models import Plan

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def create_plan(name: str) -> Plan:
    """Create a new plan with a list of steps."""
    steps = ["Step 1", "Step 2", "Step 3"]  # Example steps
    return Plan(name=name, steps=";".join(steps))

def validate_steps(steps: str) -> bool:
    """Validate the steps string to ensure it doesn't contain malicious content."""
    # Example validation: only allow alphanumeric, spaces, and semicolons
    return bool(re.match(r'^[a-zA-Z0-9\s;]*$', steps))

def execute_plan(plan: Plan) -> None:
    """Execute the given plan."""
    if not validate_steps(plan.steps):
        logger.error("Invalid steps detected, execution aborted.")
        return

    steps = plan.steps.split(";")
    for step in steps:
        # Execute each step (placeholder logic)
        logger.info(f"Executing: {step}")
```