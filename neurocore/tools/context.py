"""
Contexto de uma requisição, repassado pelo executor às ferramentas que
precisam dele (dono da conversa, provedor escolhido e a chave do usuário).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ToolContext:
    owner: Optional[str] = None
    provider: str = "local"
    api_key: Optional[str] = None

    def __repr__(self) -> str:  # nunca mostra a chave em logs
        return f"ToolContext(owner={self.owner!r}, provider={self.provider!r}, api_key={'***' if self.api_key else None})"
