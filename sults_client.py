"""
Cliente HTTP da Sults (API v1 + API v2).

- Autenticação: token estático no header `Authorization`, puro (SEM prefixo `Bearer`).
- Dois tokens independentes: `token_v1` (API v1) e `token_v2` (API v2). O token da v1 não funciona
  na v2 e vice-versa.
- Rate limit global: 2 requisições por segundo por IP do servidor, compartilhado entre todos os
  tenants e versões (espaçamento mínimo de 0,5 s entre chamadas).
- Retentativas: 429 e 5xx com backoff exponencial de 1 s, 2 s, 4 s, 8 s.

Credenciais são lidas de `storage.load_tokens("sults", tenant)` e, como fallback para o tenant
`default`, das variáveis `SULTS_TOKEN_V1` e `SULTS_TOKEN_V2`.
"""

import os
import time
import asyncio
import logging
from typing import Any, Dict, Optional, Tuple

import httpx

from storage import StorageManager

logger = logging.getLogger("mcp-proxy.sults")

SULTS_API_URL = os.getenv("SULTS_API_URL", "https://api.sults.com.br").rstrip("/")
RATE_LIMIT_INTERVAL = 0.5  # ~2 req/s global por IP
BACKOFF_DELAYS = (1, 2, 4, 8)

VALIDATION_ENDPOINTS = {
    "v1": ("GET", "/v1/empresas", {"limit": 1}),
    "v2": ("GET", "/api/v2/checklist/template/category/list", {"limit": 1}),
}

# Limitador global compartilhado entre todos os tenants (limite da Sults é por IP)
_rate_lock: Optional[asyncio.Lock] = None
_last_request_at: float = 0.0


class SultsError(Exception):
    pass


def _get_rate_lock() -> asyncio.Lock:
    global _rate_lock
    if _rate_lock is None:
        _rate_lock = asyncio.Lock()
    return _rate_lock


async def _acquire_rate_slot() -> None:
    """Garante espaçamento mínimo de 0,5 s entre requisições à Sults (2 req/s global)."""
    global _last_request_at
    async with _get_rate_lock():
        now = time.monotonic()
        wait = RATE_LIMIT_INTERVAL - (now - _last_request_at)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_request_at = time.monotonic()


async def _send_with_retry(
    method: str,
    url: str,
    headers: Dict[str, str],
    params: Optional[Dict[str, Any]] = None,
    json_body: Optional[Any] = None,
    files: Optional[Dict[str, Tuple[str, bytes]]] = None,
    timeout: float = 60.0,
) -> httpx.Response:
    """Executa a requisição respeitando o limitador global e backoff exponencial em 429/5xx."""
    last_resp: Optional[httpx.Response] = None
    total_attempts = len(BACKOFF_DELAYS) + 1

    for attempt in range(total_attempts):
        await _acquire_rate_slot()
        async with httpx.AsyncClient(timeout=timeout) as http:
            resp = await http.request(
                method,
                url,
                params=params or None,
                json=json_body if (files is None and json_body is not None) else None,
                files=files,
                headers=headers,
            )
        last_resp = resp
        if (resp.status_code == 429 or resp.status_code >= 500) and attempt < len(BACKOFF_DELAYS):
            delay = BACKOFF_DELAYS[attempt]
            logger.warning(
                f"[Sults] HTTP {resp.status_code} em {method} {url}; aguardando {delay}s antes de tentar novamente."
            )
            await asyncio.sleep(delay)
            continue
        return resp

    return last_resp  # type: ignore[return-value]


async def validate_token(version: str, token: str) -> Dict[str, Any]:
    """Valida um token v1 ou v2 fazendo um GET leve na API da Sults."""
    ver = version.lower().strip()
    if ver not in VALIDATION_ENDPOINTS:
        raise SultsError(f"Versão Sults inválida: '{version}'. Use 'v1' ou 'v2'.")
    token_clean = (token or "").strip()
    if not token_clean:
        raise SultsError(f"Token Sults {ver} vazio.")

    method, path, params = VALIDATION_ENDPOINTS[ver]
    url = f"{SULTS_API_URL}{path}"
    headers = {"Authorization": token_clean, "Accept": "application/json"}

    resp = await _send_with_retry(method, url, headers=headers, params=params, timeout=20.0)
    if resp.status_code == 401:
        raise SultsError(
            f"Token Sults {ver} rejeitado (HTTP 401): token inválido ou da versão errada "
            "(o token da v1 não funciona na v2 e vice-versa)."
        )
    if resp.status_code >= 400:
        raise SultsError(f"Falha ao validar token Sults {ver} (HTTP {resp.status_code}): {resp.text[:300]}")
    return {"version": ver, "status": resp.status_code, "valid": True}


class SultsClient:
    def __init__(self, storage: StorageManager):
        self.storage = storage

    def load_credentials(self, tenant: str) -> Dict[str, Any]:
        creds = dict(self.storage.load_tokens("sults", tenant) or {})
        if tenant == "default":
            if not creds.get("token_v1"):
                env_v1 = os.getenv("SULTS_TOKEN_V1", "").strip()
                if env_v1:
                    creds["token_v1"] = env_v1
            if not creds.get("token_v2"):
                env_v2 = os.getenv("SULTS_TOKEN_V2", "").strip()
                if env_v2:
                    creds["token_v2"] = env_v2
        return creds

    def _get_token(self, tenant: str, version: str) -> str:
        ver = version.lower().strip()
        if ver not in ("v1", "v2"):
            raise SultsError(f"Versão Sults inválida: '{version}'. Use 'v1' ou 'v2'.")
        creds = self.load_credentials(tenant)
        token = (creds.get(f"token_{ver}") or "").strip()
        if not token:
            raise SultsError(
                f"Token Sults {ver} não configurado para o tenant '{tenant}'. "
                f"Cadastre o token da API {ver} no painel (ou defina SULTS_TOKEN_{ver.upper()} para o tenant 'default')."
            )
        return token

    async def request(
        self,
        tenant: str,
        version: str,
        method: str,
        path: str,
        query: Optional[Dict[str, Any]] = None,
        body: Optional[Any] = None,
        files: Optional[Dict[str, Tuple[str, bytes]]] = None,
    ) -> Any:
        ver = version.lower().strip()
        token = self._get_token(tenant, ver)
        method_upper = method.upper()
        clean_path = "/" + path.lstrip("/")
        url = f"{SULTS_API_URL}{clean_path}"
        params = {k: v for k, v in (query or {}).items() if v is not None}
        headers = {"Authorization": token, "Accept": "application/json"}

        resp = await _send_with_retry(
            method_upper,
            url,
            headers=headers,
            params=params,
            json_body=body if body not in (None, {}) else None,
            files=files,
        )

        if resp.status_code == 401:
            raise SultsError(
                f"HTTP 401 em {method_upper} {clean_path} (Sults {ver}): token ausente, inválido ou da versão errada "
                "(o token da v1 não funciona na v2 e vice-versa)."
            )
        if resp.status_code >= 400:
            raise SultsError(f"{method_upper} {clean_path} -> HTTP {resp.status_code}: {resp.text[:500]}")
        if not resp.content:
            return {"status": resp.status_code}
        try:
            return resp.json()
        except Exception:
            return {"status": resp.status_code, "text": resp.text[:2000]}
