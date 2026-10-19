import os
import time
import json
import asyncio
import logging
import urllib.parse
from contextlib import asynccontextmanager
from typing import Optional, Dict, Any, List, Tuple

from fastapi import FastAPI, HTTPException, Request, Header, Query, Path, status
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from pydantic import BaseModel

from providers import (
    PROVIDERS,
    get_provider,
    generate_pkce,
    get_or_register_client,
    build_authorization_url,
    exchange_code_for_tokens
)
from storage import StorageManager, INTERNAL_API_KEY
from session_manager import SessionManager, is_auth_error
from dashboard import get_dashboard_html

# Configuração de Logs
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("mcp-proxy")

DATA_DIR = os.getenv("DATA_DIR", "/app/data")
PUBLIC_URL = os.getenv("PUBLIC_URL", "").rstrip("/")
storage = StorageManager(DATA_DIR)
session_manager = SessionManager(storage)


def get_request_base_url(request: Request) -> str:
    """Calcula a base_url pública respeitando PUBLIC_URL, headers de proxy reverso e SSL."""
    if PUBLIC_URL:
        return PUBLIC_URL

    # Headers comuns de reverse proxy (Nginx, Caddy, Cloudflare, Traefik)
    forwarded_proto = request.headers.get("x-forwarded-proto") or ("https" if request.headers.get("x-forwarded-ssl") == "on" else None)
    forwarded_host = request.headers.get("x-forwarded-host") or request.headers.get("host")

    if forwarded_host:
        proto = forwarded_proto or ("http" if "localhost" in forwarded_host or "127.0.0.1" in forwarded_host else "https")
        return f"{proto}://{forwarded_host}"

    url = str(request.base_url).rstrip("/")
    # Se não for localhost e não for https, força https
    if not ("localhost" in url or "127.0.0.1" in url) and url.startswith("http://"):
        url = "https://" + url[7:]
    return url

# Armazenamento temporário de estados OAuth pendentes (state -> {service, tenant, ...})
_pending_oauth: Dict[str, Dict[str, Any]] = {}
_oauth_lock = asyncio.Lock()


def clean_expired_oauth_states():
    now = time.time()
    expired = [k for k, v in _pending_oauth.items() if v.get("expires_at", 0) < now]
    for k in expired:
        _pending_oauth.pop(k, None)


def resolve_auth(
    request: Request,
    target_service: Optional[str] = None,
    target_tenant: Optional[str] = None
) -> Tuple[str, str]:
    """Valida a autenticação e resolve o serviço e o tenant para a requisição.

    Retorna: (service, tenant_id)
    """
    x_api_key = request.headers.get("x-api-key")
    if not x_api_key:
        auth_header = request.headers.get("authorization", "")
        if auth_header.lower().startswith("bearer "):
            x_api_key = auth_header[7:].strip()

    if not x_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Header 'x-api-key' ou 'Authorization: Bearer <key>' obrigatório."
        )

    matched = storage.find_tenant_by_api_key(x_api_key)
    if not matched:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API Key inválida ou não autorizada."
        )

    is_master = matched.get("is_master", False)

    # Resolução do Tenant
    if is_master:
        # Se for Master Key, permite especificar qualquer tenant via header ou parâmetro
        resolved_tenant = target_tenant or request.headers.get("x-tenant-id") or "default"
    else:
        # Se for chave específica de um tenant, trava no tenant dono da chave
        resolved_tenant = matched["id"]
        if target_tenant and target_tenant.lower() != resolved_tenant.lower():
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Esta API Key pertence exclusivamente ao tenant '{resolved_tenant}' e não pode acessar '{target_tenant}'."
            )

    # Resolução do Serviço
    resolved_service = target_service or request.headers.get("x-service") or "lovable"
    resolved_service = resolved_service.lower().strip()

    if resolved_service not in PROVIDERS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Serviço '{resolved_service}' inválido. Suportados: {list(PROVIDERS.keys())}"
        )

    return resolved_service, resolved_tenant


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Iniciando MCP Multi-Tenant Bridge...")
    session_manager.start_background_task()
    yield
    logger.info("Encerrando MCP Multi-Tenant Bridge...")
    await session_manager.close_all()


