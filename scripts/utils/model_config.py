"""
Model configuration + routing utilities.

This repo uses two conceptual model roles:
- reasoning: planning/writing/reviewing tool-using agents
- rag: PaperQA (retrieval + synthesis over the local library)

Defaults are set to Gemini 2.5 Pro for reasoning and Gemini 2.5 Flash for RAG.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Iterable, List, Optional


DEFAULT_REASONING_MODEL = "gemini/gemini-3-pro-preview"
DEFAULT_RAG_MODEL = "gemini/gemini-2.5-flash"
DEFAULT_EMBEDDING_MODEL = "gemini/text-embedding-004"

try:
    import litellm
    # Silence "model not mapped" warnings for our default embedding model
    litellm.model_cost["gemini/text-embedding-004"] = {
        "input_cost_per_token": 0.0,
        "output_cost_per_token": 0.0,
        "litellm_provider": "gemini",
        "mode": "embedding"
    }
except ImportError:
    pass


def normalize_model_id(model: str) -> str:
    """
    Normalize common shorthand model ids into LiteLLM-style provider/model ids.

    Examples:
      - "gpt-5.2-high" -> "openai/gpt-5.2-high"
      - "gemini-2.5-flash" -> "gemini/gemini-2.5-flash"
      - "openai/gpt-5.2-fast" -> unchanged
    """
    model = (model or "").strip()
    if not model:
        return model
    if "/" in model:
        return model

    # Heuristics for common providers
    if model.startswith(("gpt-", "o", "text-embedding-")):
        return f"openai/{model}"
    if model.startswith("gemini"):
        return f"gemini/{model}"
    if model.startswith("antigravity"):
        return f"antigravity/{model}" if "/" not in model else model

    # Unknown provider; leave as-is
    return model


def _provider_from_model(model: str) -> Optional[str]:
    model = (model or "").strip()
    if not model:
        return None
    if "/" in model:
        return model.split("/", 1)[0]
    return None


def required_env_vars_for_model(model: str) -> List[str]:
    """
    Return a list of env vars that must be present to call this provider.
    We allow multiple options for some providers (e.g. Gemini).
    """
    model = normalize_model_id(model)
    provider = _provider_from_model(model)
    if provider == "openai":
        return ["OPENAI_API_KEY"]
    if provider == "gemini":
        # LiteLLM commonly reads GEMINI_API_KEY; many users also set GOOGLE_API_KEY.
        return ["GEMINI_API_KEY", "GOOGLE_API_KEY"]
    if provider == "antigravity":
        return ["ANTIGRAVITY_OAUTH"]  # Placeholder for OAuth check
    return []


def ensure_model_env(model: str) -> None:
    """
    Raise a helpful error if the required provider key isn't configured.
    For Gemini, OAuth authentication is also accepted as an alternative to API keys.
    """
    required = required_env_vars_for_model(model)
    if not required:
        return

    # If ANY of the required vars is set, consider it satisfied.
    if any(os.getenv(k) for k in required):
        return
    
    # For Gemini, also check if OAuth is available
    provider = _provider_from_model(normalize_model_id(model))
    if provider == "gemini":
        try:
            from .gemini_oauth import is_oauth_available
            if is_oauth_available():
                return  # OAuth is configured, no API key needed
        except ImportError:
            pass
    
    # For Antigravity, check OAuth
    if provider == "antigravity":
        try:
            from .antigravity_oauth import is_oauth_available
            if is_oauth_available():
                return
        except ImportError:
            pass

    provider = provider or "unknown"
    opts = " or ".join(required)
    provider = provider or "unknown"
    opts = " or ".join(required)
    if provider == "antigravity":
        raise RuntimeError(f"Antigravity OAuth not configured. Run 'research antigravity-login'.")
    raise RuntimeError(f"Missing API key for provider '{provider}'. Set {opts}.")


@dataclass(frozen=True)
class ModelRouting:
    reasoning_model: str
    rag_model: str
    embedding_model: str

    @staticmethod
    def from_env(
        *,
        reasoning_model: Optional[str] = None,
        rag_model: Optional[str] = None,
        embedding_model: Optional[str] = None,
    ) -> "ModelRouting":
        """
        Compute routing from explicit args, env vars, and defaults.

        Env vars:
          - RESEARCH_REASONING_MODEL
          - RESEARCH_RAG_MODEL
          - RESEARCH_EMBEDDING_MODEL
        """
        rm = reasoning_model or os.getenv("RESEARCH_REASONING_MODEL") or DEFAULT_REASONING_MODEL
        ragm = rag_model or os.getenv("RESEARCH_RAG_MODEL") or DEFAULT_RAG_MODEL
        em = embedding_model or os.getenv("RESEARCH_EMBEDDING_MODEL") or DEFAULT_EMBEDDING_MODEL

        rm = normalize_model_id(rm)
        ragm = normalize_model_id(ragm)
        em = normalize_model_id(em)

        return ModelRouting(reasoning_model=rm, rag_model=ragm, embedding_model=em)


def apply_routing_to_env(routing: ModelRouting) -> None:
    """
    Push routing into environment variables so subprocesses / imported modules
    see the same model configuration.
    """
    os.environ["RESEARCH_REASONING_MODEL"] = routing.reasoning_model
    os.environ["RESEARCH_RAG_MODEL"] = routing.rag_model
    os.environ["RESEARCH_EMBEDDING_MODEL"] = routing.embedding_model


