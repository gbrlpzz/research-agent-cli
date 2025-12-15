"""
LLM wrapper with Gemini OAuth support.

Provides a unified interface for all LLM calls with proper error handling.
For Gemini models, uses OAuth (Gemini plan quota) by default with API key fallback.
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

from .model_config import ensure_model_env, normalize_model_id

console = Console()

# Default timeout for API calls
API_TIMEOUT_SECONDS = int(os.getenv('API_TIMEOUT_SECONDS', '120'))

# Environment variable to disable OAuth (use API key instead)
# Environment variable to disable OAuth (use API key instead)
GEMINI_USE_OAUTH = os.getenv('GEMINI_USE_OAUTH', 'true').lower() not in ('false', '0', 'no')
_FALLBACK_CONFIRMED = False  # Track if user approved fallback

def set_oauth_enabled(enabled: bool) -> None:
    """Enable or disable Gemini OAuth dynamically."""
    global GEMINI_USE_OAUTH
    GEMINI_USE_OAUTH = enabled


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


# =============================================================================
# Message Format Conversion (OpenAI <-> Gemini)
# =============================================================================

def _openai_messages_to_gemini(
    messages: List[Dict[str, Any]]
) -> tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """
    Convert OpenAI-style messages to Gemini format.
    
    Returns (contents, system_instruction).
    """
    contents: List[Dict[str, Any]] = []
    system_instruction: Optional[Dict[str, Any]] = None
    
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content")
        tool_calls = msg.get("tool_calls")
        tool_call_id = msg.get("tool_call_id")
        
        if role == "system":
            # System message becomes systemInstruction
            if content:
                system_instruction = {"parts": [{"text": content}]}
            continue
        
        if role == "assistant":
            gemini_role = "model"
        elif role == "tool":
            gemini_role = "user"  # Tool responses come from user side
        else:
            gemini_role = "user"
        
        parts: List[Dict[str, Any]] = []
        
        # Optimization: Use raw Gemini parts if preserved (maintains signatures/thoughts)
        if "raw_gemini_parts" in msg:
            parts.extend(msg["raw_gemini_parts"])
            # Fallthrough to append logic
        
        else:
            # Handle text content
            if content:
                if isinstance(content, str):
                    parts.append({"text": content})
                elif isinstance(content, list):
                    # Handle multi-part content (e.g., with images)
                    for part in content:
                        if isinstance(part, dict):
                            if part.get("type") == "text":
                                parts.append({"text": part.get("text", "")})
                            # Add more content types as needed
                        elif isinstance(part, str):
                            parts.append({"text": part})
            
            # Handle tool calls (assistant requesting tools)
            if tool_calls:
                for tc in tool_calls:
                    func = tc.get("function", {})
                    args = func.get("arguments", "{}")
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except json.JSONDecodeError:
                            args = {}
                    parts.append({
                        "functionCall": {
                            "name": func.get("name", ""),
                            "args": args,
                        }
                    })
            
            # Handle tool response
            if role == "tool" and tool_call_id:
                # Find the function name from the previous assistant message
                func_name = tool_call_id  # We'll use tool_call_id as fallback
                # Look for the function name in previous messages
                for prev_msg in reversed(messages):
                    if prev_msg.get("tool_calls"):
                        for tc in prev_msg["tool_calls"]:
                            if tc.get("id") == tool_call_id:
                                func_name = tc.get("function", {}).get("name", tool_call_id)
                                break
                
                parts.append({
                    "functionResponse": {
                        "name": func_name,
                        "response": {"result": content if content else ""},
                    }
                })
        
        if parts:
            # Merge with previous message if same role (Critical for parallel tool calls)
            if contents and contents[-1]["role"] == gemini_role:
                contents[-1]["parts"].extend(parts)
            else:
                contents.append({"role": gemini_role, "parts": parts})
    
    return contents, system_instruction


def _openai_tools_to_gemini(tools: Optional[List[Dict[str, Any]]]) -> Optional[List[Dict[str, Any]]]:
    """Convert OpenAI-style tools to Gemini format."""
    if not tools:
        return None
    
    function_declarations = []
    for tool in tools:
        if tool.get("type") == "function":
            func = tool.get("function", {})
            function_declarations.append({
                "name": func.get("name", ""),
                "description": func.get("description", ""),
                "parameters": func.get("parameters", {}),
            })
    
    if function_declarations:
        return [{"functionDeclarations": function_declarations}]
    return None


def _gemini_response_to_openai(response: Dict[str, Any]) -> Dict[str, Any]:
    """Convert Gemini response to OpenAI-style message dict."""
    candidates = response.get("candidates", [])
    if not candidates:
        return {"role": "assistant", "content": None}
    
    candidate = candidates[0]
    content_data = candidate.get("content", {})
    parts = content_data.get("parts", [])
    
    text_parts = []
    tool_calls = []
    
    for i, part in enumerate(parts):
        if "text" in part:
            text_parts.append(part["text"])
        elif "functionCall" in part:
            fc = part["functionCall"]
            tool_calls.append({
                "id": f"call_{i}",
                "type": "function",
                "function": {
                    "name": fc.get("name", ""),
                    "arguments": json.dumps(fc.get("args", {})),
                },
            })
    
    result: Dict[str, Any] = {
        "role": "assistant",
        "content": "\n".join(text_parts) if text_parts else None,
        "raw_gemini_parts": parts,  # Preserve raw parts for round-trip fidelity
    }
    
    if tool_calls:
        result["tool_calls"] = tool_calls
    
    return result


# =============================================================================
# Gemini OAuth Call
# =============================================================================

def _call_gemini_oauth(
    model: str,
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]] = None,
    temperature: Optional[float] = None,
) -> Dict[str, Any]:
    """Call Gemini via OAuth (Cloud Code Assist API)."""
    from .gemini_oauth import gemini_generate_content
    
    # Convert messages to Gemini format
    contents, system_instruction = _openai_messages_to_gemini(messages)
    gemini_tools = _openai_tools_to_gemini(tools)
    
    # Build generation config
    generation_config: Optional[Dict[str, Any]] = None
    if temperature is not None:
        generation_config = {"temperature": temperature}
    
    # Make the call
    response = gemini_generate_content(
        model=model,
        contents=contents,
        system_instruction=system_instruction,
        generation_config=generation_config,
        tools=gemini_tools,
    )
    
    # Convert response back to OpenAI format
    return _gemini_response_to_openai(response)



def _should_use_gemini_oauth(model: str) -> bool:
    """Check if we should use Gemini OAuth for this model."""
    if not GEMINI_USE_OAUTH:
        return False
    
    # Only for Gemini models
    normalized = normalize_model_id(model)
    if not normalized.startswith("gemini/"):
        return False
    
    # Check if OAuth is available
    try:
        from .gemini_oauth import is_oauth_available
        return is_oauth_available()
    except ImportError:
        return False


def _call_antigravity_oauth(
    model: str,
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]] = None,
    temperature: Optional[float] = None,
) -> Dict[str, Any]:
    """Call Antigravity via OAuth."""
    from .antigravity_oauth import antigravity_generate_content
    
    # Convert messages to Gemini format (Antigravity uses same format)
    contents, system_instruction = _openai_messages_to_gemini(messages)
    gemini_tools = _openai_tools_to_gemini(tools)
    
    # Build generation config
    generation_config: Optional[Dict[str, Any]] = None
    if temperature is not None:
        generation_config = {"temperature": temperature}
    
    # Make the call
    response = antigravity_generate_content(
        model=model,
        contents=contents,
        system_instruction=system_instruction,
        generation_config=generation_config,
        tools=gemini_tools,
    )
    
    # Convert response back to OpenAI format
    return _gemini_response_to_openai(response)


def _should_use_antigravity_oauth(model: str) -> bool:
    """Check if we should use Antigravity OAuth."""
    normalized = normalize_model_id(model)
    if not normalized.startswith("antigravity/"):
        return False
        
    try:
        from .antigravity_oauth import is_oauth_available
        return is_oauth_available()
    except ImportError:
        return False


# =============================================================================
# Main LLM Chat Interface
# =============================================================================

def llm_chat(
    *,
    model: str,
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]] = None,
    temperature: Optional[float] = None,
    timeout_seconds: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Unified chat call, returning the assistant message dict.
    Compatible with OpenAI-style tool calling (tool_calls).
    
    For Gemini models:
    - Uses OAuth (Cloud Code Assist API) if configured
    - Falls back to LiteLLM with API key otherwise
    """
    # Try Gemini OAuth first for Gemini models
    if _should_use_gemini_oauth(model):
        try:
            return _call_gemini_oauth(model, messages, tools, temperature)
        except Exception as e:
            # Log and fall back to LiteLLM
            if "OAuth not configured" not in str(e):
                console.print(f"[dim]Gemini OAuth failed: {e}[/dim]")
                
                # Check for confirmation
                global _FALLBACK_CONFIRMED
                if not _FALLBACK_CONFIRMED:
                    from rich.prompt import Confirm
                    try:
                        if Confirm.ask("[yellow]Fallback to API Key (billed usage)?[/yellow]"):
                            _FALLBACK_CONFIRMED = True
                        else:
                            raise RuntimeError("Fallback aborted by user.")
                    except Exception:
                        # Handle potential stdin issues (e.g. non-interactive) by failing safe
                        raise RuntimeError(f"Gemini OAuth failed ({e}) and fallback confirmation unavailable.")

                console.print("[dim]Falling back to API key...[/dim]")

    # Try Antigravity OAuth
    if _should_use_antigravity_oauth(model):
        return _call_antigravity_oauth(model, messages, tools, temperature)
    
    # Fall back to LiteLLM
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
