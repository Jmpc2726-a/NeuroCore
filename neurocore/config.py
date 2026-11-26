"""
NeuroCore Agent Configuration Settings.

Todas as opções podem ser definidas por variáveis de ambiente (ou por um
arquivo `.env`, que fica fora do repositório). Veja o README para a lista.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv

load_dotenv()


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on", "sim")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_list(name: str, default: List[str]) -> List[str]:
    raw = os.environ.get(name)
    if raw is None:
        return list(default)
    return [item.strip() for item in raw.split(",") if item.strip()]


class Config:
    def __init__(self):
        self.WORKSPACE_ROOT: str = os.path.abspath(
            os.environ.get("NEUROCORE_WORKSPACE", os.environ.get("WORKSPACE_ROOT", os.getcwd()))
        )
        self.SESSIONS_DIR: str = os.path.join(self.WORKSPACE_ROOT, ".sessions")
        self.TASKS_DIR: str = os.path.join(self.WORKSPACE_ROOT, ".tasks")
        self.TASKS_FILE: str = os.path.join(self.WORKSPACE_ROOT, ".tasks.json")
        self.DEFAULT_PROVIDER: str = os.environ.get(
            "NEUROCORE_DEFAULT_PROVIDER", os.environ.get("DEFAULT_PROVIDER", "local")
        )

        # --- Segurança -----------------------------------------------------
        # Em hospedagens públicas (Render define RENDER=true) o modo público é
        # ligado automaticamente: as ferramentas que alteram o servidor
        # (shell, escrita e edição de arquivos) ficam desligadas.
        self.PUBLIC_MODE: bool = _env_bool("NEUROCORE_PUBLIC_MODE", _env_bool("RENDER", False))
        self.ENABLE_DANGEROUS_TOOLS: bool = _env_bool(
            "NEUROCORE_ENABLE_DANGEROUS_TOOLS", not self.PUBLIC_MODE
        )
        # Se definido, toda a API (exceto /health) exige este token no
        # cabeçalho "X-NeuroCore-Token" ou "Authorization: Bearer <token>".
        self.ACCESS_TOKEN: Optional[str] = (os.environ.get("NEUROCORE_ACCESS_TOKEN") or "").strip() or None
        # Lista separada por vírgulas. Vazia = apenas a mesma origem.
        self.CORS_ORIGINS: List[str] = _env_list(
            "NEUROCORE_CORS_ORIGINS", ["http://127.0.0.1:8000", "http://localhost:8000"]
        )
        self.RATE_LIMIT_PER_MINUTE: int = _env_int("NEUROCORE_RATE_LIMIT_PER_MINUTE", 30)
        self.MAX_PROMPT_CHARS: int = _env_int("NEUROCORE_MAX_PROMPT_CHARS", 20000)
        self.MAX_REQUEST_BYTES: int = _env_int("NEUROCORE_MAX_REQUEST_BYTES", 1024 * 1024)
        self.MAX_SESSIONS: int = _env_int("NEUROCORE_MAX_SESSIONS", 500)
        self.MAX_TASKS: int = _env_int("NEUROCORE_MAX_TASKS", 200)
        # Tempo máximo de uma conexão SSE de atividade; o navegador reconecta sozinho.
        self.SSE_MAX_SECONDS: int = _env_int("NEUROCORE_SSE_MAX_SECONDS", 300)

        # --- Anexos (imagens e documentos) ---------------------------------
        self.UPLOADS_DIR: str = os.path.join(self.WORKSPACE_ROOT, ".uploads")
        self.MAX_UPLOAD_BYTES: int = _env_int("NEUROCORE_MAX_UPLOAD_BYTES", 15 * 1024 * 1024)
        self.MAX_UPLOAD_FILES: int = _env_int("NEUROCORE_MAX_UPLOAD_FILES", 3000)
        self.UPLOAD_RATE_LIMIT_PER_MINUTE: int = _env_int("NEUROCORE_UPLOAD_RATE_LIMIT_PER_MINUTE", 60)
        self.MAX_ATTACHMENTS_PER_MESSAGE: int = _env_int("NEUROCORE_MAX_ATTACHMENTS_PER_MESSAGE", 10)
        # Quantas imagens (as mais recentes) são reenviadas ao modelo a cada chamada.
        self.MAX_IMAGES_PER_REQUEST: int = _env_int("NEUROCORE_MAX_IMAGES_PER_REQUEST", 8)

        # --- Geração de imagens --------------------------------------------
        self.ENABLE_IMAGE_GENERATION: bool = _env_bool("NEUROCORE_ENABLE_IMAGE_GENERATION", True)
        self.OPENAI_IMAGE_MODEL: str = os.environ.get("NEUROCORE_OPENAI_IMAGE_MODEL", "gpt-image-1")
        self.GEMINI_IMAGE_MODEL: str = os.environ.get("NEUROCORE_GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image")
        self.OPENROUTER_IMAGE_MODEL: str = os.environ.get(
            "NEUROCORE_OPENROUTER_IMAGE_MODEL", "google/gemini-2.5-flash-image"
        )
        # Servidores dos quais a ferramenta do ENEM pode baixar as figuras das questões.
        self.ENEM_IMAGE_HOSTS: List[str] = _env_list("NEUROCORE_ENEM_IMAGE_HOSTS", ["enem.dev"])

        # Fuso horário usado no prompt quando o navegador não informa o seu.
        self.TIMEZONE: str = os.environ.get("NEUROCORE_TIMEZONE", "America/Sao_Paulo")

        self.MAX_CONTEXT_TOKENS: int = _env_int("NEUROCORE_MAX_CONTEXT_TOKENS", 96000)
        self.MAX_STEPS: int = _env_int("NEUROCORE_MAX_STEPS", 15)
        self.SHELL_TIMEOUT: int = _env_int("NEUROCORE_SHELL_TIMEOUT", 30)
        self.LLM_TIMEOUT: int = _env_int("NEUROCORE_LLM_TIMEOUT", 300)
        self.MAX_FILE_SIZE_BYTES: int = 10 * 1024 * 1024
        self.HOST: str = os.environ.get("HOST", "127.0.0.1")
        self.PORT: int = _env_int("PORT", 8000)

        self.default_model_gemini: str = os.environ.get("NEUROCORE_GEMINI_MODEL", "gemini-2.5-flash")
        self.default_model_openai: str = os.environ.get("NEUROCORE_OPENAI_MODEL", "gpt-4o")
        self.default_model_anthropic: str = os.environ.get("NEUROCORE_ANTHROPIC_MODEL", "claude-opus-5-5")
        self.default_model_openrouter: str = os.environ.get(
            "NEUROCORE_OPENROUTER_MODEL", "nvidia/nemotron-3-ultra-550b-a55b:free"
        )
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
        for name in ("gemini", "openai", "anthropic", "openrouter"):
            if self.is_provider_configured(name):
                providers.append(name)
        return providers

    def ensure_directories(self):
        os.makedirs(self.SESSIONS_DIR, exist_ok=True)
        os.makedirs(self.TASKS_DIR, exist_ok=True)
        os.makedirs(self.UPLOADS_DIR, exist_ok=True)


Settings = Config
config = Config()


def get_settings() -> Config:
    return config
