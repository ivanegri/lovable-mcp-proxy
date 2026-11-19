"""
Cliente HTTP da API Omie ERP (JSON-RPC sobre HTTP POST).

- Autenticação: `app_key` e `app_secret` enviados no corpo JSON de cada chamada POST.
- Formato padrão:
    POST https://app.omie.com.br/api/v1/<modulo>/<servico>/
    {"call": "<Metodo>", "app_key": "...", "app_secret": "...", "param": [{...}]}
- Rate limit (regras oficiais da Omie):
    - 960 req/min por IP, 240 req/min por IP+app_key+método, 4 requisições simultâneas.
    - Aplicamos um semáforo de 4 conexões simultâneas + espaçamento mínimo global de 0,25 s (~4 req/s)
      e backoff exponencial (1 s, 2 s, 4 s, 8 s) em 429, 5xx transitórios ou faults de rate limit.

Credenciais são lidas de `storage.load_tokens("omie", tenant)` e, como fallback para o tenant
`default`, das variáveis `OMIE_APP_KEY` e `OMIE_APP_SECRET`.
"""

import os
import time
import asyncio
import logging
from typing import Any, Dict, Optional

import httpx

from storage import StorageManager

logger = logging.getLogger("mcp-proxy.omie")

OMIE_API_URL = os.getenv("OMIE_API_URL", "https://app.omie.com.br").rstrip("/")
RATE_LIMIT_INTERVAL = 0.25  # ~4 req/s global (240/min, seguro dentro dos limites da Omie)
MAX_CONCURRENT_REQUESTS = 4
BACKOFF_DELAYS = (1, 2, 4, 8)

_rate_lock: Optional[asyncio.Lock] = None
_concurrency_sem: Optional[asyncio.Semaphore] = None
_last_request_at: float = 0.0

_RETRYABLE_FAULT_TERMS = (
    "MISUSE_API_PROCESS",
    "REDUNDANT",
    "TOO MANY REQUESTS",
    "LIMITE DE REQUISI",
    "AGUARDE",
    "CONCORR",
    "BLOQUEAD",
)


class OmieError(Exception):
    pass


def _get_rate_lock() -> asyncio.Lock:
    global _rate_lock
    if _rate_lock is None:
        _rate_lock = asyncio.Lock()
    return _rate_lock


def _get_semaphore() -> asyncio.Semaphore:
    global _concurrency_sem
    if _concurrency_sem is None:
        _concurrency_sem = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
    return _concurrency_sem


async def _acquire_rate_slot() -> None:
    global _last_request_at
    async with _get_rate_lock():
        now = time.monotonic()
        wait = RATE_LIMIT_INTERVAL - (now - _last_request_at)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_request_at = time.monotonic()


def _parse_omie_fault(resp: httpx.Response) -> Optional[Dict[str, str]]:
    """Extrai faultcode/faultstring se a resposta for um erro estruturado da Omie."""
    try:
        data = resp.json()
    except Exception:
        return None
    if isinstance(data, dict) and ("faultstring" in data or "faultcode" in data):
        return {
            "faultcode": str(data.get("faultcode", "")),
            "faultstring": str(data.get("faultstring", "")),
        }
    return None


def _is_retryable_response(resp: httpx.Response, fault: Optional[Dict[str, str]]) -> bool:
    if resp.status_code == 429:
        return True
    if fault:
        combined = f"{fault.get('faultcode', '')} {fault.get('faultstring', '')}".upper()
        if any(term in combined for term in _RETRYABLE_FAULT_TERMS):
            return True
        # Omie devolve HTTP 500 com SOAP-ENV:Client-* para erros determinísticos de negócio/auth
        if "CLIENT" in fault.get("faultcode", "").upper():
            return False
    return resp.status_code >= 500


