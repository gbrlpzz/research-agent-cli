"""
LLM wrapper for LiteLLM.

Provides a unified interface for all LLM calls with proper error handling.
"""
import json
import os
from typing import Any, Dict, List, Optional

from rich.console import Console

# Import LiteLLM lazily
try:
    import litellm  # type: ignore
except ImportError:
    litellm = None

from .model_config import ensure_model_env

console = Console()

# Default timeout for API calls
API_TIMEOUT_SECONDS = int(os.getenv('API_TIMEOUT_SECONDS', '120'))


def _require_litellm() -> None:
    """Ensure LiteLLM is installed."""
    if litellm is None:
        raise RuntimeError(
            "LiteLLM is required but not installed. Install dependencies with: pip install -r requirements.txt"
        )


def _safe_json_loads(s: Any) -> Dict[str, Any]:
    """Safely parse JSON from any input, returning empty dict on failure."""
    if s is None:
        return {}
    if isinstance(s, dict):
        return s
    if not isinstance(s, str):
        return {}
    s = s.strip()
    if not s:
        return {}
    try:
        return json.loads(s)
    except Exception:
        return {}


def llm_chat(
    *,
    model: str,
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]] = None,
    temperature: Optional[float] = None,
    timeout_seconds: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Unified chat call via LiteLLM, returning the assistant message dict.
    Compatible with OpenAI-style tool calling (tool_calls).
    """
    _require_litellm()
    ensure_model_env(model)

    # Reduce noisy provider debug output + drop unsupported params automatically.
    try:
        litellm.suppress_debug_info = True
        litellm.set_verbose = False
        litellm.drop_params = True
    except Exception:
        pass

    # GPT-5 via LiteLLM often rejects temperature values other than 1. Omitting is safest.
    temp_to_send: Optional[float] = temperature
    if model and "/gpt-5" in model and temperature is not None and float(temperature) != 1.0:
        temp_to_send = None

    try:
        kwargs: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "tools": tools,
            "timeout": timeout_seconds or API_TIMEOUT_SECONDS,
        }
        if temp_to_send is not None:
            kwargs["temperature"] = temp_to_send
        resp = litellm.completion(**kwargs)
    except Exception as e:
        msg = str(e)
        # Fail fast on model access issues
        if "does not exist" in msg or "do not have access" in msg or "NotFoundError" in type(e).__name__:
            raise RuntimeError(
                f"Model not available: {model}. Set RESEARCH_REASONING_MODEL / RESEARCH_RAG_MODEL to a model your API key can access."
            ) from e
        raise

    # Track token usage if orchestrator is active
    try:
        from phases.orchestrator import get_orchestrator
        orch = get_orchestrator()
        if orch and orch._current_phase and hasattr(resp, 'usage') and resp.usage:
            usage = resp.usage
            input_tokens = getattr(usage, 'prompt_tokens', 0) or 0
            output_tokens = getattr(usage, 'completion_tokens', 0) or 0
            orch.record_tokens(orch._current_phase, input_tokens, output_tokens)
    except Exception:
        pass  # Orchestrator may not be initialized

    # LiteLLM returns OpenAI-like payloads
    return resp["choices"][0]["message"]