app = FastAPI(
    title="MCP Multi-Tenant Bridge (Lovable & Uscreen)",
    description="Gateway multi-tenant seguro para integração com Lovable e Uscreen via Model Context Protocol (MCP)",
    version="2.0.0",
    lifespan=lifespan
)


# ==========================================
# 1. Web Dashboard & Informações
# ==========================================

@app.get("/", response_class=HTMLResponse)
async def dashboard_view():
    """Retorna o painel visual de gerenciamento multi-tenant e conexão OAuth."""
    return HTMLResponse(content=get_dashboard_html())


@app.get("/health")
async def health_check():
    """Status consolidado do sistema e dos tenants."""
    tenants = storage.get_all_tenants()
    token_records = storage.list_all_token_records()
    active_connections = sum(1 for r in token_records if r.get("connected") and not r.get("is_expired"))

    return {
        "status": "healthy",
        "service": "MCP Multi-Tenant Bridge",
        "supported_providers": list(PROVIDERS.keys()),
        "total_tenants": len(tenants),
        "active_connections": active_connections,
        "token_records": token_records
    }


# ==========================================
# 2. Gerenciamento de Tenants (API)
# ==========================================

class CreateTenantRequest(BaseModel):
    id: str
    name: str
    api_key: Optional[str] = None
    description: Optional[str] = ""


@app.get("/api/tenants")
async def list_tenants(request: Request):
    """Lista todos os tenants e seu status de conexão (Requer Master Key ou chave válida)."""
    resolve_auth(request)
    return {
        "tenants": storage.get_all_tenants(),
        "tokens_status": storage.list_all_token_records()
    }


@app.post("/api/tenants")
async def create_tenant(payload: CreateTenantRequest, request: Request):
    """Cria ou atualiza um tenant."""
    resolve_auth(request)
    try:
        created = storage.create_or_update_tenant(
            tenant_id=payload.id,
            name=payload.name,
            api_key=payload.api_key,
            description=payload.description or ""
        )
        return {"status": "ok", "tenant": created}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/tenants/{tenant_id}")
async def delete_tenant(tenant_id: str, request: Request):
    """Remove um tenant e suas credenciais associadas."""
    resolve_auth(request)
    try:
        success = storage.delete_tenant(tenant_id)
        if not success:
            raise HTTPException(status_code=404, detail="Tenant não encontrado ou protegido.")
        return {"status": "ok", "message": f"Tenant '{tenant_id}' removido com sucesso."}
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))


@app.delete("/api/tokens/{service}/{tenant_id}")
async def disconnect_service_token(service: str, tenant_id: str, request: Request):
    """Revoga e remove o token de um serviço para o tenant especificado."""
    resolve_auth(request, target_service=service, target_tenant=tenant_id)
    storage.delete_tokens(service, tenant_id)
    return {"status": "ok", "message": f"Conexão com {service.upper()} para '{tenant_id}' removida."}


# ==========================================
# 3. Fluxo OAuth 2.1 no Navegador (1-Click)
# ==========================================

