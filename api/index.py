"""
Vercel entrypoint. Vercel's Python runtime serves a module-level WSGI `app`
from api/*.py; this re-exports Django's, unchanged.
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

from config.wsgi import application as app  # noqa: E402,F401
