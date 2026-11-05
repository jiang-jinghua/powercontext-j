"""Runtime settings resolved from flags and environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .errors import ConfigurationError


@dataclass(frozen=True)
class Settings:
    """All paths and model settings for one SDD workspace."""

    home: Path
    project_dir: Path
    db_path: Path
    runs_dir: Path
    state_db: Path
    scheduler_db: Path
    minimax_api_key: str | None
    minimax_base_url: str
    minimax_model: str
    minimax_api_style: str
    minimax_embedding_model: str | None

    @property
    def db_dir(self) -> Path:
        return self.db_path.parent

    def ensure_dirs(self) -> None:
        self.db_dir.mkdir(parents=True, exist_ok=True)
        self.runs_dir.mkdir(parents=True, exist_ok=True)

    def require_minimax(self) -> str:
        if not self.minimax_api_key:
            raise ConfigurationError(
                "MINIMAX_API_KEY is not set; export it or pass --api-key to call MiniMax. "
                "Use --provider manual --patch <file> to apply a reviewed patch offline."
            )
        return self.minimax_api_key


def load_settings(
    *,
    home: str | Path | None = None,
    project: str | Path | None = None,
    db_path: str | Path | None = None,
) -> Settings:
    """Resolve paths relative to the submission root (``minimax/``)."""
    root = Path(home).resolve() if home else Path.cwd().resolve()
    project_dir = Path(project).resolve() if project else root / "demo" / "order-service"
    configured_db = Path(db_path).resolve() if db_path else Path(
        os.environ.get("SDD_DB_PATH", root / ".sdd" / "seekdb")
    ).resolve()
    state_dir = root / ".sdd"
    style = os.environ.get("MINIMAX_API_STYLE", "native").lower()
    default_base = "https://api.minimax.cn/anthropic" if style == "anthropic" else "https://api.minimaxi.com"
    return Settings(
        home=root,
        project_dir=project_dir,
        db_path=configured_db,
        runs_dir=root / "runs",
        state_db=state_dir / "state.db",
        scheduler_db=state_dir / "state-scheduler.db",
        minimax_api_key=os.environ.get("MINIMAX_API_KEY") or None,
        minimax_base_url=os.environ.get("MINIMAX_BASE_URL", default_base).rstrip("/"),
        minimax_model=os.environ.get("MINIMAX_MODEL", "MiniMax-M3.1-Flash"),
        minimax_api_style=style,
        minimax_embedding_model=os.environ.get("MINIMAX_EMBEDDING_MODEL") or None,
    )