@app.get("/oauth/login")
async def oauth_login(
    service: str = Query(..., description="lovable ou uscreen"),
    tenant: str = Query("default", description="ID do tenant"),
    request: Request = None
):
    """Inicia o fluxo OAuth com o provedor selecionado redirecionando o navegador para autorização."""
    service_clean = service.lower().strip()
    if service_clean not in PROVIDERS:
        return RedirectResponse(f"/?error=Serviço+{service_clean}+inválido")

    provider = get_provider(service_clean)
    tenant_info = storage.get_tenant(tenant)
    if not tenant_info:
        # Se o tenant não existe ainda, cria-o automaticamente
        storage.create_or_update_tenant(tenant, f"Tenant {tenant}")

    # Determina o redirect_uri baseado no host de acesso da requisição
    base_url = get_request_base_url(request)
    redirect_uri = f"{base_url}/oauth/callback"

    try:
        client_id, client_secret = get_or_register_client(provider, redirect_uri, storage.data_dir)
    except Exception as e:
        logger.error(f"Erro ao registrar client para {service_clean}: {e}")
        return RedirectResponse(f"/?error={urllib.parse.quote('Erro ao registrar cliente OAuth: ' + str(e))}")

    code_verifier, code_challenge, state = generate_pkce()

    async with _oauth_lock:
        clean_expired_oauth_states()
        _pending_oauth[state] = {
            "service": service_clean,
            "tenant": tenant,
            "code_verifier": code_verifier,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "expires_at": time.time() + 600
        }

    auth_url = build_authorization_url(
        provider=provider,
        client_id=client_id,
        redirect_uri=redirect_uri,
        code_challenge=code_challenge,
        state=state
    )

    logger.info(f"Redirecionando usuário para autorização do {provider.name} (Tenant: {tenant})...")
    return RedirectResponse(url=auth_url, status_code=status.HTTP_302_FOUND)


@app.get("/oauth/callback")
async def oauth_callback(
    code: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    error: Optional[str] = Query(None),
    error_description: Optional[str] = Query(None)
):
    """Callback chamado pelo provedor OAuth (Lovable / Uscreen) com o código de autorização."""
    if error or not code or not state:
        err_msg = error_description or error or "Autorização cancelada ou código ausente"
        logger.warning(f"OAuth Callback retornou erro: {err_msg}")
        return RedirectResponse(f"/?error={urllib.parse.quote(err_msg)}")

    async with _oauth_lock:
        pending = _pending_oauth.pop(state, None)

    if not pending:
        logger.warning("OAuth Callback recebido com state inválido ou expirado.")
        return RedirectResponse("/?error=Sessão+de+autorização+expirada.+Tente+novamente.")

    service = pending["service"]
    tenant = pending["tenant"]
    provider = get_provider(service)

    try:
        logger.info(f"Trocando authorization_code por tokens para ({service}, {tenant})...")
        tokens = await asyncio.to_thread(
            exchange_code_for_tokens,
            provider=provider,
            client_id=pending["client_id"],
            client_secret=pending["client_secret"],
            code=code,
            code_verifier=pending["code_verifier"],
            redirect_uri=pending["redirect_uri"]
        )

        tokens["expires_at"] = int(time.time()) + int(tokens.get("expires_in", 3600))
        tokens["refreshed_at"] = int(time.time())
        storage.save_tokens(service, tenant, tokens)

        # Conecta a sessão imediatamente em segundo plano
        asyncio.create_task(session_manager.connect_session(service, tenant))

        logger.info(f"✅ Autorização concluída com sucesso para ({service}, {tenant})!")
        return RedirectResponse(f"/?connected={service}&tenant={tenant}&status=success")

    except Exception as e:
        logger.error(f"Erro ao trocar código por tokens para ({service}, {tenant}): {e}")
        return RedirectResponse(f"/?error={urllib.parse.quote('Falha ao obter tokens: ' + str(e))}")


# ==========================================
# 4. Endpoints de Execução MCP (Para Agentes)
# ==========================================

