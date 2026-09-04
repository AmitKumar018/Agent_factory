```python
"""Database models for planning and execution state."""

from sqlalchemy import Column, Integer, String, Text, create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from config import settings

Base = declarative_base()

class Plan(Base):
    """Model representing a planning artifact."""
    __tablename__ = 'plans'

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    steps = Column(Text)

class RunState(Base):
    """Model representing the state of a run."""
    __tablename__ = 'run_states'

    id = Column(Integer, primary_key=True, index=True)
    plan_id = Column(Integer)
    current_step = Column(Integer)

# Database setup
engine = create_engine(settings.database_url)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def init_db():
    """Initialize the database."""
    Base.metadata.create_all(bind=engine)
```