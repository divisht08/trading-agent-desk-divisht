from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass
class Config:
    raw: dict[str, Any]

    @classmethod
    def load(cls, path: str | None = None) -> "Config":
        p = Path(path or os.environ.get("AGENT_CONFIG", "config.yaml"))
        with open(p) as f:
            return cls(yaml.safe_load(f))

    def __getitem__(self, k: str) -> Any:
        return self.raw[k]

    @property
    def capital(self) -> float:
        return float(self.raw["capital"])

    @property
    def mode(self) -> str:
        return self.raw.get("mode", "paper")

    def live_enabled(self) -> bool:
        """Live trading requires BOTH mode: live in config AND the env unlock phrase."""
        return (
            self.mode == "live"
            and os.environ.get("LIVE_UNLOCK") == "I_ACCEPT_REAL_MONEY_RISK"
        )
