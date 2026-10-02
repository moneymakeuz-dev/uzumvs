import os
import sys
from pathlib import Path

__version__ = "0.1.0"

# Smart App Control blocks psycopg-binary's DLL on this machine; fall back to a local libpq.
_PG_CLIENT = Path(__file__).resolve().parent.parent / "var" / "pgclient"
if sys.platform == "win32" and (_PG_CLIENT / "libpq.dll").is_file():
    os.environ["PATH"] = f"{_PG_CLIENT}{os.pathsep}{os.environ.get('PATH', '')}"
    os.environ.setdefault("PSYCOPG_IMPL", "python")
