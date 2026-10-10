"""Runtime configuration loaded from config.toml (all keys optional)."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel


class PublishConfig(BaseModel):
    backend: Literal["local", "s3"] = "local"
    target: str = "out"
    bucket: str | None = None
    prefix: str = ""
    cloudfront_distribution_id: str | None = None


class Config(BaseModel):
    model: str = "claude-opus-5-5"
    effort: Literal["low", "medium", "high"] = "medium"
    window_hours: int = 48
    monthly_input_token_ceiling: int = 5_000_000
    data_dir: Path = Path("data")
    out_dir: Path = Path("out")
    site_url: str = ""
    timezone: str = "America/Los_Angeles"
    publish: PublishConfig = PublishConfig()


def load_config(path: Path = Path("config.toml")) -> Config:
    if not path.exists():
        return Config()
    with path.open("rb") as fh:
        return Config.model_validate(tomllib.load(fh))
