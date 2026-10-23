"""Run from the repository without activating a virtual environment."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from web_workbench.__main__ import main

if __name__ == "__main__":
    main()
