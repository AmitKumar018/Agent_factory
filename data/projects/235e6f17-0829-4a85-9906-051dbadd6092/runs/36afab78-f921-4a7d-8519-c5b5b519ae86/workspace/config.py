```python
"""Configuration module for environment variables and settings."""

import os
from pydantic import BaseSettings

class Settings(BaseSettings):
    """Application settings."""
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./test.db")
    artifact_store_path: str = os.getenv("ARTIFACT_STORE_PATH", "./artifacts")

settings = Settings()
```