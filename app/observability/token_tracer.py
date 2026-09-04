"""
app/observability/token_tracer.py
Token usage tracing stub.
"""

from __future__ import annotations
import logging
from typing import Any, Optional, Dict

logger = logging.getLogger(__name__)


def extract_usage_from_response(response: Any) -> Optional[Dict[str, int]]:
    """Extracts token usage from an LLM API response object."""
    try:
        if hasattr(response, "usage") and response.usage is not None:
            return {
                "prompt_tokens": getattr(response.usage, "prompt_tokens", 0),
                "completion_tokens": getattr(response.usage, "completion_tokens", 0),
                "total_tokens": getattr(response.usage, "total_tokens", 0),
            }
    except Exception as e:
        logger.warning(f"Could not extract token usage: {e}")
    return None


def trace_tokens(node_name: str, usage: Any = None) -> None:
    """Traces token usage for a given node."""
    logger.debug('token_trace', extra={'node': node_name, 'usage': usage})


def get_token_tracer():
    """Returns a token tracer instance (stub)."""
    return None
