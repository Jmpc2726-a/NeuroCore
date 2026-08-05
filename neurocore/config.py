"""
NeuroCore Agent Configuration Settings.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional
from dotenv import load_dotenv

load_dotenv()


class Config:
    def __init__(self):
        self.WORKSPACE_ROOT: str = os.path.abspath(
            os.environ.get("NEUROCORE_WORKSPACE", os.environ.get("WORKSPACE_ROOT", "/home/Andrade/neurocore-agent"))
        )
        self.SESSIONS_DIR: str = os.path.join(self.WORKSPACE_ROOT, ".sessions")
        self.TASKS_DIR: str = os.path.join(self.WORKSPACE_ROOT, ".tasks")
        self.TASKS_FILE: str = os.path.join(self.WORKSPACE_ROOT, ".tasks.json")
        self.DEFAULT_PROVIDER: str = os.environ.get("NEUROCORE_DEFAULT_PROVIDER", os.environ.get("DEFAULT_PROVIDER", "local"))
        self.CORS_ORIGINS: List[str] = ["*"]
        self.MAX_CONTEXT_TOKENS: int = 32000
        self.MAX_STEPS: int = 15
        self.SHELL_TIMEOUT: int = 30
        self.MAX_FILE_SIZE_BYTES: int = 10 * 1024 * 1024
        self.HOST: str = "127.0.0.1"
        self.PORT: int = 8000

        self.default_model_gemini: str = "gemini-2.5-flash"
        self.default_model_openai: str = "gpt-4o"
        self.default_model_anthropic: str = "claude-3-5-sonnet"
        self.default_model_openrouter: str = "qwen/qwen-2.5-coder-32b-instruct:free"
        self.default_model_local: str = "simulated-local-v1"

    @property
    def gemini_api_key(self) -> Optional[str]:
        return os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")

    @property
    def openai_api_key(self) -> Optional[str]:
        return os.environ.get("OPENAI_API_KEY")

    @property
    def anthropic_api_key(self) -> Optional[str]:
        return os.environ.get("ANTHROPIC_API_KEY")

    @property
    def openrouter_api_key(self) -> Optional[str]:
        return os.environ.get("OPENROUTER_API_KEY")

    @property
    def workspace_root(self) -> Path:
        return Path(self.WORKSPACE_ROOT)

    @property
    def sessions_dir(self) -> Path:
        return Path(self.SESSIONS_DIR)

    @property
    def tasks_file(self) -> Path:
        return Path(self.TASKS_FILE)

    @property
    def default_provider(self) -> str:
        return self.DEFAULT_PROVIDER

    @property
    def shell_timeout(self) -> int:
        return self.SHELL_TIMEOUT

    @property
    def max_file_size_bytes(self) -> int:
        return self.MAX_FILE_SIZE_BYTES

    @property
    def cors_origins(self) -> List[str]:
        return self.CORS_ORIGINS

    def is_provider_configured(self, provider: str) -> bool:
        p = provider.lower()
        if p in ("local", "offline", "simulated"):
            return True
        if p == "gemini":
            return bool(self.gemini_api_key and self.gemini_api_key.strip())
        if p == "openai":
            return bool(self.openai_api_key and self.openai_api_key.strip())
        if p in ("anthropic", "claude"):
            return bool(self.anthropic_api_key and self.anthropic_api_key.strip())
        if p == "openrouter":
            return bool(self.openrouter_api_key and self.openrouter_api_key.strip())
        return False

    def get_configured_providers(self) -> List[str]:
        providers = ["local"]
        if self.is_provider_configured("gemini"):
            providers.append("gemini")
        if self.is_provider_configured("openai"):
            providers.append("openai")
        if self.is_provider_configured("anthropic"):
            providers.append("anthropic")
        if self.is_provider_configured("openrouter"):
            providers.append("openrouter")
        return providers

    def ensure_directories(self):
        os.makedirs(self.SESSIONS_DIR, exist_ok=True)
        os.makedirs(self.TASKS_DIR, exist_ok=True)


Settings = Config
config = Config()


def get_settings() -> Config:
    return config
