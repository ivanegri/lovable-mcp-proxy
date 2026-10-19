import os
import json
import base64
import hashlib
import secrets
import urllib.parse
import urllib.request
import urllib.error
import logging
from typing import Optional, Dict, Any, Tuple

logger = logging.getLogger("mcp-proxy.providers")

LOVABLE_RAW_URL = os.getenv("LOVABLE_MCP_URL") or os.getenv("LOVABLE_SSE_URL") or "https://mcp.lovable.dev"
if LOVABLE_RAW_URL.endswith("/sse"):
    LOVABLE_MCP_URL = LOVABLE_RAW_URL[:-4]
else:
    LOVABLE_MCP_URL = LOVABLE_RAW_URL

USCREEN_RAW_URL = os.getenv("USCREEN_MCP_URL") or os.getenv("USCREEN_SSE_URL") or "https://app.uscreen.tv/mcp"
if USCREEN_RAW_URL.endswith("/sse"):
    USCREEN_MCP_URL = USCREEN_RAW_URL[:-4]
else:
    USCREEN_MCP_URL = USCREEN_RAW_URL


class OAuthProvider:
    def __init__(
        self,
        service_id: str,
        name: str,
        mcp_url: str,
        registration_endpoint: str,
        authorization_endpoint: str,
        token_endpoint: str,
        scopes: str,
        token_auth_method: str = "client_secret_basic",
        resource: Optional[str] = None,
        default_grant_types: Optional[list] = None
    ):
        self.service_id = service_id
        self.name = name
        self.mcp_url = mcp_url
        self.registration_endpoint = registration_endpoint
        self.authorization_endpoint = authorization_endpoint
        self.token_endpoint = token_endpoint
        self.scopes = scopes
        self.token_auth_method = token_auth_method
        self.resource = resource
        self.default_grant_types = default_grant_types or ["authorization_code", "refresh_token"]


PROVIDERS: Dict[str, OAuthProvider] = {
    "lovable": OAuthProvider(
        service_id="lovable",
        name="Lovable",
        mcp_url=LOVABLE_MCP_URL,
        registration_endpoint=os.getenv("LOVABLE_REGISTRATION_ENDPOINT", "https://lovable.dev/oauth/register"),
        authorization_endpoint=os.getenv("LOVABLE_AUTHORIZE_ENDPOINT", "https://lovable.dev/oauth/authorize"),
        token_endpoint=os.getenv("LOVABLE_TOKEN_ENDPOINT", "https://lovable.dev/oauth/token"),
        scopes="offline projects:read projects:write workspaces:read workspaces:write",
        token_auth_method="client_secret_basic",
        resource=None,
        default_grant_types=["authorization_code", "refresh_token"]
    ),
    "uscreen": OAuthProvider(
        service_id="uscreen",
        name="Uscreen",
        mcp_url=USCREEN_MCP_URL,
        registration_endpoint=os.getenv("USCREEN_REGISTRATION_ENDPOINT", "https://app.uscreen.tv/oauth/register"),
        authorization_endpoint=os.getenv("USCREEN_AUTHORIZE_ENDPOINT", "https://app.uscreen.tv/oauth/authorize"),
        token_endpoint=os.getenv("USCREEN_TOKEN_ENDPOINT", "https://app.uscreen.tv/oauth/token"),
        scopes="content_analytics.read finance_analytics.read crm.read cms.read products.read cms.write crm.write",
        token_auth_method="none",
        resource="https://app.uscreen.tv/mcp",
        default_grant_types=["authorization_code", "refresh_token"]
    )
}


def get_provider(service: str) -> OAuthProvider:
    service_clean = service.lower().strip()
    if service_clean not in PROVIDERS:
        raise ValueError(f"Serviço desconhecido: '{service}'. Serviços suportados: {list(PROVIDERS.keys())}")
    return PROVIDERS[service_clean]


def generate_pkce() -> Tuple[str, str, str]:
    """Gera code_verifier, code_challenge (S256) e state conforme a especificação OAuth 2.1."""
    code_verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(code_verifier.encode("utf-8")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).decode("utf-8").replace("=", "")
    state = secrets.token_urlsafe(24)
    return code_verifier, code_challenge, state


def _client_cache_key(provider: OAuthProvider, redirect_uri: str) -> str:
    uri_hash = hashlib.sha256(redirect_uri.encode("utf-8")).hexdigest()[:12]
    return f"{provider.service_id}_{uri_hash}.json"


