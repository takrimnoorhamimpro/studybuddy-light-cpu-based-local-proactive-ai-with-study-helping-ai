"""Shared project paths for tests kept under ``app/tests``."""
from pathlib import Path
import sys

TESTS_DIR = Path(__file__).resolve().parent
APP_DIR = TESTS_DIR.parent
PROJECT_ROOT = APP_DIR.parent

if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))
