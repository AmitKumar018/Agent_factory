import json
from pydantic_settings import BaseSettings
from pydantic import Field
from functools import lru_cache


class Settings(BaseSettings):
    """
    All application configuration loaded from environment variables.
    Uses pydantic-settings so values can come from .env file or real env vars.

    IMPORTANT: All fields are UPPERCASE so they can be accessed consistently
    as settings.FIELD_NAME throughout the codebase.
    """

    # Gemini/Google GenAI
    GEMINI_API_KEY: str = Field("dev-gemini-key-not-set", env="GEMINI_API_KEY")
    GOOGLE_API_KEY: str = Field("dev-gemini-key-not-set", env="GOOGLE_API_KEY")
    OPENAI_API_KEY: str = Field("dev-openai-key-not-set", env="OPENAI_API_KEY")

    #JWT 
    JWT_SECRET: str = Field("dev-jwt-secret-change-me", env="JWT_SECRET")
    JWT_ALGORITHM: str = Field("HS256", env="JWT_ALGORITHM")
    JWT_EXPIRE_MINUTES: int = Field(1440, env="JWT_EXPIRE_MINUTES")

    #  Database paths
    SQLITE_DB_PATH: str = Field("./data/app.sqlite", env="SQLITE_DB_PATH")

    
    CHECKPOINT_DB_PATH: str = Field("./data/checkpoints.sqlite", env="CHECKPOINT_DB_PATH")

    
    DATA_ROOT: str = Field("./data/projects", env="DATA_ROOT")

    # App 
    APP_ENV: str = Field("development", env="APP_ENV")
    LOG_LEVEL: str = Field("INFO", env="LOG_LEVEL")

    #Cost / token ceilings per run 
    MAX_TOKENS_PER_RUN: int = Field(200000, env="MAX_TOKENS_PER_RUN")
    MAX_COST_USD_PER_RUN: float = Field(5.00, env="MAX_COST_USD_PER_RUN")

    # Workflow iteration limits
    
    MAX_CLARIFICATION_ROUNDS: int = Field(3, env="MAX_CLARIFICATION_ROUNDS")

    
    MAX_CRITIC_ITERATIONS: int = Field(3, env="MAX_CRITIC_ITERATIONS")

    MAX_REVIEWER_RETRIES: int = Field(3, env="MAX_REVIEWER_RETRIES")

   
    MAX_REFLECTION_ITERATIONS: int = Field(3, env="MAX_REFLECTION_ITERATIONS")

    # Default LLM model
    DEFAULT_MODEL: str = Field("gemini-2.5-flash", env="DEFAULT_MODEL")

    #Model pricing 
    MODEL_PRICING_RAW: str = Field(
        '{"gemini-2.5-flash":{"input":0.00015,"output":0.0006},"gemini-1.5-flash":{"input":0.000075,"output":0.0003},"gemini-2.5-pro":{"input":0.00125,"output":0.005},"gemini-1.5-pro":{"input":0.00125,"output":0.005},"gpt-4o":{"input":0.005,"output":0.015}}',
        env="MODEL_PRICING"
    )

    @property
    def MODEL_PRICING(self) -> dict:
        """Parse the JSON pricing string into a usable dict."""
        return json.loads(self.MODEL_PRICING_RAW)

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"   # ignore unknown env vars


@lru_cache()
def get_settings() -> Settings:
    """
    Returns a cached singleton of Settings.
    Using lru_cache means we only parse env vars once per process.
    """
    return Settings()


# Module-level singleton so callers can do:
#   from app.config import settings
settings = get_settings()
