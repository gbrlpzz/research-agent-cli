"""
Antigravity OAuth Authentication for Research Agent.

Implements OAuth authentication using Google's Antigravity (IDE) credentials,
allowing users to access models like claude-opus-4-5-thinking.
"""

import base64
import hashlib
import http.server
import json
import os
import secrets
import threading
import time
import urllib.parse
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from rich.console import Console

console = Console()

# =============================================================================
# OAuth Constants (from opencode-antigravity-auth)
# =============================================================================

ANTIGRAVITY_CLIENT_ID = os.getenv("ANTIGRAVITY_CLIENT_ID")
ANTIGRAVITY_CLIENT_SECRET = os.getenv("ANTIGRAVITY_CLIENT_SECRET")
ANTIGRAVITY_SCOPES = [
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/cclog",
    "https://www.googleapis.com/auth/experimentsandconfigs",
]
# Use the port from opencode-antigravity-auth
ANTIGRAVITY_REDIRECT_URI = "http://localhost:51121/oauth-callback"
ANTIGRAVITY_CODE_ASSIST_ENDPOINT = "https://cloudcode-pa.googleapis.com"

ANTIGRAVITY_HEADERS = {
    "User-Agent": "antigravity/1.11.5 windows/amd64",
    "X-Goog-Api-Client": "google-cloud-sdk vscode_cloudshelleditor/0.1",
    "Client-Metadata": '{"ideType":"IDE_UNSPECIFIED","platform":"PLATFORM_UNSPECIFIED","pluginType":"GEMINI"}',
}

# Token storage location
TOKEN_FILE = Path.home() / ".antigravity-oauth.json"

# Token expiry buffer (refresh 5 minutes before expiry)
EXPIRY_BUFFER_SECONDS = 300


# =============================================================================
# PKCE Implementation
# =============================================================================

def generate_pkce() -> Tuple[str, str]:
    """Generate PKCE code verifier and challenge."""
    verifier_bytes = secrets.token_bytes(32)
    verifier = base64.urlsafe_b64encode(verifier_bytes).decode("utf-8").rstrip("=")
    
    challenge_bytes = hashlib.sha256(verifier.encode("utf-8")).digest()
    challenge = base64.urlsafe_b64encode(challenge_bytes).decode("utf-8").rstrip("=")
    
    return verifier, challenge


# =============================================================================
# Token Storage
# =============================================================================

@dataclass
class OAuthTokens:
    """OAuth tokens and metadata."""
    access_token: str
    refresh_token: str
    expires_at: float  # Unix timestamp
    project_id: str
    email: Optional[str] = None
    
    def is_expired(self) -> bool:
        """Check if access token is expired (with buffer)."""
        return time.time() >= (self.expires_at - EXPIRY_BUFFER_SECONDS)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
            "project_id": self.project_id,
            "email": self.email,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "OAuthTokens":
        return cls(
            access_token=data["access_token"],
            refresh_token=data["refresh_token"],
            expires_at=data["expires_at"],
            project_id=data["project_id"],
            email=data.get("email"),
        )


def save_tokens(tokens: OAuthTokens) -> None:
    """Save tokens to file."""
    TOKEN_FILE.write_text(json.dumps(tokens.to_dict(), indent=2))
    os.chmod(TOKEN_FILE, 0o600)


def load_tokens() -> Optional[OAuthTokens]:
    """Load tokens from file."""
    if not TOKEN_FILE.exists():
        return None
    try:
        data = json.loads(TOKEN_FILE.read_text())
        return OAuthTokens.from_dict(data)
    except (json.JSONDecodeError, KeyError, TypeError):
        return None


def clear_tokens() -> None:
    """Remove stored tokens."""
    if TOKEN_FILE.exists():
        TOKEN_FILE.unlink()


def is_oauth_available() -> bool:
    """Check if OAuth tokens are available and valid."""
    tokens = load_tokens()
    if tokens is None:
        return False
    return bool(tokens.refresh_token)


# =============================================================================
# OAuth Flow
# =============================================================================

class OAuthCallbackHandler(http.server.BaseHTTPRequestHandler):
    """HTTP handler to capture OAuth callback."""
    
    callback_url: Optional[str] = None
    
    def do_GET(self):
        OAuthCallbackHandler.callback_url = self.path
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        self.wfile.write(b"""
        <html>
        <head><title>Antigravity Authentication Complete</title></head>
        <body style="font-family: system-ui; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0;">
            <div style="text-align: center;">
                <h1>Authentication Complete!</h1>
                <p>You can close this window and return to the terminal.</p>
            </div>
        </body>
        </html>
        """)
    
    def log_message(self, format, *args):
        pass  # Suppress logging


