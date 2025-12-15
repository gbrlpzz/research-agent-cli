#!/usr/bin/env python3
"""
Research Agent Menu Bar App for macOS.

A lightweight menu bar application that provides:
- Quick access to start new research
- Progress tracking for running research
- Research queue management
- Native macOS notifications
- Telegram notifications (via existing integration)

Usage:
    python scripts/research_menubar.py
"""

import json
import os
import subprocess
import sys
import threading
import queue
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional, Deque, Dict, Any

import rumps

# Add scripts directory to path for imports
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))

from utils.macos_notifications import (
    notify_research_started,
    notify_phase_change,
    notify_research_complete,
    notify_error,
    notify_queue_status,
)

# Icons
ICON_IDLE = "🔬"
ICON_RUNNING = "⚗️"
ICON_QUEUED = "📋"


@dataclass
class ResearchTask:
    """A queued research task."""
    topic: str
    created_at: datetime = field(default_factory=datetime.now)
    
    def __str__(self) -> str:
        return self.topic


@dataclass
class ResearchState:
    """Current state of a running research task."""
    topic: str
    phase: str = "Starting"
    status: str = "Initializing..."
    progress: int = 0
    citations: int = 0
    round: int = 0
    started_at: datetime = field(default_factory=datetime.now)
    report_dir: Optional[Path] = None


