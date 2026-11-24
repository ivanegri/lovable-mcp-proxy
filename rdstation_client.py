"""
Cliente HTTP da RD Station (Marketing API v2 + CRM API v2).

- Autenticação: OAuth 2.0 (`https://api.rd.services/auth/dialog` e `https://api.rd.services/auth/token`)
  usando `client_id` e `client_secret` configurados por tenant no painel ou globalmente via `.env`
  (`RDSTATION_CLIENT_ID` / `RDSTATION_CLIENT_SECRET`).
- Rolling Refresh Token: a RD Station invalida o `refresh_token` anterior a cada renovação e
  devolve um novo. Por isso toda renovação usa um `asyncio.Lock` por tenant e persiste o novo
  par `access_token` + `refresh_token` imediatamente no storage.
- Retentativas: 401 renova o token e repete uma vez; 429 e 5xx usam backoff exponencial (1 s, 2 s, 4 s).
"""

import os
import time
import asyncio
import logging
from typing import Any, Dict, Optional, Tuple

import httpx

from storage import StorageManager

logger = logging.getLogger("mcp-proxy.rdstation")

RDSTATION_API_URL = os.getenv("RDSTATION_API_URL", "https://api.rd.services").rstrip("/")
RDSTATION_AUTH_URL = f"{RDSTATION_API_URL}/auth/dialog"
RDSTATION_TOKEN_URL = f"{RDSTATION_API_URL}/auth/token"
EXPIRY_MARGIN = 300  # renova 5 min antes de expirar
BACKOFF_DELAYS = (1, 2, 4)


class RDStationError(Exception):
    pass


def resolve_client_credentials(
    storage: StorageManager,
    tenant: str,
) -> Tuple[str, str]:
    """Retorna (client_id, client_secret) priorizando o cadastro do tenant e caindo para .env."""
    saved = storage.load_tokens("rdstation", tenant) or {}
    client_id = (saved.get("client_id") or os.getenv("RDSTATION_CLIENT_ID", "")).strip()
    client_secret = (saved.get("client_secret") or os.getenv("RDSTATION_CLIENT_SECRET", "")).strip()
    return client_id, client_secret


async def exchange_code(
    client_id: str,
    client_secret: str,
    code: str,
) -> Dict[str, Any]:
    """Troca o authorization code por access_token e refresh_token na RD Station."""
    payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "grant_type": "authorization_code",
    }
    async with httpx.AsyncClient(timeout=20.0) as http:
        resp = await http.post(
            RDSTATION_TOKEN_URL,
            json=payload,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )
    if resp.status_code >= 400:
        raise RDStationError(f"Falha ao obter token RD Station ({resp.status_code}): {resp.text[:400]}")
    data = resp.json()
    if not data.get("access_token"):
        raise RDStationError(f"RD Station não retornou access_token: {data}")
    return data


async def refresh_token(
    client_id: str,
    client_secret: str,
    refresh_token_str: str,
) -> Dict[str, Any]:
    """Renova o access_token usando o refresh_token (rolling refresh token)."""
    payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token_str,
        "grant_type": "refresh_token",
    }
    async with httpx.AsyncClient(timeout=20.0) as http:
        resp = await http.post(
            RDSTATION_TOKEN_URL,
            json=payload,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )
    if resp.status_code >= 400:
        raise RDStationError(f"Falha ao renovar token RD Station ({resp.status_code}): {resp.text[:400]}")
    data = resp.json()
    if not data.get("access_token"):
        raise RDStationError(f"RD Station não retornou access_token no refresh: {data}")
    return data


