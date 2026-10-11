import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Config:
    data_dir: Path = field(default_factory=lambda: Path(os.getenv("NEOBOTS_DATA_DIR", ".data")))
    model_url: str = field(default_factory=lambda: os.getenv("MODEL_BASE_URL", "").rstrip("/"))
    model: str = field(default_factory=lambda: os.getenv("MODEL_NAME", ""))
    api_key: str = field(default_factory=lambda: os.getenv("MODEL_API_KEY", ""))
    password: str = field(default_factory=lambda: os.getenv("NEOBOTS_PASSWORD", ""))
    runtime_url: str = field(default_factory=lambda: os.getenv("NEOBOTS_RUNTIME_URL", "http://127.0.0.1:8788"))
    runtime_token: str = field(default_factory=lambda: os.getenv("NEOBOTS_RUNTIME_TOKEN", ""))
    max_steps: int = 30
    concurrency: int = 3

    @property
    def model_ready(self):
        return bool(self.model_url and self.model and self.api_key)