def authorize(project_id: str = "") -> Dict[str, str]:
    """Generate OAuth authorization URL with PKCE."""
    verifier, challenge = generate_pkce()
    
    # Encode state (verifier + project_id)
    state_data = json.dumps({"verifier": verifier, "projectId": project_id})
    state = base64.urlsafe_b64encode(state_data.encode()).decode().rstrip("=")
    
    params = {
        "client_id": ANTIGRAVITY_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": ANTIGRAVITY_REDIRECT_URI,
        "scope": " ".join(ANTIGRAVITY_SCOPES),
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        "access_type": "offline",
        "prompt": "consent",
    }
    
    url = f"https://accounts.google.com/o/oauth2/v2/auth?{urllib.parse.urlencode(params)}"
    
    return {
        "url": url,
        "verifier": verifier,
        "project_id": project_id,
    }


def exchange_code(code: str, state: str) -> OAuthTokens:
    """Exchange authorization code for tokens."""
    # Decode state
    state_padded = state + "=" * (4 - len(state) % 4)
    state_data = json.loads(base64.urlsafe_b64decode(state_padded))
    verifier = state_data["verifier"]
    project_id = state_data.get("projectId", "")
    
    # Exchange code
    response = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": ANTIGRAVITY_CLIENT_ID,
            "client_secret": ANTIGRAVITY_CLIENT_SECRET,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": ANTIGRAVITY_REDIRECT_URI,
            "code_verifier": verifier,
        },
    )
    response.raise_for_status()
    token_data = response.json()
    
    # Get user info
    email = None
    try:
        user_resp = requests.get(
            "https://www.googleapis.com/oauth2/v1/userinfo?alt=json",
            headers={"Authorization": f"Bearer {token_data['access_token']}"},
        )
        if user_resp.ok:
            email = user_resp.json().get("email")
    except Exception:
        pass
    
    return OAuthTokens(
        access_token=token_data["access_token"],
        refresh_token=token_data["refresh_token"],
        expires_at=time.time() + token_data["expires_in"],
        project_id=project_id,
        email=email,
    )


def refresh_access_token(tokens: OAuthTokens) -> Optional[OAuthTokens]:
    """Refresh an expired access token."""
    try:
        response = requests.post(
            "https://oauth2.googleapis.com/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": tokens.refresh_token,
                "client_id": ANTIGRAVITY_CLIENT_ID,
                "client_secret": ANTIGRAVITY_CLIENT_SECRET,
            },
        )
        
        if not response.ok:
            error_data = response.json() if response.text else {}
            if error_data.get("error") == "invalid_grant":
                console.print("[yellow]Antigravity OAuth token revoked. Please run 'research antigravity-login' again.[/yellow]")
                clear_tokens()
            return None
        
        data = response.json()
        new_tokens = OAuthTokens(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token", tokens.refresh_token),
            expires_at=time.time() + data["expires_in"],
            project_id=tokens.project_id,
            email=tokens.email,
        )
        save_tokens(new_tokens)
        return new_tokens
        
    except Exception as e:
        console.print(f"[red]Failed to refresh Antigravity token: {e}[/red]")
        return None


def get_valid_tokens() -> Optional[OAuthTokens]:
    """Get valid OAuth tokens, refreshing if necessary."""
    tokens = load_tokens()
    if tokens is None:
        return None
    
    if tokens.is_expired():
        tokens = refresh_access_token(tokens)
    
    return tokens


# =============================================================================
# Interactive Login Flow
# =============================================================================

