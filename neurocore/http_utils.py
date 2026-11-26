"""
Chamadas HTTP às APIs dos provedores, com mensagens de erro sem dados
sensíveis (as URLs e cabeçalhos com chaves nunca aparecem no erro).
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import httpx

from neurocore.config import get_settings


class ProviderError(RuntimeError):
    """Erro de um provedor, já sem dados sensíveis (chaves, URLs com token)."""

    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


def default_timeout(seconds: Optional[float] = None) -> httpx.Timeout:
    return httpx.Timeout(float(seconds or get_settings().LLM_TIMEOUT), connect=15.0)


def api_error(provider: str, resp: httpx.Response) -> ProviderError:
    detail = ""
    try:
        data = resp.json()
        err = data.get("error", data) if isinstance(data, dict) else data
        if isinstance(err, dict):
            detail = str(err.get("message") or err.get("type") or "")
        else:
            detail = str(err)
    except Exception:
        detail = resp.text[:200]
    return ProviderError(f"{provider} API error ({resp.status_code}): {detail[:300]}", resp.status_code)


async def post_json(
    provider: str,
    url: str,
    payload: Dict[str, Any],
    headers: Dict[str, str],
    timeout: Optional[float] = None,
) -> Dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=default_timeout(timeout)) as client:
            resp = await client.post(url, json=payload, headers=headers)
    except httpx.TimeoutException:
        raise ProviderError(f"{provider}: tempo de resposta esgotado")
    except httpx.HTTPError as exc:
        # Não repassa str(exc): pode conter a URL da requisição.
        raise ProviderError(f"{provider}: falha de conexão ({type(exc).__name__})")
    if resp.status_code != 200:
        raise api_error(provider, resp)
    try:
        return resp.json()
    except ValueError:
        raise ProviderError(f"{provider}: resposta inválida da API")


async def post_multipart(
    provider: str,
    url: str,
    data: Dict[str, Any],
    files: Any,
    headers: Dict[str, str],
    timeout: Optional[float] = None,
) -> Dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=default_timeout(timeout)) as client:
            resp = await client.post(url, data=data, files=files, headers=headers)
    except httpx.TimeoutException:
        raise ProviderError(f"{provider}: tempo de resposta esgotado")
    except httpx.HTTPError as exc:
        raise ProviderError(f"{provider}: falha de conexão ({type(exc).__name__})")
    if resp.status_code != 200:
        raise api_error(provider, resp)
    try:
        return resp.json()
    except ValueError:
        raise ProviderError(f"{provider}: resposta inválida da API")
