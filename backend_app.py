#!/usr/bin/env python3
"""Thin launcher — delegates to apps/backend_app.py."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "apps"))
from backend_app import main
raise SystemExit(main())
