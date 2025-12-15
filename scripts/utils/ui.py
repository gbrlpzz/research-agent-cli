"""
Terminal User Interface (TUI) Manager.

Handles the rich text interface, including:
- Live dashboard with header/footer
- Scrolling log window
- Status updates
"""

import time
import logging
from typing import Optional, List, Dict, Any
from datetime import datetime
from collections import deque

from rich.console import Console, Group
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.text import Text
from rich.table import Table
from rich.progress import Progress, BarColumn, TextColumn, TimeRemainingColumn
from rich.style import Style

# Global console instance
console = Console()

class UIManager:
    """Manages the terminal UI layout and updates."""

    def __init__(self, topic: str, model_name: str):
        self.topic = topic
        self.model_name = model_name
        self.start_time = time.time()
        
        # State
        self.current_phase = "Initializing"
        self.status_message = "Starting..."
        self.total_cost = 0.0
        self.total_tokens = 0
        self.log_buffer = deque(maxlen=10) # Keep last 10 logs visible
        self.metrics: Dict[str, Any] = {}
        
        # Layout setup
        self.layout = self._make_layout()
        self.live = Live(
            self.layout, 
            refresh_per_second=4, 
            screen=True,  # Full screen mode
            redirect_stdout=False, # We handle logs manually
            redirect_stderr=False
        )
        
        # Internal log handler to capture logging.info/debug?
        # For now, we expect explicit calls to ui.log()
        
    def _make_layout(self) -> Layout:
        """Create the main layout structure."""
        layout = Layout()
        
        # Split into Header, Body, Footer
        layout.split(
            Layout(name="header", size=3),
            Layout(name="body"),
            Layout(name="footer", size=12),
        )
        
        # Split body into Main (Status) and Side (Metrics)
        # For now just one main body
        # layout["body"].split_row(
        #     Layout(name="main", ratio=2),
        #     Layout(name="side", ratio=1),
        # )
        
        return layout

    def start(self):
        """Start the live display."""
        self.live.start()
        self.update()

    def stop(self):
        """Stop the live display."""
        self.live.stop()

    def update_metrics(self, cost: float, tokens: int, **kwargs):
        """Update global metrics."""
        self.total_cost = cost
        self.total_tokens = tokens
        self.metrics.update(kwargs)
        self.update()

    def set_phase(self, phase: str, model: Optional[str] = None):
        """Update current phase."""
        self.current_phase = phase
        if model:
            self.model_name = model
        self.update()

    def set_status(self, message: str):
        """Update generic status message."""
        self.status_message = message
        self.update()

    def log(self, message: str, level: str = "INFO"):
        """Add a log message to the scrolling footer."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        
        styles = {
            "INFO": "white",
            "WARNING": "yellow",
            "ERROR": "red bold",
            "DEBUG": "dim",
            "SUCCESS": "green"
        }
        style = styles.get(level, "white")
        
        self.log_buffer.append((timestamp, level, message, style))
        self.update()

    def send_notification(self, message: str, title: str = "Research Agent", urgent: bool = False, reveal_path: Optional[str] = None):
        """
        Send a MacOS notification.
        
        Args:
            message: The body text of the notification
            title: The title of the notification
            urgent: If True, uses 'display alert' (modal dialog) instead of banner
            reveal_path: Optional path to reveal in Finder if user clicks action button
        """
        import subprocess
        import sys
        if sys.platform != "darwin":
            return
            
        try:
            if urgent or reveal_path:
                # 'display alert' is modal (pops up in center)
                # If reveal_path is set, we add a button to open it
                buttons = 'buttons {"OK"}'
                if reveal_path:
                    buttons = 'buttons {"Show in Finder", "OK"} default button "OK"'
                
                script = f'display alert "{title}" message "{message}" as critical {buttons}'
                
                # If we have a path to reveal, we need to wait for the result to see if they clicked it
                if reveal_path:
                    result = subprocess.run(
                        ["osascript", "-e", script], 
                        capture_output=True, 
                        text=True, 
                        check=False
                    )
                    if "Show in Finder" in result.stdout:
                         subprocess.run(["open", "-R", reveal_path], check=False)
                else:
                    # Run detached if no interaction needed
                    subprocess.Popen(["osascript", "-e", script])
            else:
                # 'display notification' is a transient banner
                script = f'display notification "{message}" with title "{title}" sound name "default"'
                subprocess.run(["osascript", "-e", script], check=False)
        except Exception:
            pass  # Fail silently if notifications don't work

    def _generate_header(self) -> Panel:
        """Generate the header panel."""
        elapsed = time.time() - self.start_time
        hours = int(elapsed // 3600)
        minutes = int((elapsed % 3600) // 60)
        seconds = int(elapsed % 60)
        timer = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        
        grid = Table.grid(expand=True)
        grid.add_column(justify="left", ratio=1)
        grid.add_column(justify="center", ratio=1)
        grid.add_column(justify="right", ratio=1)
        
        grid.add_row(
            f"[bold]{self.topic}[/bold]",
            f"[blue]Phase: {self.current_phase}[/blue]",
            f"[green]${self.total_cost:.4f}[/green] ({self.total_tokens:,} toks) | [yellow]{timer}[/yellow]"
        )
        
        return Panel(
            grid,
            style="bold white",
            border_style="blue"
        )

    def _generate_body(self) -> Panel:
        """Generate the main body panel."""
        # Simple content for now
        content = Group(
            Text(f"\nModel: {self.model_name}", style="dim"),
            Text(f"\n{self.status_message}", style="bold cyan", justify="center"),
        )
        
        if self.metrics:
            metrics_table = Table(title="Metrics", box=None, show_header=False)
            metrics_table.add_column("Key", style="dim")
            metrics_table.add_column("Value", style="bold")
            for k, v in self.metrics.items():
                metrics_table.add_row(k, str(v))
            content = Group(content, Text("\n"), metrics_table)

        return Panel(
            content,
            title="Active Task",
            border_style="cyan"
        )

    def _generate_footer(self) -> Panel:
        """Generate the scrolling log footer."""
        log_text = Text()
        for ts, lvl, msg, style in self.log_buffer:
            log_text.append(f"{ts} [{lvl}] {msg}\n", style=style)
            
        return Panel(
            log_text,
            title="Recent Activity",
            border_style="dim"
        )

    def update(self):
        """Render the layout."""
        self.layout["header"].update(self._generate_header())
        self.layout["body"].update(self._generate_body())
        self.layout["footer"].update(self._generate_footer())
        self.live.refresh()

# Global UI instance placeholder
_ui: Optional[UIManager] = None

def get_ui() -> Optional[UIManager]:
    return _ui

def set_ui(ui: UIManager):
    global _ui
    _ui = ui
