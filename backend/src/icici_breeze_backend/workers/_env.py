"""`.env` loading shared by the OS workers, which start without the API process's bootstrap."""
from __future__ import annotations

import os


def load_env() -> None:
    try:
        from dotenv import load_dotenv

        root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
        env_path = os.path.join(root, ".env")
        if os.path.isfile(env_path):
            load_dotenv(env_path, override=True)
        else:
            load_dotenv(override=True)
    except ImportError:
        pass
