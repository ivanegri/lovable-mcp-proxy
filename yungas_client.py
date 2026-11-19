"""
Cliente HTTP da Yungas (Public API v3 + PEX v1).

- API v3: OAuth2 client_credentials (Basic client_id:client_secret). O token é obtido sob demanda,
  mantido em cache por tenant e renovado automaticamente (antes de expirar e em caso de 401).
- PEX v1: autenticação por API-KEY fixa.

Credenciais são lidas de storage.load_tokens("yungas", tenant) e, como fallback para o tenant
'default', das variáveis YUNGAS_CLIENT_ID / YUNGAS_CLIENT_SECRET / YUNGAS_SCOPES / YUNGAS_ENV /
YUNGAS_PEX_API_KEY.
"""

import os
import time
import asyncio
import logging
from typing import Any, Dict, Optional, Tuple

import httpx

from storage import StorageManager

logger = logging.getLogger("mcp-proxy.yungas")

ENV_HOSTS = {
    "production": "https://app.yungas.com.br",
    "staging": "https://staging.yungas.com.br",
}
PEX_HOST = os.getenv("YUNGAS_PEX_URL", "https://pex.yungas.com.br").rstrip("/")
DEFAULT_SCOPES = (
    "files.all ticket.all revenue.all user.all franchise.all tag.all franchisor.all "
    "faq.all checklist.all health-score.all store.all news.all helpdesk.all"
)
EXPIRY_MARGIN = 60  # renova 60s antes de expirar


class YungasError(Exception):
    pass


def host_for_env(env: Optional[str]) -> str:
    return ENV_HOSTS.get((env or "production").lower(), ENV_HOSTS["production"])


def fetch_token_sync_params(creds: Dict[str, Any]) -> Tuple[str, Dict[str, str], Dict[str, str]]:
    """Monta (url, auth, data) para o POST /oauth/token."""
    url = f"{host_for_env(creds.get('env'))}/api/v3/public/oauth/token"
    data = {"grant_type": "client_credentials", "scope": creds.get("scopes") or DEFAULT_SCOPES}
    return url, (creds["client_id"], creds["client_secret"]), data


async def request_token(creds: Dict[str, Any]) -> Dict[str, Any]:
    """Obtém um access_token novo. Levanta YungasError em caso de falha."""
    url, auth, data = fetch_token_sync_params(creds)
    async with httpx.AsyncClient(timeout=20) as http:
        resp = await http.post(url, data=data, auth=auth)
    if resp.status_code >= 400:
        raise YungasError(f"Falha ao autenticar na Yungas ({resp.status_code}): {resp.text[:300]}")
    payload = resp.json()
    if not payload.get("access_token"):
        raise YungasError(f"A Yungas não retornou access_token: {payload}")
    return payload


class YungasClient:
    def __init__(self, storage: StorageManager):
        self.storage = storage
        self._locks: Dict[str, asyncio.Lock] = {}

    # ---------- credenciais ----------
    def load_credentials(self, tenant: str) -> Dict[str, Any]:
        creds = dict(self.storage.load_tokens("yungas", tenant) or {})
        if tenant == "default":
            creds.setdefault("client_id", os.getenv("YUNGAS_CLIENT_ID", ""))
            creds.setdefault("client_secret", os.getenv("YUNGAS_CLIENT_SECRET", ""))
            creds.setdefault("scopes", os.getenv("YUNGAS_SCOPES", ""))
            creds.setdefault("env", os.getenv("YUNGAS_ENV", "production"))
            creds.setdefault("pex_api_key", os.getenv("YUNGAS_PEX_API_KEY", ""))
        return creds

    async def _ensure_token(self, tenant: str, force: bool = False) -> Tuple[str, Dict[str, Any]]:
        lock = self._locks.setdefault(tenant, asyncio.Lock())
        async with lock:
            creds = self.load_credentials(tenant)
            if not creds.get("client_id") or not creds.get("client_secret"):
                raise YungasError(
                    f"Yungas não configurada para o tenant '{tenant}'. "
                    "Cadastre client_id e client_secret no painel."
                )
            token = creds.get("access_token")
            exp = creds.get("expires_at") or 0
            if force or not token or exp - EXPIRY_MARGIN <= time.time():
                logger.info(f"[Yungas] Obtendo novo token para o tenant '{tenant}'...")
                payload = await request_token(creds)
                creds["access_token"] = payload["access_token"]
                creds["expires_at"] = int(time.time()) + int(payload.get("expires_in", 3600))
                creds["refreshed_at"] = int(time.time())
                self.storage.save_tokens("yungas", tenant, creds)
                token = creds["access_token"]
            return token, creds

    # ---------- requisições ----------
    async def request(
        self,
        tenant: str,
        method: str,
        path: str,
        query: Optional[Dict[str, Any]] = None,
        body: Optional[Any] = None,
        api: str = "v3",
    ) -> Any:
        """api='v3' usa Bearer OAuth; api='pex' usa a API-KEY do PEX."""
        method = method.upper()
        path = "/" + path.lstrip("/")
        params = {k: v for k, v in (query or {}).items() if v is not None}

        for attempt in range(3):
            if api == "pex":
                creds = self.load_credentials(tenant)
                key = creds.get("pex_api_key")
                if not key:
                    raise YungasError(f"PEX API key não configurada para o tenant '{tenant}'.")
                url = f"{PEX_HOST}/api/v1/public{path}"
                headers = {"X-API-KEY": key, "Accept": "application/json"}
            else:
                token, creds = await self._ensure_token(tenant, force=(attempt == 1))
                url = f"{host_for_env(creds.get('env'))}/api/v3/public{path}"
                headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

            async with httpx.AsyncClient(timeout=60) as http:
                resp = await http.request(
                    method, url, params=params, json=body if body not in (None, {}) else None, headers=headers
                )

            if resp.status_code == 401 and api == "v3" and attempt == 0:
                logger.info("[Yungas] 401 recebido, renovando token e repetindo a chamada.")
                continue
            if resp.status_code == 405 and attempt < 2:  # limite de 1000 req / 5 min
                await asyncio.sleep(2 * (attempt + 1))
                continue
            break

        if resp.status_code >= 400:
            raise YungasError(f"{method} {path} -> HTTP {resp.status_code}: {resp.text[:500]}")
        if not resp.content:
            return {"status": resp.status_code}
        try:
            return resp.json()
        except Exception:
            return {"status": resp.status_code, "text": resp.text[:2000]}
