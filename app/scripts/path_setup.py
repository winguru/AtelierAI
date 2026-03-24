"""Common path setup for all scripts.

This module handles imports from src/ and legacy/ directories.
"""

import sys
import os

# Get the scripts directory
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Get parent directory (project root)
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)

# Add project root, backend, and src to path.
# backend/ is needed so models.py and other backend modules can do
# flat imports (e.g. `from database import Base`) at import time.
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'backend'))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'src'))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'legacy'))
