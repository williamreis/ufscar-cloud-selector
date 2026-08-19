"""
Os módulos do backend se importam de forma plana (`from schemas import ...`)
porque o uvicorn roda com working_dir em backend/app. Os testes reproduzem esse
sys.path em vez de reescrever os imports da aplicação.
"""

import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1] / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))
