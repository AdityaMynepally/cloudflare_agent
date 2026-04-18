"""
Root-level launcher: adds backend/ to sys.path and imports the real app.
Run with: uvicorn api:app --reload --port 8000
"""

import sys
import os

# Add backend/ to Python path so all imports resolve
backend_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backend")
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

# Load .env from backend/ if dotenv is available
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(backend_dir, ".env"))
except ImportError:
    pass

# Change working directory to backend/ so screenshots/ and results/ are created there
os.chdir(backend_dir)

# Import the actual FastAPI app
from api import app  # noqa: E402, F401