class RDStationClient:
    def __init__(self, storage: StorageManager):
        self.storage = storage
        self._locks: Dict[str, asyncio.Lock] = {}

    def load_credentials(self, tenant: str) -> Dict[str, Any]:
        creds = dict(self.storage.load_tokens("rdstation", tenant) or {})
        cid, csec = resolve_client_credentials(self.storage, tenant)
        if cid:
            creds["client_id"] = cid
        if csec:
            creds["client_secret"] = csec
        return creds

    async def _ensure_token(self, tenant: str, force: bool = False) -> str:
        lock = self._locks.setdefault(tenant.lower(), asyncio.Lock())
        async with lock:
            creds = self.load_credentials(tenant)
            token = (creds.get("access_token") or "").strip()
            ref = (creds.get("refresh_token") or "").strip()
            exp = creds.get("expires_at") or 0

            if not token and not ref:
                raise RDStationError(
                    f"RD Station não conectada para o tenant '{tenant}'. "
                    "Configure client_id/client_secret e clique em 'Conectar RD Station' no painel."
                )

            needs_refresh = force or not token or (exp > 0 and exp - EXPIRY_MARGIN <= time.time())
            if needs_refresh:
                cid = (creds.get("client_id") or "").strip()
                csec = (creds.get("client_secret") or "").strip()
                if not ref:
                    if token and not force:
                        return token
                    raise RDStationError(
                        f"Token RD Station expirado e sem refresh_token para '{tenant}'. Reconecte via OAuth no painel."
                    )
                if not cid or not csec:
                    raise RDStationError(
                        f"client_id e client_secret da RD Station não configurados para '{tenant}' "
                        "(configure no painel ou defina RDSTATION_CLIENT_ID e RDSTATION_CLIENT_SECRET no .env)."
                    )
                logger.info(f"[RD Station] Renovando token para o tenant '{tenant}'...")
                new_data = await refresh_token(cid, csec, ref)
                creds["access_token"] = new_data["access_token"]
                if new_data.get("refresh_token"):
                    creds["refresh_token"] = new_data["refresh_token"]
                creds["expires_at"] = int(time.time()) + int(new_data.get("expires_in", 86400))
                creds["refreshed_at"] = int(time.time())
                self.storage.save_tokens("rdstation", tenant.lower(), creds)
                token = creds["access_token"]

            return token

    async def request(
        self,
        tenant: str,
        method: str,
        path: str,
        query: Optional[Dict[str, Any]] = None,
        body: Optional[Any] = None,
    ) -> Any:
        method_upper = method.upper()
        clean_path = "/" + path.lstrip("/")
        url = f"{RDSTATION_API_URL}{clean_path}"
        params = {k: v for k, v in (query or {}).items() if v is not None}

        refreshed_after_401 = False
        last_resp: Optional[httpx.Response] = None

        for attempt in range(len(BACKOFF_DELAYS) + 2):
            token = await self._ensure_token(tenant, force=False)
            headers = {
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            }
            async with httpx.AsyncClient(timeout=60.0) as http:
                resp = await http.request(
                    method_upper,
                    url,
                    params=params or None,
                    json=body if body not in (None, {}) else None,
                    headers=headers,
                )
            last_resp = resp

            if resp.status_code == 401 and not refreshed_after_401:
                refreshed_after_401 = True
                logger.info(f"[RD Station] 401 em {method_upper} {clean_path}; renovando token e repetindo.")
                await self._ensure_token(tenant, force=True)
                continue

            if (resp.status_code == 429 or resp.status_code >= 500) and attempt < len(BACKOFF_DELAYS):
                delay = BACKOFF_DELAYS[attempt]
                logger.warning(f"[RD Station] HTTP {resp.status_code} em {method_upper} {clean_path}; aguardando {delay}s.")
                await asyncio.sleep(delay)
                continue
            break

        assert last_resp is not None
        if last_resp.status_code >= 400:
            raise RDStationError(f"{method_upper} {clean_path} -> HTTP {last_resp.status_code}: {last_resp.text[:500]}")
        if not last_resp.content:
            return {"status": last_resp.status_code}
        try:
            return last_resp.json()
        except Exception:
            return {"status": last_resp.status_code, "text": last_resp.text[:2000]}