async def _post_omie(
    service_path: str,
    call: str,
    app_key: str,
    app_secret: str,
    param: Optional[Dict[str, Any]] = None,
    timeout: float = 60.0,
) -> Any:
    clean_path = "/" + service_path.strip("/") + "/"
    url = f"{OMIE_API_URL}{clean_path}"
    payload = {
        "call": call,
        "app_key": app_key,
        "app_secret": app_secret,
        "param": [param if param is not None else {}],
    }
    headers = {"Content-Type": "application/json", "Accept": "application/json"}

    total_attempts = len(BACKOFF_DELAYS) + 1
    last_resp: Optional[httpx.Response] = None
    last_fault: Optional[Dict[str, str]] = None

    for attempt in range(total_attempts):
        async with _get_semaphore():
            await _acquire_rate_slot()
            async with httpx.AsyncClient(timeout=timeout) as http:
                resp = await http.post(url, json=payload, headers=headers)

        fault = _parse_omie_fault(resp)
        last_resp = resp
        last_fault = fault

        if _is_retryable_response(resp, fault) and attempt < len(BACKOFF_DELAYS):
            delay = BACKOFF_DELAYS[attempt]
            logger.warning(
                f"[Omie] HTTP {resp.status_code} em {clean_path} ({call}); aguardando {delay}s para retry..."
            )
            await asyncio.sleep(delay)
            continue
        break

    assert last_resp is not None
    if last_fault:
        fc = last_fault["faultcode"]
        fs = last_fault["faultstring"]
        raise OmieError(f"Omie {clean_path} ({call}) -> {fc}: {fs}")
    if last_resp.status_code >= 400:
        raise OmieError(
            f"Omie {clean_path} ({call}) -> HTTP {last_resp.status_code}: {last_resp.text[:500]}"
        )
    if not last_resp.content:
        return {"status": last_resp.status_code}
    try:
        return last_resp.json()
    except Exception:
        return {"status": last_resp.status_code, "text": last_resp.text[:2000]}


async def validate_credentials(app_key: str, app_secret: str) -> Dict[str, Any]:
    """Valida app_key e app_secret consultando a lista de empresas na Omie."""
    key = (app_key or "").strip()
    secret = (app_secret or "").strip()
    if not key or not secret:
        raise OmieError("Informe APP_KEY e APP_SECRET da Omie.")

    data = await _post_omie(
        service_path="/api/v1/geral/empresas/",
        call="ListarEmpresas",
        app_key=key,
        app_secret=secret,
        param={"pagina": 1, "registros_por_pagina": 1},
        timeout=20.0,
    )
    empresas = data.get("empresas_cadastro") if isinstance(data, dict) else None
    empresa_nome = ""
    if isinstance(empresas, list) and empresas:
        empresa_nome = empresas[0].get("nome_fantasia") or empresas[0].get("razao_social") or ""
    return {"valid": True, "empresa": empresa_nome}


class OmieClient:
    def __init__(self, storage: StorageManager):
        self.storage = storage

    def load_credentials(self, tenant: str) -> Dict[str, Any]:
        creds = dict(self.storage.load_tokens("omie", tenant) or {})
        if tenant == "default":
            if not creds.get("app_key"):
                env_key = os.getenv("OMIE_APP_KEY", "").strip()
                if env_key:
                    creds["app_key"] = env_key
            if not creds.get("app_secret"):
                env_secret = os.getenv("OMIE_APP_SECRET", "").strip()
                if env_secret:
                    creds["app_secret"] = env_secret
        return creds

    async def call(
        self,
        tenant: str,
        service_path: str,
        call: str,
        param: Optional[Dict[str, Any]] = None,
    ) -> Any:
        creds = self.load_credentials(tenant)
        app_key = (creds.get("app_key") or "").strip()
        app_secret = (creds.get("app_secret") or "").strip()
        if not app_key or not app_secret:
            raise OmieError(
                f"Omie não configurada para o tenant '{tenant}'. "
                "Cadastre APP_KEY e APP_SECRET no painel (ou defina OMIE_APP_KEY e OMIE_APP_SECRET para o tenant 'default')."
            )
        return await _post_omie(service_path, call, app_key, app_secret, param=param)
