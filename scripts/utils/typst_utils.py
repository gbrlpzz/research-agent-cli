"""
Typst compilation and error-fixing utilities.

Provides functions for compiling Typst documents and auto-fixing common errors.
"""
import re
import subprocess
from pathlib import Path
from typing import Set

from rich.console import Console

console = Console()


def filter_bibtex_to_cited(master_bib_path: Path, cited_keys: Set[str]) -> str:
    """Filter master.bib to only include entries that were cited."""
    if not master_bib_path.exists():
        return "% No references\n"
    
    content = master_bib_path.read_text()
    
    # Parse bibtex entries
    pattern = r'(@\w+\{([^,]+),.*?\n\})'
    matches = re.findall(pattern, content, re.DOTALL)
    
    filtered_entries = []
    for full_entry, key in matches:
        key = key.strip()
        if key in cited_keys or not cited_keys:
            filtered_entries.append(full_entry)
    
    if not filtered_entries:
        # Fallback: return all entries if no matches
        return content
    
    return "\n\n".join(filtered_entries) + "\n"


def extract_citations_from_typst(typst_content: str) -> Set[str]:
    """Extract all @citation_key references from Typst content."""
    # Match @key patterns (not in code blocks)
    pattern = r'@([a-zA-Z][a-zA-Z0-9_-]*)'
    matches = re.findall(pattern, typst_content)
    return set(matches)


def fix_typst_error(typst_path: Path, error_msg: str) -> bool:
    """Attempt to fix common Typst errors."""
    content = typst_path.read_text()
    original_content = content
    
    # Fix 1: Double asterisks (Markdown bold) -> Single asterisk (Typst bold)
    if "**" in content:
        content = content.replace("**", "*")
        
    # Fix 2: Bullet point syntax collision: "* *text*" confuses Typst
    # Typst uses "-" for lists, not "*". Fix pattern "* *" at line start to "- *"
    content = re.sub(r'^(\s*)\* \*', r'\1- *', content, flags=re.MULTILINE)
    
    # Fix 3: Unclosed delimiters (often due to mismatched *)
    if "unclosed delimiter" in error_msg:
        # Find lines with odd number of * and try to close them
        lines = content.split('\n')
        fixed_lines = []
        for line in lines:
            asterisk_count = line.count('*')
            if asterisk_count % 2 != 0:
                # Odd number - append a closing *
                line = line.rstrip() + '*'
            fixed_lines.append(line)
        content = '\n'.join(fixed_lines)
            
    # Fix 4: Missing citation labels - remove ALL hallucinated @keys
    if 'label' in error_msg and 'does not exist' in error_msg:
        # Extract ALL missing keys from errors like: label `<keyname>` does not exist
        missing_keys = re.findall(r'label `<([^>]+)>` does not exist', error_msg)
        for missing_key in missing_keys:
            console.print(f"[yellow]Auto-removing hallucinated citation: @{missing_key}[/yellow]")
            # Remove the @key from the content (with optional surrounding space)
            content = re.sub(rf'\s*@{re.escape(missing_key)}', '', content)
    
    # Fix 5: Wrong bibliography filename
    if 'file not found' in error_msg and 'master.bib' in error_msg:
        if 'bibliography("master.bib")' in content:
            content = content.replace('bibliography("master.bib")', 'bibliography("refs.bib")')
    
    # Fix 6: Markdown headers ("# Heading") -> Typst headers ("= Heading")
    # Typst uses "=" for headings. Markdown uses "#".
    # We look for lines starting with "# " that are NOT inside a code block or string.
    # Simple heuristic: exact match at start of line
    content = re.sub(r'^# ', '= ', content, flags=re.MULTILINE)
    content = re.sub(r'^## ', '== ', content, flags=re.MULTILINE)
    content = re.sub(r'^### ', '=== ', content, flags=re.MULTILINE)

    # Fix 7: Stray hashes/pounds that are not hashtags or code
    # Typst treats # as a code starter. If used as "Item #1", it breaks.
    # We escape them to \# if they look like standalone text usage.
    # Look for "#" followed by a digit, where it's NOT a heading (already fixed) or color hex
    # Regex: (space or start)#(digit) -> \1\#\2
    content = re.sub(r'(^|\s)#(\d)', r'\1\\#\2', content)

    # Fix 8: Access encoded HTML entities (common in BibTeX/LLM output)
    # Convert &amp; -> & (Typst escapes & automatically in text, but &amp; literal looks bad)
    if "&amp;" in content:
        content = content.replace("&amp;", "&")
        
    # Convert other common entities just in case
    if "&lt;" in content: content = content.replace("&lt;", "<")
    if "&gt;" in content: content = content.replace("&gt;", ">")
    if "&quot;" in content: content = content.replace("&quot;", '"')

    if content != original_content:
        typst_path.write_text(content)
        return True
    return False


def compile_and_fix(typ_path: Path, max_attempts: int = 5) -> bool:
    """Compile Typst file and attempt to auto-fix errors."""
    pdf_path = typ_path.with_suffix(".pdf")
    
    for attempt in range(max_attempts):
        result = subprocess.run(
            ["typst", "compile", str(typ_path.name), str(pdf_path.name)],
            cwd=str(typ_path.parent),
            capture_output=True,
            text=True,
            timeout=30
        )
        
        if result.returncode == 0:
            console.print(f"[green]✓ Compiled {typ_path.name}[/green]")
            return True
            
        console.print(f"[yellow]⚠ Compile validation failed (attempt {attempt+1}): {result.stderr.strip()[:100]}[/yellow]")
        
        # Try to fix
        if attempt < max_attempts - 1:
            if fix_typst_error(typ_path, result.stderr):
                console.print("[cyan]  Applying auto-fix...[/cyan]")
                continue
    
    console.print(f"[red]❌ Failed to compile {typ_path.name} after {max_attempts} attempts[/red]")
    return False