def interactive_login(project_id: str = "") -> bool:
    """Run interactive OAuth login flow."""
    console.print("\n[bold]=== Antigravity OAuth Setup ===[/bold]")
    
    if not project_id:
        console.print("\n[dim]A Google Cloud project ID is required for API access.[/dim]")
        project_id = console.input("[bold]Enter your Google Cloud Project ID:[/bold] ").strip()
        if not project_id:
            console.print("[red]Project ID is required.[/red]")
            return False
    
    auth = authorize(project_id)
    
    # Try to start local callback server
    server = None
    try:
        # Parse port from redirect URI
        parsed_uri = urllib.parse.urlparse(ANTIGRAVITY_REDIRECT_URI)
        port = parsed_uri.port or 51121
        
        server = http.server.HTTPServer(("localhost", port), OAuthCallbackHandler)
        server_thread = threading.Thread(target=server.handle_request, daemon=True)
        server_thread.start()
        
        console.print("\n1. Opening browser for sign-in...")
        console.print("2. After approval, return here.\n")
        
        webbrowser.open(auth["url"])
        
        server_thread.join(timeout=120)
        
        if OAuthCallbackHandler.callback_url:
            parsed = urllib.parse.urlparse(OAuthCallbackHandler.callback_url)
            params = urllib.parse.parse_qs(parsed.query)
            code = params.get("code", [None])[0]
            state = params.get("state", [None])[0]
            
            if code and state:
                try:
                    tokens = exchange_code(code, state)
                    save_tokens(tokens)
                    console.print(f"\n[green]✓ Successfully authenticated as {tokens.email or 'user'}[/green]")
                    console.print(f"[dim]Project: {tokens.project_id}[/dim]")
                    return True
                except Exception as e:
                    console.print(f"\n[red]Failed to exchange code: {e}[/red]")
                    return False
        
        console.print("[red]No callback received. Try manual flow.[/red]")
        
    except OSError:
        console.print(f"[yellow]Port {port} in use. Using manual flow.[/yellow]")
    finally:
        if server:
            server.server_close()
    
    # Manual fallback...
    # (Simplified for brevity, can implement if needed or just rely on auto)
    # The user can just paste the code if we provided instructions.
    # For now, let's just fail if port fails or implement basic manual link print
    console.print("\n[bold]Manual OAuth Link:[/bold]")
    console.print(f"{auth['url']}")
    
    callback_url = console.input("\n[bold]Paste the callback URL here:[/bold] ").strip()
    try:
        parsed = urllib.parse.urlparse(callback_url)
        params = urllib.parse.parse_qs(parsed.query)
        code = params.get("code", [None])[0]
        state = params.get("state", [None])[0]
        if code and state:
            tokens = exchange_code(code, state)
            save_tokens(tokens)
            console.print(f"\n[green]✓ Successfully authenticated as {tokens.email or 'user'}[/green]")
            return True
    except Exception:
        pass
        
    return False


def logout() -> None:
    """Clear stored tokens."""
    if TOKEN_FILE.exists():
        clear_tokens()
        console.print("[green]✓ Antigravity logged out.[/green]")
    else:
        console.print("[dim]No tokens found.[/dim]")


# =============================================================================
# API Client
# =============================================================================

def antigravity_generate_content(
    model: str,
    contents: List[Dict[str, Any]],
    *,
    system_instruction: Optional[Dict[str, Any]] = None,
    generation_config: Optional[Dict[str, Any]] = None,
    tools: Optional[List[Dict[str, Any]]] = None,
    tool_config: Optional[Dict[str, Any]] = None,
    stream: bool = False,
) -> Dict[str, Any]:
    """Call Cloud Code Assist API using Antigravity credentials."""
    tokens = get_valid_tokens()
    if tokens is None:
        raise RuntimeError("Antigravity OAuth not configured. Run 'research antigravity-login' first.")
    
    # Normalize model (remove antigravity/ prefix)
    # e.g. antigravity/claude-opus-4-5-thinking -> claude-opus-4-5-thinking
    if "/" in model:
        model = model.split("/", 1)[1]
    
    request_payload: Dict[str, Any] = {
        "contents": contents,
    }
    
    if system_instruction:
        request_payload["systemInstruction"] = system_instruction
    
    if generation_config:
        request_payload["generationConfig"] = generation_config
    
    if tools:
        request_payload["tools"] = tools
    
    if tool_config:
        request_payload["toolConfig"] = tool_config
        
    wrapped_payload = {
        "project": tokens.project_id,
        "model": model,
        "request": request_payload,
    }
    # Make request with retries
    action = "streamGenerateContent" if stream else "generateContent"
    url = f"{ANTIGRAVITY_CODE_ASSIST_ENDPOINT}/v1internal:{action}"
    
    headers = {
        "Authorization": f"Bearer {tokens.access_token}",
        "Content-Type": "application/json",
        **ANTIGRAVITY_HEADERS,
    }
    
    max_retries = 3
    for attempt in range(max_retries + 1):
        response = requests.post(url, json=wrapped_payload, headers=headers)
        
        if response.status_code == 429:
            if attempt < max_retries:
                wait_time = 30
                try:
                    import re
                    match = re.search(r"reset after (\d+)s", response.text)
                    if match:
                        wait_time = int(match.group(1)) + 1
                except Exception:
                    pass
                
                console.print(f"[yellow]Antigravity rate limit exceeded. Waiting {wait_time}s before retry ({attempt + 1}/{max_retries})...[/yellow]")
                time.sleep(wait_time)
                continue
        
        if not response.ok:
            raise RuntimeError(f"Antigravity API error ({response.status_code}): {response.text}")
        
        data = response.json()
        if "response" in data:
            return data["response"]
        return data