class ResearchMenuBarApp(rumps.App):
    """macOS menu bar app for Research Agent."""
    
    def __init__(self):
        super().__init__(ICON_IDLE, quit_button=None)
        
        # State
        self.current_task: Optional[ResearchState] = None
        self.task_queue: Deque[ResearchTask] = deque()
        self.process: Optional[subprocess.Popen] = None
        self.external_pid: Optional[int] = None  # PID of external process (from terminal)
        self.output_thread: Optional[threading.Thread] = None
        self.should_stop = threading.Event()
        
        # Settings (could be persisted)
        self.max_revisions = 3
        self.model = "gemini-2.5-flash"
        
        # Check for existing research process
        self._detect_existing_research()
        
        # Build menu
        self._build_menu()
        
        # Timer for periodic updates
        self.timer = rumps.Timer(self._check_process, 2)
        self.timer.start()
    
    def _detect_existing_research(self):
        """Detect if there's an existing research process running from terminal."""
        try:
            result = subprocess.run(
                ["pgrep", "-f", "agent.py.*-i"],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0 and result.stdout.strip():
                pids = result.stdout.strip().split("\n")
                if pids:
                    self.external_pid = int(pids[0])
                    # Try to get the topic from the command line
                    cmd_result = subprocess.run(
                        ["ps", "-p", str(self.external_pid), "-o", "args="],
                        capture_output=True,
                        text=True,
                    )
                    if cmd_result.returncode == 0:
                        args = cmd_result.stdout.strip()
                        # Extract topic after -i flag
                        if " -i " in args:
                            topic = args.split(" -i ", 1)[1].split(" --")[0].strip()
                            self.current_task = ResearchState(topic=topic)
                            self.current_task.phase = "Running"
                            
                            # Find active report directory and read checkpoint
                            self._find_active_report_dir()
        except Exception:
            pass
    
    def _find_active_report_dir(self):
        """Find the active report directory for external process."""
        reports_dir = REPO_ROOT / "reports"
        if not reports_dir.exists():
            return
        
        # Get most recently modified report directory
        report_dirs = sorted(
            [d for d in reports_dir.iterdir() if d.is_dir() and not d.name.startswith(".")],
            key=lambda d: d.stat().st_mtime,
            reverse=True
        )
        
        if report_dirs:
            self.active_report_dir = report_dirs[0]
            if self.current_task:
                self.current_task.report_dir = self.active_report_dir
                self._read_checkpoint()
    
    def _read_checkpoint(self):
        """Read checkpoint file to get current phase and progress."""
        if not hasattr(self, 'active_report_dir') or not self.active_report_dir:
            return
        
        checkpoint_file = self.active_report_dir / "artifacts" / "checkpoint.json"
        if not checkpoint_file.exists():
            return
        
        try:
            data = json.loads(checkpoint_file.read_text())
            phase = data.get("phase", "")
            phase_data = data.get("data", {})
            
            # Map internal phase names to display names
            phase_map = {
                "planning": "Planning",
                "argument_map": "Building Arguments",
                "initial_draft": "Drafting",
                "peer_review_r1": "Review (Round 1)",
                "peer_review_r2": "Review (Round 2)", 
                "peer_review_r3": "Review (Round 3)",
                "revision_r1": "Revising (Round 1)",
                "revision_r2": "Revising (Round 2)",
                "revision_r3": "Revising (Round 3)",
                "completed": "Complete",
            }
            
            if self.current_task:
                self.current_task.phase = phase_map.get(phase, phase.replace("_", " ").title())
                if "round" in phase_data:
                    self.current_task.round = phase_data["round"]
                    
                # Count citations from bib file
                bib_file = self.active_report_dir / "artifacts" / "refs.bib"
                if bib_file.exists():
                    self.current_task.citations = bib_file.read_text().count("@")
        except Exception:
            pass
    
    def _build_menu(self):
        """Build the menu structure."""
        # Build queue menu first
        queue_menu = rumps.MenuItem("Queue")
        queue_menu.add(rumps.MenuItem("No pending tasks", callback=None))
        
        self.menu = [
            rumps.MenuItem("New Research...", callback=self.new_research, key="n"),
            None,  # Separator
            rumps.MenuItem("Status", callback=None),  # Status header (non-clickable)
            rumps.MenuItem("  Idle", callback=None),  # Status detail
            rumps.MenuItem("Stop Research", callback=None),  # Disabled initially
            None,  # Separator
            queue_menu,
            None,  # Separator
            self._build_reports_menu(),
            None,  # Separator
            rumps.MenuItem("Quit Research Agent", callback=self.quit_app),
        ]
        
        # Set initial status based on detected external process
        if self.current_task:
            task = self.current_task
            topic_short = task.topic[:25] + "…" if len(task.topic) > 25 else task.topic
            if task.citations:
                self.menu["  Idle"].title = f"  {task.phase} · {task.citations} citations"
            else:
                self.menu["  Idle"].title = f"  {task.phase}"
            self.title = f"⚗️ {topic_short}"
    
    def _format_elapsed(self, started_at: datetime) -> str:
        """Format elapsed time in human-readable form."""
        elapsed = datetime.now() - started_at
        minutes = int(elapsed.total_seconds() // 60)
        if minutes < 1:
            return "just started"
        elif minutes < 60:
            return f"{minutes}m"
        else:
            hours = minutes // 60
            mins = minutes % 60
            return f"{hours}h {mins}m"
    
    def _update_status_menu(self):
        """Update the status menu item based on current state."""
        status_item = self.menu["  Idle"]
        stop_item = self.menu["Stop Research"]
        
        if self.current_task:
            task = self.current_task
            elapsed = self._format_elapsed(task.started_at)
            
            # Build status line for dropdown
            if task.citations:
                status_item.title = f"  {task.topic[:40]} · {task.citations} citations · {elapsed}"
            else:
                status_item.title = f"  {task.topic[:40]} · {elapsed}"
            
            # Show phase and round in menu bar title (visible without clicking)
            phase_short = task.phase
            if task.round and "Round" not in task.phase:
                # Show round X/MAX format
                max_rounds = self.max_revisions
                self.title = f"⚗️ {phase_short} ({task.round}/{max_rounds})"
            else:
                self.title = f"⚗️ {phase_short}"
            
            # Enable stop button (only for our processes, not external)
            if self.process:
                stop_item.set_callback(self.stop_research)
            else:
                stop_item.set_callback(None)
                
        elif self.task_queue:
            count = len(self.task_queue)
            status_item.title = f"  {count} task{'s' if count > 1 else ''} queued"
            self.title = f"📋 {count}"
            stop_item.set_callback(None)
        else:
            status_item.title = "  Idle"
            self.title = "🔬"
            stop_item.set_callback(None)
        
        # Update queue submenu
        self._update_queue_menu()
    
    def _update_queue_menu(self):
        """Update the queue submenu."""
        queue_menu = self.menu["Queue"]
        queue_menu.clear()
        
        if self.task_queue:
            for i, task in enumerate(self.task_queue, 1):
                topic_short = task.topic[:45] + "…" if len(task.topic) > 45 else task.topic
                item = rumps.MenuItem(f"{i}. {topic_short}", callback=None)
                queue_menu.add(item)
            queue_menu.add(None)
            queue_menu.add(rumps.MenuItem("Clear Queue", callback=self.clear_queue))
        else:
            queue_menu.add(rumps.MenuItem("No pending tasks", callback=None))
    
    def stop_research(self, _):
        """Stop the currently running research."""
        if self.process:
            response = rumps.alert(
                title="Stop Research?",
                message="This will terminate the current research. You can resume later.",
                ok="Stop",
                cancel="Cancel",
            )
            if response == 1:  # OK clicked
                self.should_stop.set()
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                
                notify_error("Research stopped by user", self.current_task.topic if self.current_task else None)
                self.process = None
                self.current_task = None
                self._update_status_menu()
                self._process_next_in_queue()
    
    def _build_reports_menu(self) -> rumps.MenuItem:
        """Build the reports submenu."""
        reports_menu = rumps.MenuItem("Reports")
        
        reports_dir = REPO_ROOT / "reports"
        if not reports_dir.exists():
            reports_menu.add(rumps.MenuItem("(no reports)", callback=None))
            return reports_menu
        
        # Get all report directories
        report_dirs = sorted(
            [d for d in reports_dir.iterdir() if d.is_dir() and not d.name.startswith(".")],
            key=lambda d: d.stat().st_mtime,
            reverse=True
        )
        
        if not report_dirs:
            reports_menu.add(rumps.MenuItem("(no reports)", callback=None))
            return reports_menu
        
        # Group by date
        from collections import defaultdict
        by_date = defaultdict(list)
        
        for report_dir in report_dirs:
            # Extract date and topic from directory name (format: YYYYMMDD_HHMMSS_topic_slug)
            parts = report_dir.name.split("_", 2)
            if len(parts) >= 3:
                date_str = parts[0]  # YYYYMMDD
                topic_slug = parts[2].replace("_", " ").title()
            else:
                date_str = "unknown"
                topic_slug = report_dir.name
            
            # Format date nicely
            try:
                date_obj = datetime.strptime(date_str, "%Y%m%d")
                date_key = date_obj.strftime("%b %d, %Y")
            except ValueError:
                date_key = date_str
            
            by_date[date_key].append((topic_slug, report_dir))
        
        # Add to menu grouped by date
        for date_key, reports in by_date.items():
            date_submenu = rumps.MenuItem(f"📅 {date_key}")
            for topic_slug, report_dir in reports:
                item = rumps.MenuItem(
                    f"  {topic_slug[:50]}",
                    callback=lambda sender, path=report_dir: self._open_report(path)
                )
                date_submenu.add(item)
            reports_menu.add(date_submenu)
        
        # Add separator and folder shortcut at bottom
        reports_menu.add(None)
        reports_menu.add(rumps.MenuItem(
            "📂 Show All in Finder",
            callback=lambda _: subprocess.run(["open", str(reports_dir)], check=False)
        ))
        
        return reports_menu
    
    def _update_reports_menu(self):
        """Refresh the reports menu after a new report is generated."""
        # Remove old reports menu and insert new one
        if "Reports" in self.menu:
            del self.menu["Reports"]
        # Insert after "Open Reports Folder"
        self.menu.insert_after("Open Reports Folder", self._build_reports_menu())
    
    def _open_report(self, report_dir: Path):
        """Open a report directory or PDF."""
        # Try to find PDF
        artifacts_dir = report_dir / "artifacts"
        pdfs = list(artifacts_dir.glob("*.pdf")) if artifacts_dir.exists() else []
        
        if pdfs:
            # Open the most recent PDF
            pdf = max(pdfs, key=lambda p: p.stat().st_mtime)
            subprocess.run(["open", str(pdf)], check=False)
        else:
            # Open the directory
            subprocess.run(["open", str(report_dir)], check=False)
    
    @rumps.clicked("New Research...")
    def new_research(self, _):
        """Show input dialog for new research topic."""
        # Create window with helpful message
        window = rumps.Window(
            message="Enter a research question or topic.\n\nThe agent will plan, draft, and review a comprehensive report.",
            title="🔬 New Research",
            ok="Start Research",
            cancel="Cancel",
            dimensions=(500, 80),
        )
        window.default_text = ""
        
        response = window.run()
        
        if response.clicked and response.text.strip():
            topic = response.text.strip()
            
            # Confirm if there's already a running task
            if self.current_task:
                confirm = rumps.alert(
                    title="Research Already Running",
                    message=f"Add to queue?\n\nCurrent: {self.current_task.topic[:50]}...\nNew: {topic[:50]}...",
                    ok="Add to Queue",
                    cancel="Cancel",
                )
                if confirm != 1:
                    return
            
            self._enqueue_research(topic)
    
    def _enqueue_research(self, topic: str):
        """Add a research topic to the queue."""
        task = ResearchTask(topic=topic)
        
        if self.current_task is None and not self.process:
            # Start immediately
            self._start_research(task)
        else:
            # Add to queue
            self.task_queue.append(task)
            position = len(self.task_queue)
            notify_queue_status(position, topic)
            self._update_status_menu()
    
    def _start_research(self, task: ResearchTask):
        """Start a research task."""
        self.current_task = ResearchState(topic=task.topic)
        self.should_stop.clear()
        
        # Notify
        notify_research_started(task.topic)
        self._update_status_menu()
        
        # Build command (with Telegram flag for notifications)
        agent_script = SCRIPT_DIR / "agent.py"
        cmd = [
            sys.executable,
            str(agent_script),
            "--json-output",
            "--telegram",  # Enable Telegram notifications
            "-i", task.topic,
            "--max-revisions", str(self.max_revisions),
        ]
        
        # Start subprocess
        try:
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                cwd=str(REPO_ROOT),
                env={**os.environ, "PYTHONUNBUFFERED": "1"},
            )
            
            # Start output reader thread
            self.output_thread = threading.Thread(
                target=self._read_output,
                daemon=True,
            )
            self.output_thread.start()
            
        except Exception as e:
            notify_error(str(e), task.topic)
            self.current_task = None
            self._process_next_in_queue()
    
    def _read_output(self):
        """Read and parse NDJSON output from the agent process."""
        if not self.process or not self.process.stdout:
            return
        
        last_phase = None
        
        for line in self.process.stdout:
            if self.should_stop.is_set():
                break
            
            line = line.strip()
            if not line:
                continue
            
            # Try to parse as JSON
            try:
                data = json.loads(line)
                self._handle_progress_update(data, last_phase)
                if "phase" in data:
                    last_phase = data["phase"]
            except json.JSONDecodeError:
                # Not JSON, might be regular output - ignore
                pass
    
    def _handle_progress_update(self, data: Dict[str, Any], last_phase: Optional[str]):
        """Handle a progress update from the agent."""
        if not self.current_task:
            return
        
        phase = data.get("phase", self.current_task.phase)
        status = data.get("status", self.current_task.status)
        
        # Update state
        self.current_task.phase = phase
        self.current_task.status = status
        
        if "citations" in data:
            self.current_task.citations = data["citations"]
        if "round" in data:
            self.current_task.round = data["round"]
        if "report_dir" in data:
            self.current_task.report_dir = Path(data["report_dir"])
        
        # Notify on phase change
        if phase != last_phase and last_phase is not None:
            notify_phase_change(phase, self.current_task.topic)
        
        # Update menu
        rumps.rumps._MAIN_APP and setattr(rumps.rumps._MAIN_APP, "_needs_update", True)
    
    def _check_process(self, _):
        """Periodic check of process status (called by timer)."""
        # Check if external process is still running
        if self.external_pid:
            try:
                os.kill(self.external_pid, 0)  # Check if process exists
                # Re-read checkpoint to update progress
                self._read_checkpoint()
            except OSError:
                # External process finished
                topic = self.current_task.topic if self.current_task else "Research"
                self.external_pid = None
                self.current_task = None
                notify_research_complete(topic)
                self._update_reports_menu()
        
        self._update_status_menu()
        
        if self.process:
            poll = self.process.poll()
            if poll is not None:
                # Process finished
                self._handle_process_complete(poll)
    
    def _handle_process_complete(self, return_code: int):
        """Handle completion of a research process."""
        topic = self.current_task.topic if self.current_task else "Research"
        report_dir = self.current_task.report_dir if self.current_task else None
        
        if return_code == 0:
            # Success
            pdf_path = None
            if report_dir:
                artifacts_dir = report_dir / "artifacts"
                pdfs = list(artifacts_dir.glob("*.pdf")) if artifacts_dir.exists() else []
                if pdfs:
                    pdf_path = str(max(pdfs, key=lambda p: p.stat().st_mtime))
            
            notify_research_complete(topic, pdf_path)
        else:
            # Error
            notify_error(f"Process exited with code {return_code}", topic)
        
        # Clean up
        self.process = None
        self.current_task = None
        self._update_reports_menu()
        
        # Process next in queue
        self._process_next_in_queue()
    
    def _process_next_in_queue(self):
        """Start the next task in the queue if available."""
        if self.task_queue and not self.current_task:
            next_task = self.task_queue.popleft()
            self._start_research(next_task)
        else:
            self._update_status_menu()
    
    def clear_queue(self, _):
        """Clear all queued tasks."""
        self.task_queue.clear()
        self._update_status_menu()
        rumps.notification(
            title="Queue Cleared",
            subtitle=None,
            message="All queued research tasks have been removed.",
        )
    
    @rumps.clicked("Open Reports Folder")
    def open_reports(self, _):
        """Open the reports folder in Finder."""
        reports_dir = REPO_ROOT / "reports"
        reports_dir.mkdir(exist_ok=True)
        subprocess.run(["open", str(reports_dir)], check=False)
    
    def quit_app(self, _):
        """Quit the application."""
        # Stop any running process
        if self.process:
            self.should_stop.set()
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
        
        rumps.quit_application()


def main():
    """Run the menu bar app."""
    # Ensure we're on macOS
    if sys.platform != "darwin":
        print("This app only runs on macOS.")
        sys.exit(1)
    
    app = ResearchMenuBarApp()
    app.run()


if __name__ == "__main__":
    main()
