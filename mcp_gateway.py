"""
Endpoint MCP nativo (Streamable HTTP) com token fixo.

Agentes (Claude, Cursor, N8N, Flowise...) se conectam como se fosse um servidor MCP comum,
enviando apenas uma chave fixa (x-api-key ou Authorization: Bearer). O gateway resolve o
tenant/serviço e repassa as chamadas para a sessão MCP upstream (Lovable ou Uscreen), que é
autenticada via OAuth e renovada automaticamente pelo SessionManager.

Rotas aceitas:
    /mcp                      -> serviço via header 'x-service' ou ?service= (padrão: lovable)
    /{service}/mcp            -> tenant resolvido pela chave
    /{service}/{tenant}/mcp   -> tenant explícito (chave do tenant ou Master Key)
"""

import json
import logging
import contextvars
import urllib.parse
from typing import Optional, Tuple, Any

from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings

from providers import PROVIDERS
from storage import StorageManager
from session_manager import SessionManager

logger = logging.getLogger("mcp-proxy.gateway")

# (service, tenant) da requisição atual, definido pelo wrapper ASGI
_current_target: contextvars.ContextVar[Optional[Tuple[str, str]]] = contextvars.ContextVar(
    "mcp_gateway_target", default=None
)

RESERVED_FIRST_SEGMENTS = {"api", "oauth", "health", "tools", "reconnect", "docs", "openapi.json", "redoc"}


class AuthError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def match_mcp_path(path: str) -> Optional[Tuple[Optional[str], Optional[str]]]:
    """Retorna (service, tenant) se o path for um endpoint MCP, senão None."""
    parts = [p for p in path.strip("/").split("/") if p]
    if not parts or parts[-1] != "mcp":
        return None
    if len(parts) == 1:
        return (None, None)
    if parts[0] in RESERVED_FIRST_SEGMENTS:
        return None
    if len(parts) == 2:
        return (parts[0], None)
    if len(parts) == 3:
        return (parts[0], parts[1])
    return None


def _header(headers: dict, name: str) -> Optional[str]:
    return headers.get(name.lower())


def resolve_target(
    storage: StorageManager,
    headers: dict,
    query: dict,
    path_service: Optional[str],
    path_tenant: Optional[str],
) -> Tuple[str, str]:
    """Valida a chave fixa e resolve (service, tenant)."""
    api_key = _header(headers, "x-api-key")
    if not api_key:
        auth = _header(headers, "authorization") or ""
        if auth.lower().startswith("bearer "):
            api_key = auth[7:].strip()
    if not api_key:
        api_key = (query.get("api_key") or query.get("key") or [None])[0]

    if not api_key:
        raise AuthError(401, "Envie a chave no header 'x-api-key' ou 'Authorization: Bearer <chave>'.")

    matched = storage.find_tenant_by_api_key(api_key)
    if not matched:
        raise AuthError(401, "API Key inválida.")

    if matched.get("is_master"):
        tenant = path_tenant or _header(headers, "x-tenant-id") or (query.get("tenant") or ["default"])[0]
    else:
        tenant = matched["id"]
        if path_tenant and path_tenant.lower() != tenant.lower():
            raise AuthError(403, f"Esta chave pertence ao tenant '{tenant}' e não pode acessar '{path_tenant}'.")

    service = (path_service or _header(headers, "x-service") or (query.get("service") or ["lovable"])[0]).lower()
    if service not in PROVIDERS:
        raise AuthError(404, f"Serviço '{service}' inválido. Suportados: {list(PROVIDERS.keys())}")

    return service, tenant.lower()


class MCPGateway:
    def __init__(self, storage: StorageManager, session_manager: SessionManager):
        self.storage = storage
        self.sessions = session_manager

        self.server = Server(
            "mcp-multi-tenant-bridge",
            version="2.1.0",
            instructions="Gateway MCP multi-tenant para Lovable e Uscreen.",
            on_list_tools=self._on_list_tools,
            on_call_tool=self._on_call_tool,
        )

        self.manager = StreamableHTTPSessionManager(
            app=self.server,
            stateless=True,       # cada requisição é independente -> escala e não depende de sticky session
            json_response=True,   # respostas JSON simples (compatível com N8N/cURL e clientes MCP)
            security_settings=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        )

    # ---------- Resolução do alvo dentro dos handlers ----------
    def _target_from_ctx(self, ctx: Any) -> Tuple[str, str]:
        target = _current_target.get()
        if target:
            return target
        # Fallback: tenta ler os headers da requisição HTTP anexada ao contexto
        req = getattr(ctx, "request", None)
        if req is not None and hasattr(req, "headers"):
            headers = {k.lower(): v for k, v in req.headers.items()}
            query = urllib.parse.parse_qs(str(getattr(req.url, "query", "") or ""))
            m = match_mcp_path(req.url.path) or (None, None)
            return resolve_target(self.storage, headers, query, m[0], m[1])
        raise RuntimeError("Não foi possível identificar o tenant desta requisição MCP.")

    # ---------- Handlers MCP ----------
    async def _on_list_tools(self, ctx: Any, params: Optional[types.PaginatedRequestParams]) -> types.ListToolsResult:
        service, tenant = self._target_from_ctx(ctx)
        return await self.sessions.run_with_session(
            service, tenant, lambda s: s.list_tools(params=params), retry_any_error=True
        )

    async def _on_call_tool(self, ctx: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
        service, tenant = self._target_from_ctx(ctx)
        try:
            return await self.sessions.run_with_session(
                service, tenant, lambda s: s.call_tool(params.name, params.arguments or {})
            )
        except Exception as e:
            logger.error(f"[MCP] Erro em tools/call '{params.name}' ({service}, {tenant}): {e}")
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=f"Erro ao executar '{params.name}': {e}")],
                isError=True,
            )

    # ---------- ASGI ----------
    async def handle(self, scope, receive, send, path_service: Optional[str], path_tenant: Optional[str]):
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        query = urllib.parse.parse_qs(scope.get("query_string", b"").decode("latin-1"))

        try:
            target = resolve_target(self.storage, headers, query, path_service, path_tenant)
        except AuthError as e:
            await _send_json(send, e.status, {"error": e.message}, www_auth=e.status == 401)
            return

        token = _current_target.set(target)
        try:
            await self.manager.handle_request(scope, receive, send)
        finally:
            _current_target.reset(token)


async def _send_json(send, status: int, payload: dict, www_auth: bool = False):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = [(b"content-type", b"application/json; charset=utf-8"), (b"content-length", str(len(body)).encode())]
    if www_auth:
        headers.append((b"www-authenticate", b'Bearer realm="mcp-bridge"'))
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


class GatewayASGIApp:
    """Envolve o FastAPI: intercepta rotas */mcp e repassa o resto para a API REST."""

    def __init__(self, api_app, gateway: MCPGateway):
        self.api_app = api_app
        self.gateway = gateway

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            m = match_mcp_path(scope.get("path", ""))
            if m is not None:
                await self.gateway.handle(scope, receive, send, m[0], m[1])
                return
        await self.api_app(scope, receive, send)
