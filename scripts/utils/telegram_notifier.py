"""
Telegram Notifier Utility
=========================

Handles sending status updates and documents to a Telegram chat.
Designed to provide a "live dashboard" feel by editing a single status message.
"""

import os
import time
import requests
import json
from typing import Optional, Dict, Any
from pathlib import Path
from rich.console import Console

console = Console()

class TelegramNotifier:
    def __init__(self, token: Optional[str] = None, chat_id: Optional[str] = None):
        self.token = token or os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID") or os.getenv("AUTHORIZED_USER_ID")
        self.base_url = f"https://api.telegram.org/bot{self.token}"
        self.status_message_id = None
        self.start_time = time.time()
        self.topic = "Research Task"
        self.last_update_time = 0
        self.enabled = bool(self.token and self.chat_id)
        
        if not self.enabled:
            console.print("[dim]Telegram notifications disabled (missing token or chat_id)[/dim]")

    def start_research(self, topic: str, model_name: str):
        """Send initial start message."""
        if not self.enabled: return
        
        self.topic = topic
        self.start_time = time.time()
        
        text = (
            f"🔬 *Starting Research*\n\n"
            f"_{topic}_\n\n"
            f"Please wait..."
        )
        
        try:
            resp = requests.post(f"{self.base_url}/sendMessage", json={
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": "Markdown"
            })
            if resp.status_code == 200:
                self.status_message_id = resp.json()["result"]["message_id"]
        except Exception as e:
            console.print(f"[dim yellow]Failed to send Telegram start: {e}[/dim yellow]")

    def update_status(self, phase: str, stats: Dict[str, Any] = None):
        """Update the existing status message with new phase and stats."""
        if not self.enabled or not self.status_message_id: return
        
        # Rate limit updates (Telegram allows ~1 per sec, let's play safe with 2s)
        if time.time() - self.last_update_time < 2:
            return
        
        stats = stats or {}
        elapsed_min = int((time.time() - self.start_time) / 60)
        
        # Emoji mapping
        emojis = {
            'Starting': '🚀',
            'Planning': '📋',
            'ArgumentMap': '🗺️',
            'Drafting': '✍️',
            'Review': '🔎',
            'Revision': '📝',
            'Complete': '✅'
        }
        emoji = emojis.get(phase, '➤')
        
        # Build message
        text = (
            f"🔬 *Research in Progress*\n\n"
            f"_{self.topic}_\n\n"
            f"{emoji} *Phase:* {phase}\n"
            f"⏱️ *Time:* {elapsed_min}m"
        )
        
        # Add dynamic stats
        if 'questions' in stats:
            text += f"\n❓ *Questions:* {stats['questions']}"
        if 'citations' in stats:
            text += f"\n📚 *Citations:* {stats['citations']}"
        if 'round' in stats:
            text += f"\n🔄 *Round:* {stats['round']}"
            
        try:
            requests.post(f"{self.base_url}/editMessageText", json={
                "chat_id": self.chat_id,
                "message_id": self.status_message_id,
                "text": text,
                "parse_mode": "Markdown"
            })
            self.last_update_time = time.time()
        except Exception as e:
            # Ignore "message is not modified" errors
            pass

    def send_document(self, file_path: Path, caption: str = "Research Report"):
        """Send the final PDF."""
        if not self.enabled: return
        
        if not file_path.exists():
            self.send_message(f"❌ Error: Final PDF not found at {file_path}")
            return

        try:
            with open(file_path, 'rb') as f:
                requests.post(
                    f"{self.base_url}/sendDocument",
                    data={"chat_id": self.chat_id, "caption": caption},
                    files={"document": f}
                )
            
            # Also update status to complete
            self.update_status("Complete")
            
        except Exception as e:
            self.send_message(f"⚠️ PDF generated but failed to send: {e}")

    def send_message(self, text: str):
        """Send a simple text message."""
        if not self.enabled: return
        try:
            requests.post(f"{self.base_url}/sendMessage", json={
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": "Markdown"
            })
        except Exception:
            pass
