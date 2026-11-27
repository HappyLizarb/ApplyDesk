"""Local application workspace and guarded browser coordinator."""

from pathlib import Path

# The wheel keeps the launcher, web assets and native window inventory together, just as the desktop bundle does.
PROGRAM_ROOT = Path(__file__).resolve().parent.parent / "applydesk_desktop"
if not (PROGRAM_ROOT / "run.py").is_file():
    PROGRAM_ROOT = PROGRAM_ROOT.parent
