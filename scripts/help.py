#!/usr/bin/env python3
"""
Interactive help and tutorial for the research CLI tool.
"""
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.markdown import Markdown

console = Console()

def show_help():
    """Display comprehensive help with all commands."""
    
    # Header
    console.print()
    console.print(Panel.fit(
        "[bold cyan]Research CLI - Interactive Tutorial[/bold cyan]\n"
        "[dim]Terminal-first research pipeline with multi-source search[/dim]",
        border_style="cyan"
    ))
    console.print()
    
    # Discovery Commands
    discovery_table = Table(title="🔍 Discovery Commands", show_header=True, header_style="bold magenta")
    discovery_table.add_column("Command", style="cyan", width=30)
    discovery_table.add_column("Description", style="white")
    discovery_table.add_column("Cost", style="yellow", justify="right")
    
    discovery_table.add_row(
        "research <query>",
        "Unified search (Semantic Scholar + paper-scraper)\nAutomatically deduplicates and tags sources",
        "Free"
    )
    discovery_table.add_row(
        "research exa <query>",
        "Neural/semantic search via Exa.ai\nBetter for conceptual queries",
        "1 credit"
    )
    discovery_table.add_row(
        "research edison <query>",
        "AI literature synthesis with tables\nGenerates comprehensive reports",
        "1 credit"
    )
    console.print(discovery_table)
    console.print()
    
    # Management Commands
    management_table = Table(title="📚 Library Management", show_header=True, header_style="bold green")
    management_table.add_column("Command", style="cyan", width=30)
    management_table.add_column("Description", style="white")
    
    management_table.add_row(
        "research add [id]",
        "Quick add from DOI/arXiv or clipboard\nFetches PDFs automatically"
    )
    management_table.add_row(
        "research cite [query]",
        "Search library and copy citation keys\nUse 'o' to open, Tab to multi-select"
    )
    management_table.add_row(
        "research open [query]",
        "Search library and open in browser"
    )
    console.print(management_table)
    console.print()
    
    # Q&A Commands
    qa_table = Table(title="🤖 Question Answering (PaperQA + Gemini)", show_header=True, header_style="bold cyan")
    qa_table.add_column("Command", style="cyan", width=30)
    qa_table.add_column("Description", style="white")
    
    qa_table.add_row(
        "research qa <question>",
        "Ask questions about your library\nUses Gemini 2.0 Flash (free tier)"
    )
    console.print(qa_table)
    console.print()
    
    # Edison Commands
    edison_table = Table(title="🤖 Edison Scientific", show_header=True, header_style="bold blue")
    edison_table.add_column("Command", style="cyan", width=30)
    edison_table.add_column("Description", style="white")
    
    edison_table.add_row("research edison list", "Browse past reports")
    edison_table.add_row("research edison show <id>", "View specific report")
    edison_table.add_row("research edison cache <query>", "Check if query is cached")
    edison_table.add_row("research edison credits", "Show credit balance")
    console.print(edison_table)
    console.print()
    
    # Examples
    console.print(Panel(
        "[bold yellow]💡 Quick Start Examples[/bold yellow]\n\n"
        "[cyan]1. Find papers:[/cyan]\n"
        "   research \"attention mechanism\"\n\n"
        "[cyan]2. Add a specific paper:[/cyan]\n"
        "   research add 10.1234/example\n"
        "   research add 1706.03762  [dim]# arXiv ID[/dim]\n\n"
        "[cyan]3. Get citation key for Typst:[/cyan]\n"
        "   research cite vaswani\n"
        "   [dim]# Press Enter to copy @citation_key[/dim]\n\n"
        "[cyan]4. AI synthesis:[/cyan]\n"
        "   research edison \"What are transformers?\"\n"
        "\n"
        "[cyan]5. Ask your library:[/cyan]\n"
        "   research qa \"How does attention work?\"\n"
        "   [dim]# Requires GEMINI_API_KEY in .env[/dim]\n",
        title="Examples",
        border_style="yellow"
    ))
    console.print()
    
    # Tips
    console.print(Panel(
        "[bold green]✨ Tips & Tricks[/bold green]\n\n"
        "• [cyan]Multi-select:[/cyan] Press Tab in fzf to select multiple papers\n"
        "• [cyan]Open in browser:[/cyan] Press 'o' while browsing to open paper\n"
        "• [cyan]Source tags:[/cyan] [S2] = Semantic Scholar, [PS] = paper-scraper\n"
        "• [cyan]Progress:[/cyan] Spinners show real-time search progress\n"
        "• [cyan]Auto PDF:[/cyan] Fetches from ArXiv, Unpaywall, paper-scraper\n",
        title="Tips",
        border_style="green"
    ))
    console.print()
    
    # Workflow
    console.print(Panel(
        "[bold magenta]📋 Typical Workflow[/bold magenta]\n\n"
        "[cyan]1.[/cyan] Search for papers: [white]research \"your topic\"[/white]\n"
        "[cyan]2.[/cyan] Select papers with Tab, press Enter\n"
        "[cyan]3.[/cyan] PDFs auto-download to library/\n"
        "[cyan]4.[/cyan] Find citation key: [white]research cite author[/white]\n"
        "[cyan]5.[/cyan] Use in Typst: [white]@citation_key[/white]\n"
        "[cyan]6.[/cyan] Compile: [white]typst compile main.typ[/white]\n",
        title="Workflow",
        border_style="magenta"
    ))
    console.print()
    
    # Footer
    console.print("[dim]For more details: https://github.com/gbrlpzz/research-agent-cli[/dim]")
    console.print()

if __name__ == "__main__":
    show_help()
