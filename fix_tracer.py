import os

content = (
    '"""\n'
    'app/observability/token_tracer.py\n'
    'Token usage tracing stub.\n'
    '"""\n\n'
    'from __future__ import annotations\n'
    'import logging\n'
    'from typing import Any, Optional, Dict\n\n'
    'logger = logging.getLogger(__name__)\n\n\n'
    'def extract_usage_from_response(response: Any) -> Optional[Dict[str, int]]:\n'
    '    """Extracts token usage from an LLM API response object."""\n'
    '    try:\n'
    '        if hasattr(response, "usage") and response.usage is not None:\n'
    '            return {\n'
    '                "prompt_tokens": getattr(response.usage, "prompt_tokens", 0),\n'
    '                "completion_tokens": getattr(response.usage, "completion_tokens", 0),\n'
    '                "total_tokens": getattr(response.usage, "total_tokens", 0),\n'
    '            }\n'
    '    except Exception as e:\n'
    '        logger.warning(f"Could not extract token usage: {e}")\n'
    '    return None\n\n\n'
    'def trace_tokens(node_name: str, usage: Any = None) -> None:\n'
    '    """Traces token usage for a given node."""\n'
    "    logger.debug('token_trace', extra={'node': node_name, 'usage': usage})\n\n\n"
    'def get_token_tracer():\n'
    '    """Returns a token tracer instance (stub)."""\n'
    '    return None\n'
)

os.makedirs('app/observability', exist_ok=True)
with open('app/observability/token_tracer.py', 'w', encoding='utf-8') as f:
    f.write(content)
with open('app/observability/__init__.py', 'a', encoding='utf-8') as f:
    pass
print('Done')