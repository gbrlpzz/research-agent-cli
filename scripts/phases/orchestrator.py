"""
Model Orchestration Module

Implements dynamic model selection based on:
- Task phase and complexity
- Budget preferences (cost-saving vs quality)
- Error recovery (escalate to expensive model on failures)

Based on insights from ToolOrchestra (arXiv:2511.21689).
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Optional, List
import os
import time
from rich.console import Console

console = Console()


class BudgetMode(Enum):
    """Budget preference modes."""
    LOW = "low"        # Prefer cheap models, minimal escalation
    BALANCED = "balanced"  # Balance cost and quality
    HIGH = "high"      # Prefer expensive models, best quality


class TaskPhase(Enum):
    """Research agent task phases."""
    PLANNING = "planning"
    ARGUMENT_MAP = "argument_map"
    DISCOVERY = "discovery"
    DRAFTING = "drafting"
    REVIEW = "review"
    REVISION = "revision"
    FINALIZATION = "finalization"


# Model tiers
CHEAP_MODEL = "gemini/gemini-2.5-flash"
EXPENSIVE_MODEL = "gemini/gemini-3-pro-preview"

# Phase-to-model mapping by budget mode
# Key insight: use cheap model for most tasks, expensive only where needed
PHASE_MODEL_MAP: Dict[BudgetMode, Dict[TaskPhase, str]] = {
    BudgetMode.LOW: {
        TaskPhase.PLANNING: CHEAP_MODEL,
        TaskPhase.ARGUMENT_MAP: CHEAP_MODEL,
        TaskPhase.DISCOVERY: CHEAP_MODEL,
        TaskPhase.DRAFTING: CHEAP_MODEL,
        TaskPhase.REVIEW: CHEAP_MODEL,
        TaskPhase.REVISION: CHEAP_MODEL,
        TaskPhase.FINALIZATION: CHEAP_MODEL,
    },
    BudgetMode.BALANCED: {
        TaskPhase.PLANNING: CHEAP_MODEL,
        TaskPhase.ARGUMENT_MAP: CHEAP_MODEL,
        TaskPhase.DISCOVERY: CHEAP_MODEL,
        TaskPhase.DRAFTING: EXPENSIVE_MODEL,  # Quality matters here
        TaskPhase.REVIEW: CHEAP_MODEL,
        TaskPhase.REVISION: EXPENSIVE_MODEL,  # Quality matters here
        TaskPhase.FINALIZATION: CHEAP_MODEL,
    },
    BudgetMode.HIGH: {
        TaskPhase.PLANNING: EXPENSIVE_MODEL,
        TaskPhase.ARGUMENT_MAP: EXPENSIVE_MODEL,
        TaskPhase.DISCOVERY: CHEAP_MODEL,  # Still use cheap for simple lookups
        TaskPhase.DRAFTING: EXPENSIVE_MODEL,
        TaskPhase.REVIEW: EXPENSIVE_MODEL,
        TaskPhase.REVISION: EXPENSIVE_MODEL,
        TaskPhase.FINALIZATION: EXPENSIVE_MODEL,
    },
}


@dataclass
class PhaseMetrics:
    """Metrics for a single phase."""
    phase: TaskPhase
    model_used: str
    start_time: float = 0.0
    end_time: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost: float = 0.0
    error_count: int = 0
    escalated: bool = False


@dataclass
class Orchestrator:
    """
    Dynamic model orchestrator.
    
    Selects models based on phase, budget, and error recovery needs.
    Tracks costs and provides metrics.
    """
    budget_mode: BudgetMode = BudgetMode.LOW
    _phase_metrics: Dict[str, PhaseMetrics] = field(default_factory=dict)
    _current_phase: Optional[TaskPhase] = None
    _error_threshold: int = 2  # Escalate after this many errors
    cost_free: bool = False
    
    @classmethod
    def from_cli(cls, budget: str = "low", cost_free: bool = False) -> "Orchestrator":
        """Create orchestrator from CLI budget arg."""
        try:
            mode = BudgetMode(budget.lower())
        except ValueError:
            console.print(f"[yellow]Unknown budget '{budget}', defaulting to 'low'[/yellow]")
            mode = BudgetMode.LOW
        return cls(budget_mode=mode, cost_free=cost_free)
    
    def get_model_for_phase(self, phase: TaskPhase) -> str:
        """Get the appropriate model for a phase based on budget."""
        self._current_phase = phase
        
        # Check if we should escalate due to errors
        phase_key = phase.value
        if phase_key in self._phase_metrics:
            metrics = self._phase_metrics[phase_key]
            if metrics.error_count >= self._error_threshold and not metrics.escalated:
                console.print(f"[yellow]⬆ Escalating to expensive model after {metrics.error_count} errors[/yellow]")
                metrics.escalated = True
                return EXPENSIVE_MODEL
        
        # Get model from phase map
        return PHASE_MODEL_MAP[self.budget_mode].get(phase, CHEAP_MODEL)
    
    def start_phase(self, phase: TaskPhase) -> str:
        """Start a phase and return the model to use."""
        phase_key = phase.value
        
        if phase_key not in self._phase_metrics:
            self._phase_metrics[phase_key] = PhaseMetrics(
                phase=phase,
                model_used="",
                start_time=time.time()
            )
        else:
            self._phase_metrics[phase_key].start_time = time.time()
        
        model = self.get_model_for_phase(phase)
        self._phase_metrics[phase_key].model_used = model
        
        return model
    
    def end_phase(self, phase: TaskPhase, tokens_in: int = 0, tokens_out: int = 0) -> None:
        """End a phase and record metrics."""
        phase_key = phase.value
        if phase_key in self._phase_metrics:
            metrics = self._phase_metrics[phase_key]
            metrics.end_time = time.time()
            metrics.input_tokens += tokens_in
            metrics.output_tokens += tokens_out
            metrics.estimated_cost += self._estimate_cost(
                metrics.model_used, tokens_in, tokens_out
            )
    
    def record_error(self, phase: TaskPhase) -> None:
        """Record an error for a phase (may trigger escalation)."""
        phase_key = phase.value
        if phase_key not in self._phase_metrics:
            self._phase_metrics[phase_key] = PhaseMetrics(
                phase=phase,
                model_used=CHEAP_MODEL
            )
        self._phase_metrics[phase_key].error_count += 1
    
    def record_tokens(self, phase: TaskPhase, tokens_in: int, tokens_out: int, model: Optional[str] = None) -> None:
        """Record token usage for current phase."""
        phase_key = phase.value
        if phase_key in self._phase_metrics:
            self._phase_metrics[phase_key].input_tokens += tokens_in
            self._phase_metrics[phase_key].output_tokens += tokens_out
            
            # Use specific model if provided, otherwise fallback to phase model
            cost_model = model if model else self._phase_metrics[phase_key].model_used
            
            self._phase_metrics[phase_key].estimated_cost += self._estimate_cost(
                cost_model, tokens_in, tokens_out
            )
    
    def _estimate_cost(self, model: str, tokens_in: int, tokens_out: int) -> float:
        """Estimate cost based on model and tokens."""
        # Check if model is covered by free quota (Gemini/Antigravity)
        # We check specific prefixes to ensure we don't zero out OpenAI/Anthropic costs
        is_free_quota_model = self.cost_free and (
            model.startswith("gemini/") or 
            model.startswith("antigravity/") or
            "gemini" in model.lower()
        )

        if is_free_quota_model:
            return 0.0

        # Approximate pricing (USD per 1M tokens)
        pricing = {
            CHEAP_MODEL: {"input": 0.075, "output": 0.30},      # Flash pricing
            EXPENSIVE_MODEL: {"input": 1.25, "output": 5.00},   # Pro pricing (estimated)
            "text-embedding-3-large": {"input": 0.13, "output": 0.0},
            "text-embedding-3-small": {"input": 0.02, "output": 0.0},
            "gemini/text-embedding-004": {"input": 0.0, "output": 0.0}, # Often free/included in quota
        }
        
        rates = pricing.get(model)
        if not rates:
            # Smart fallback
            if "text-embedding-004" in model:
                 rates = {"input": 0.0, "output": 0.0}
            elif "embedding" in model.lower():
                 rates = {"input": 0.10, "output": 0.0}
            elif "claude-3-opus" in model:
                 rates = {"input": 15.0, "output": 75.0} # Anthropic Opus
            elif "claude-3-5-sonnet" in model:
                 rates = {"input": 3.0, "output": 15.0}  # Anthropic Sonnet
            elif "gpt-4o" in model:
                 rates = {"input": 2.5, "output": 10.0}
            else:
                 rates = pricing[CHEAP_MODEL] # Default fallback
                 
        cost = (tokens_in * rates["input"] + tokens_out * rates["output"]) / 1_000_000
        return cost
    
    def get_summary(self) -> Dict:
        """Get cost/usage summary across all phases."""
        total_tokens_in = 0
        total_tokens_out = 0
        total_cost = 0.0
        total_time = 0.0
        
        phases_summary = []
        for key, metrics in self._phase_metrics.items():
            duration = metrics.end_time - metrics.start_time if metrics.end_time else 0
            total_tokens_in += metrics.input_tokens
            total_tokens_out += metrics.output_tokens
            total_cost += metrics.estimated_cost
            total_time += duration
            
            phases_summary.append({
                "phase": key,
                "model": metrics.model_used.split("/")[-1],
                "tokens": metrics.input_tokens + metrics.output_tokens,
                "cost": f"${metrics.estimated_cost:.4f}",
                "time": f"{duration:.1f}s",
                "errors": metrics.error_count,
                "escalated": metrics.escalated,
            })
        
        return {
            "budget_mode": self.budget_mode.value,
            "total_tokens": total_tokens_in + total_tokens_out,
            "total_cost": f"${total_cost:.4f}",
            "total_time": f"{total_time:.1f}s",
            "phases": phases_summary,
        }
    
    def print_summary(self) -> None:
        """Print a formatted cost summary."""
        summary = self.get_summary()
        
        console.print(f"\n[bold cyan]📊 Orchestration Summary[/bold cyan]")
        console.print(f"Budget mode: {summary['budget_mode']}")
        console.print(f"Total tokens: {summary['total_tokens']:,}")
        console.print(f"Estimated cost: {summary['total_cost']}")
        console.print(f"Total time: {summary['total_time']}")
        
        if summary['phases']:
            console.print("\n[dim]Phase breakdown:[/dim]")
            for p in summary['phases']:
                escalated = " ⬆" if p['escalated'] else ""
                console.print(f"  {p['phase']}: {p['model']} | {p['tokens']:,} tokens | {p['cost']}{escalated}")


# Global orchestrator instance (set by agent.py)
_orchestrator: Optional[Orchestrator] = None


def get_orchestrator() -> Orchestrator:
    """Get the global orchestrator instance."""
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = Orchestrator()
    return _orchestrator


def set_orchestrator(orch: Orchestrator) -> None:
    """Set the global orchestrator instance."""
    global _orchestrator
    _orchestrator = orch
