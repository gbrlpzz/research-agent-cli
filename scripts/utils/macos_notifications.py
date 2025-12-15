"""
macOS Native Notifications for Research Agent.

Uses pync for native macOS notification center integration.
Falls back to osascript if pync is not available.
"""

import subprocess
from typing import Optional

# Try to import pync, fallback to osascript
try:
    import pync
    HAS_PYNC = True
except ImportError:
    HAS_PYNC = False


def notify(
    title: str,
    message: str,
    *,
    subtitle: Optional[str] = None,
    sound: bool = True,
    group: str = "research-agent",
    open_url: Optional[str] = None,
    activate: Optional[str] = None,
) -> None:
    """
    Send a native macOS notification.
    
    Args:
        title: Notification title
        message: Main message body
        subtitle: Optional subtitle
        sound: Whether to play notification sound
        group: Group ID for notification stacking
        open_url: URL to open when notification is clicked
        activate: Bundle ID of app to activate on click
    """
    if HAS_PYNC:
        kwargs = {
            "title": title,
            "message": message,
            "group": group,
        }
        if subtitle:
            kwargs["subtitle"] = subtitle
        if sound:
            kwargs["sound"] = "default"
        if open_url:
            kwargs["open"] = open_url
        if activate:
            kwargs["activate"] = activate
        
        pync.notify(**kwargs)
    else:
        # Fallback to osascript
        script = f'display notification "{message}" with title "{title}"'
        if subtitle:
            script = f'display notification "{message}" with title "{title}" subtitle "{subtitle}"'
        if sound:
            script += ' sound name "default"'
        
        subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            check=False,
        )


def notify_research_started(topic: str) -> None:
    """Notify that research has started."""
    notify(
        title="🔬 Research Started",
        message=topic,
        sound=True,
    )


def notify_phase_change(phase: str, topic: str) -> None:
    """Notify of a significant phase change."""
    phase_emojis = {
        "Planning": "📋",
        "ArgumentMap": "🗺️",
        "Drafting": "✍️",
        "Review": "🔎",
        "Revision": "✏️",
        "Complete": "✅",
    }
    emoji = phase_emojis.get(phase, "📌")
    
    notify(
        title=f"{emoji} {phase}",
        message=topic,
        sound=False,  # Don't spam sounds for every phase
    )


def notify_research_complete(topic: str, pdf_path: Optional[str] = None) -> None:
    """Notify that research is complete."""
    kwargs = {
        "title": "✅ Research Complete",
        "message": topic,
        "sound": True,
    }
    if pdf_path:
        kwargs["open_url"] = f"file://{pdf_path}"
    
    notify(**kwargs)


def notify_error(message: str, topic: Optional[str] = None) -> None:
    """Notify of an error."""
    notify(
        title="❌ Research Error",
        message=message,
        subtitle=topic,
        sound=True,
    )


def notify_queue_status(position: int, topic: str) -> None:
    """Notify about queue position."""
    notify(
        title="📋 Added to Queue",
        message=f"Position {position}: {topic}",
        sound=False,
    )
