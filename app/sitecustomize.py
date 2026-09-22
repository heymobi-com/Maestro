"""App-level startup shim for compatibility with pyannote 3.3 on torchaudio 2.10+.

This mirrors the project-root sitecustomize so the patch is loaded no matter
whether the interpreter starts in the repo root or inside the app folder.
"""

from __future__ import annotations


try:
    import sitecustomize  # noqa: F401
except Exception:
    pass
