"""
Star Hash Integration
Generate celestial timestamp SVGs for document covers.
Uses star-hash CLI via subprocess (separate venv).
"""

import subprocess
from pathlib import Path
from datetime import datetime
from typing import Optional


# Path to star-hash project (sibling directory)
STAR_HASH_PROJECT = Path(__file__).parent.parent.parent.parent / "star-hash"
STAR_HASH_VENV_PYTHON = STAR_HASH_PROJECT / ".venv" / "bin" / "python"


def generate_star_hash(
    output_path: Path,
    lat: Optional[float] = None,
    lon: Optional[float] = None,
    time: Optional[datetime] = None,
    size: int = 456
) -> Optional[Path]:
    """
    Generate a star hash SVG for the given time and location.
    
    Uses the star-hash CLI via subprocess to avoid dependency conflicts.
    
    Args:
        output_path: Where to save the SVG
        lat: Latitude (auto-detect via IP if None)
        lon: Longitude (auto-detect via IP if None)
        time: UTC datetime (current time if None)
        size: Output size in pixels (default: 456 = 3.86cm @ 300 DPI)
    
    Returns:
        Path to generated SVG, or None if star-hash not available
    """
    if not STAR_HASH_VENV_PYTHON.exists():
        return None
    
    # Build command
    cmd = [
        str(STAR_HASH_VENV_PYTHON),
        "-m", "star_hash.cli",
        "--output", str(output_path),
        "--size", str(size)
    ]
    
    if lat is not None:
        cmd.extend(["--lat", str(lat)])
    if lon is not None:
        cmd.extend(["--lon", str(lon)])
    if time is not None:
        cmd.extend(["--time", time.strftime("%Y-%m-%dT%H:%M:%S")])
    
    try:
        result = subprocess.run(
            cmd,
            cwd=str(STAR_HASH_PROJECT),
            capture_output=True,
            text=True,
            timeout=30
        )
        
        if result.returncode == 0 and output_path.exists():
            return output_path
        return None
        
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