def get_or_register_client(provider: OAuthProvider, redirect_uri: str, data_dir: str) -> Tuple[str, Optional[str]]:
    """Obtém ou registra cliente OAuth dinamicamente para o provedor e redirect_uri especificados."""
    clients_dir = os.path.join(data_dir, "clients")
    os.makedirs(clients_dir, exist_ok=True)
    cache_file = os.path.join(clients_dir, _client_cache_key(provider, redirect_uri))

    # Verifica cache existente
    if os.path.exists(cache_file):
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if data.get("client_id") and data.get("redirect_uri") == redirect_uri:
                    return data["client_id"], data.get("client_secret")
        except Exception as e:
            logger.warning(f"Erro ao ler cache do cliente ({cache_file}): {e}")

    # Fallback para oauth_client.json legado se for Lovable
    legacy_client = os.path.join(data_dir, "oauth_client.json")
    if provider.service_id == "lovable" and os.path.exists(legacy_client):
        try:
            with open(legacy_client, "r", encoding="utf-8") as f:
                data = json.load(f)
                if data.get("client_id") and data.get("redirect_uri") == redirect_uri:
                    return data["client_id"], data.get("client_secret")
        except Exception:
            pass

    # Registra dinamicamente no endpoint do provedor
    logger.info(f"Registrando novo cliente OAuth no {provider.name} para redirect_uri '{redirect_uri}'...")
    payload_data: Dict[str, Any] = {
        "client_name": f"MCP Hub ({provider.name})",
        "redirect_uris": [redirect_uri],
        "grant_types": provider.default_grant_types,
        "response_types": ["code"],
        "scope": provider.scopes
    }
    if provider.token_auth_method:
        payload_data["token_endpoint_auth_method"] = provider.token_auth_method

    payload = json.dumps(payload_data).encode("utf-8")
    req = urllib.request.Request(
        provider.registration_endpoint,
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": "MCP-Proxy-Hub"}
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            client_data = json.load(resp)
    except urllib.error.HTTPError as he:
        body = ""
        try:
            body = he.read().decode("utf-8", errors="replace")[:400]
        except Exception:
            pass
        logger.error(f"Falha ao registrar cliente no {provider.name} ({he.code}): {body}")
        raise RuntimeError(f"Erro ao registrar cliente no {provider.name}: {he.code} - {body}")

    client_id = client_data.get("client_id")
    client_secret = client_data.get("client_secret")
    if not client_id:
        raise RuntimeError(f"Provedor {provider.name} não retornou client_id no registro: {client_data}")

    # Persiste no cache
    to_save = {
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_types": client_data.get("grant_types") or provider.default_grant_types,
        "token_endpoint_auth_method": client_data.get("token_endpoint_auth_method", provider.token_auth_method)
    }
    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump(to_save, f, indent=2)

    logger.info(f"Cliente OAuth {provider.name} registrado com sucesso (ID: {client_id[:8]}...)")
    return client_id, client_secret


def build_authorization_url(
    provider: OAuthProvider,
    client_id: str,
    redirect_uri: str,
    code_challenge: str,
    state: str
) -> str:
    """Constrói a URL de autorização OAuth 2.1 com PKCE para o usuário abrir no navegador."""
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": provider.scopes,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "state": state
    }
    if provider.resource:
        params["resource"] = provider.resource

    return f"{provider.authorization_endpoint}?{urllib.parse.urlencode(params)}"


def exchange_code_for_tokens(
    provider: OAuthProvider,
    client_id: str,
    client_secret: Optional[str],
    code: str,
    code_verifier: str,
    redirect_uri: str
) -> Dict[str, Any]:
    """Troca o authorization_code pelo access_token e refresh_token."""
    post_data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "code_verifier": code_verifier,
        "client_id": client_id
    }
    if provider.resource:
        post_data["resource"] = provider.resource

    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "MCP-Proxy-Hub"
    }

    if client_secret:
        creds = f"{client_id}:{client_secret}".encode("utf-8")
        headers["Authorization"] = "Basic " + base64.b64encode(creds).decode("utf-8")
    else:
        # Clientes públicos (como Uscreen com método none) passam client_id no body
        if "client_id" not in post_data:
            post_data["client_id"] = client_id

    data = urllib.parse.urlencode(post_data).encode("utf-8")
    req = urllib.request.Request(provider.token_endpoint, data=data, headers=headers)

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            tokens = json.load(resp)
    except urllib.error.HTTPError as he:
        body = ""
        try:
            body = he.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            pass
        raise RuntimeError(f"Erro na troca de código no {provider.name} ({he.code}): {body}")

    return tokens


def refresh_access_token(
    provider: OAuthProvider,
    client_id: str,
    client_secret: Optional[str],
    refresh_token_str: str
) -> Dict[str, Any]:
    """Renova o access_token usando o refresh_token."""
    post_data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token_str,
        "client_id": client_id
    }
    if provider.resource:
        post_data["resource"] = provider.resource

    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "MCP-Proxy-Hub"
    }

    if client_secret:
        creds = f"{client_id}:{client_secret}".encode("utf-8")
        headers["Authorization"] = "Basic " + base64.b64encode(creds).decode("utf-8")

    data = urllib.parse.urlencode(post_data).encode("utf-8")
    req = urllib.request.Request(provider.token_endpoint, data=data, headers=headers)

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            new_tokens = json.load(resp)
    except urllib.error.HTTPError as he:
        body = ""
        try:
            body = he.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            pass
        raise RuntimeError(f"Erro ao renovar token no {provider.name} ({he.code}): {body}")

    return new_tokens
