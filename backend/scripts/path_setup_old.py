"""Common path setup for all scripts.

This module handles the imports from src/ and legacy/ directories.
"""

import sys
import os

# Get the parent directory (project root)
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Add project root and src to path
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'src'))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'legacy'))