# 4.1 Rota Específica: /{service}/{tenant}/tools
@app.get("/{service}/{tenant}/tools")
@app.get("/{service}/{tenant}/tools/")
async def list_tools_scoped(
    service: str,
    tenant: str,
    request: Request
):
    """Lista as ferramentas disponíveis para o serviço e tenant especificados."""
    resolved_service, resolved_tenant = resolve_auth(request, target_service=service, target_tenant=tenant)
    try:
        response = await session_manager.run_with_session(
            service=resolved_service,
            tenant_id=resolved_tenant,
            op=lambda s: s.list_tools(),
            retry_any_error=True
        )
        return {"service": resolved_service, "tenant": resolved_tenant, "tools": [t.model_dump() for t in response.tools]}
    except Exception as e:
        logger.error(f"Erro ao listar ferramentas ({resolved_service}, {resolved_tenant}): {e}")
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e))


@app.post("/{service}/{tenant}/tools/{tool_name}")
@app.post("/{service}/{tenant}/tools/{tool_name}/")
async def call_tool_scoped(
    service: str,
    tenant: str,
    tool_name: str,
    request: Request
):
    """Executa uma ferramenta no servidor MCP para o serviço e tenant especificados."""
    resolved_service, resolved_tenant = resolve_auth(request, target_service=service, target_tenant=tenant)

    try:
        arguments = await request.json()
    except Exception:
        arguments = {}

    if not isinstance(arguments, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="O corpo da requisição deve ser um objeto JSON."
        )

    try:
        result = await session_manager.run_with_session(
            service=resolved_service,
            tenant_id=resolved_tenant,
            op=lambda s: s.call_tool(tool_name, arguments)
        )
        return {"service": resolved_service, "tenant": resolved_tenant, "result": result.model_dump()}
    except Exception as e:
        logger.error(f"Erro ao executar a ferramenta '{tool_name}' ({resolved_service}, {resolved_tenant}): {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@app.post("/{service}/{tenant}/reconnect")
@app.post("/{service}/{tenant}/reconnect/")
async def reconnect_scoped(service: str, tenant: str, request: Request):
    """Força reconexão MCP para um tenant e serviço específicos."""
    resolved_service, resolved_tenant = resolve_auth(request, target_service=service, target_tenant=tenant)
    try:
        await session_manager.connect_session(resolved_service, resolved_tenant, force_refresh=True)
        return {"status": "ok", "message": f"Reconectado ao MCP {resolved_service.upper()} ({resolved_tenant})!"}
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e))


# 4.2 Rota por Serviço: /{service}/tools (Tenant resolvido via x-api-key ou header)
@app.get("/{service}/tools")
@app.get("/{service}/tools/")
async def list_tools_service(service: str, request: Request):
    if service in ("health", "oauth", "api"):
        raise HTTPException(status_code=404, detail="Not found")
    resolved_service, resolved_tenant = resolve_auth(request, target_service=service)
    return await list_tools_scoped(resolved_service, resolved_tenant, request)


@app.post("/{service}/tools/{tool_name}")
@app.post("/{service}/tools/{tool_name}/")
async def call_tool_service(service: str, tool_name: str, request: Request):
    if service in ("health", "oauth", "api"):
        raise HTTPException(status_code=404, detail="Not found")
    resolved_service, resolved_tenant = resolve_auth(request, target_service=service)
    return await call_tool_scoped(resolved_service, resolved_tenant, tool_name, request)


# 4.3 Rota Legada: /tools e /tools/{tool_name} (Compatível com setup anterior)
@app.get("/tools")
@app.get("/tools/")
async def list_tools_legacy(request: Request):
    resolved_service, resolved_tenant = resolve_auth(request)
    return await list_tools_scoped(resolved_service, resolved_tenant, request)


@app.post("/tools/{tool_name}")
@app.post("/tools/{tool_name}/")
async def call_tool_legacy(tool_name: str, request: Request):
    resolved_service, resolved_tenant = resolve_auth(request)
    return await call_tool_scoped(resolved_service, resolved_tenant, tool_name, request)


@app.post("/reconnect")
@app.post("/reconnect/")
async def reconnect_legacy(request: Request):
    resolved_service, resolved_tenant = resolve_auth(request)
    return await reconnect_scoped(resolved_service, resolved_tenant, request)
