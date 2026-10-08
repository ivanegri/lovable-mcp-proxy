import os
import json
import time
import base64
import logging
import urllib.parse
import urllib.request
from contextlib import asynccontextmanager, AsyncExitStack
from typing import Optional, Dict, Any

from fastapi import FastAPI, HTTPException, Request, Header, status
from mcp import ClientSession
from mcp.client.sse import sse_client

# Configuração de Logs
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("lovable-mcp-proxy")

# Configurações via Variáveis de Ambiente
TOKEN_FILE = os.getenv("TOKEN_FILE_PATH", "/app/data/tokens.json")
CLIENT_FILE = os.getenv("CLIENT_FILE_PATH", "/app/data/oauth_client.json")
INTERNAL_API_KEY = os.getenv("INTERNAL_API_KEY", "minha_chave_secreta_123")
LOVABLE_SSE_URL = os.getenv("LOVABLE_SSE_URL", "https://mcp.lovable.dev/sse")
TOKEN_ENDPOINT = os.getenv("TOKEN_ENDPOINT", "https://lovable.dev/oauth/token")

mcp_session: Optional[ClientSession] = None
exit_stack: Optional[AsyncExitStack] = None


def try_refresh_token() -> Optional[str]:
    """Tenta renovar o access_token usando o refresh_token se disponível."""
    if not os.path.exists(TOKEN_FILE) or not os.path.exists(CLIENT_FILE):
        return None
    try:
        with open(TOKEN_FILE, "r", encoding="utf-8") as f:
            token_data = json.load(f)
        refresh_token = token_data.get("refresh_token")
        if not refresh_token:
            return None

        with open(CLIENT_FILE, "r", encoding="utf-8") as f:
            client_data = json.load(f)
        client_id = client_data.get("client_id")
        client_secret = client_data.get("client_secret")
        if not client_id or not client_secret:
            return None

        logger.info("🔄 Renovando access_token do Lovable via refresh_token...")
        data = urllib.parse.urlencode({
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret
        }).encode("utf-8")

        creds = f"{client_id}:{client_secret}".encode("utf-8")
        auth_header = "Basic " + base64.b64encode(creds).decode("utf-8")
        req = urllib.request.Request(
            TOKEN_ENDPOINT,
            data=data,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Authorization": auth_header,
                "User-Agent": "Lovable-MCP-Proxy"
            }
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            new_tokens = json.load(resp)

        new_access = new_tokens.get("access_token")
        if not new_access:
            return None

        token_data["access_token"] = new_access
        if "refresh_token" in new_tokens:
            token_data["refresh_token"] = new_tokens["refresh_token"]
        if "expires_in" in new_tokens:
            token_data["expires_at"] = int(time.time()) + new_tokens["expires_in"]

        with open(TOKEN_FILE, "w", encoding="utf-8") as f:
            json.dump(token_data, f, indent=2)

        logger.info("✅ Token renovado e persistido com sucesso via refresh_token!")
        return new_access
    except Exception as e:
        logger.error(f"⚠️ Falha ao renovar token com refresh_token: {e}")
        return None


def load_token(force_refresh: bool = False) -> str:
    """Carrega o token OAuth a partir do arquivo persistido, renovando automaticamente se expirado."""
    if not os.path.exists(TOKEN_FILE):
        raise RuntimeError(f"Arquivo de token não encontrado em: {TOKEN_FILE}")
    with open(TOKEN_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Se solicitado ou se o token estiver próximo de expirar (margem de 2 minutos)
    expires_at = data.get("expires_at")
    if force_refresh or (expires_at and time.time() > (expires_at - 120)):
        refreshed = try_refresh_token()
        if refreshed:
            return refreshed

    token = data.get("access_token") or data.get("token")
    if not token:
        raise RuntimeError(f"Chave 'access_token' não encontrada no arquivo {TOKEN_FILE}")
    return token


def verify_internal_auth(x_api_key: Optional[str]):
    """Protege o proxy de requisições não autorizadas na rede/VPN."""
    if not x_api_key or x_api_key != INTERNAL_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API Key Interna Inválida ou ausente (envie o header 'x-api-key')"
        )


async def connect_mcp(retry_on_auth_failure: bool = True):
    """Inicializa ou reinicializa a conexão SSE e a sessão MCP com o Lovable."""
    global mcp_session, exit_stack
    token = load_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    # Fecha conexão prévia se houver
    if exit_stack is not None:
        try:
            await exit_stack.aclose()
        except Exception as e:
            logger.warning(f"Erro ao fechar conexão MCP anterior: {e}")

    stack = AsyncExitStack()
    try:
        # sse_client retorna (read_stream, write_stream)
        streams = await stack.enter_async_context(
            sse_client(url=LOVABLE_SSE_URL, headers=headers)
        )
        read_stream, write_stream = streams

        # Inicializa a sessão MCP com os streams
        session = await stack.enter_async_context(
            ClientSession(read_stream, write_stream)
        )
        await session.initialize()

        mcp_session = session
        exit_stack = stack
        logger.info("✅ Conectado ao Lovable MCP com sucesso!")
    except Exception as e:
        await stack.aclose()
        mcp_session = None
        exit_stack = None

        # Se falhou por 401 e temos refresh_token, tenta renovar e reconectar uma vez
        if retry_on_auth_failure and ("401" in str(e) or "Unauthorized" in str(e)):
            logger.warning("Falha de autenticação (401). Tentando renovar com refresh_token...")
            if try_refresh_token():
                return await connect_mcp(retry_on_auth_failure=False)

        logger.error(f"❌ Erro de conexão com Lovable MCP: {e}")
        raise e


async def ensure_mcp_session() -> ClientSession:
    """Garante que a sessão MCP está ativa antes de executar chamadas."""
    global mcp_session
    if mcp_session is None:
        logger.info("Sessão MCP não inicializada. Tentando conectar sob demanda...")
        await connect_mcp()
    return mcp_session


@asynccontextmanager
async def lifespan(app: FastAPI):
    global exit_stack
    try:
        await connect_mcp()
    except Exception as e:
        logger.warning(
            f"⚠️ Não foi possível conectar ao Lovable MCP na inicialização: {e}\n"
            "O servidor continuará ativo. Execute 'python3 auth_login.py' "
            "ou insira o token em 'data/tokens.json' e chame /reconnect."
        )
    yield
    if exit_stack is not None:
        await exit_stack.aclose()
        logger.info("Conexão MCP finalizada com sucesso.")


app = FastAPI(
    title="Lovable MCP Proxy Bridge",
    description="Proxy REST seguro para integração com o servidor Lovable MCP via SSE",
    version="1.1.0",
    lifespan=lifespan
)


@app.get("/health")
async def health_check():
    """Verifica a integridade do proxy e a conexão com o Lovable MCP."""
    is_connected = mcp_session is not None
    token_exists = os.path.exists(TOKEN_FILE)
    has_refresh = False
    if token_exists:
        try:
            with open(TOKEN_FILE, "r") as f:
                has_refresh = bool(json.load(f).get("refresh_token"))
        except Exception:
            pass

    return {
        "status": "healthy" if is_connected else "degraded",
        "mcp_connected": is_connected,
        "token_file_present": token_exists,
        "auto_refresh_enabled": has_refresh
    }


@app.post("/reconnect")
async def reconnect(x_api_key: Optional[str] = Header(None, alias="x-api-key")):
    """Força reconexão com o Lovable MCP (útil após atualizar o tokens.json)."""
    verify_internal_auth(x_api_key)
    try:
        await connect_mcp()
        return {"status": "ok", "message": "Reconectado com sucesso ao Lovable MCP!"}
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Falha ao conectar com o Lovable MCP: {str(e)}"
        )


@app.get("/tools")
async def list_tools(x_api_key: Optional[str] = Header(None, alias="x-api-key")):
    """Lista todas as ferramentas disponíveis no servidor MCP do Lovable."""
    verify_internal_auth(x_api_key)
    session = await ensure_mcp_session()
    try:
        response = await session.list_tools()
        return {"tools": [t.model_dump() for t in response.tools]}
    except Exception as e:
        logger.error(f"Erro ao listar ferramentas: {e}")
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e))


@app.post("/tools/{tool_name}")
async def call_tool(
    tool_name: str,
    request: Request,
    x_api_key: Optional[str] = Header(None, alias="x-api-key")
):
    """Executa uma ferramenta específica no Lovable MCP."""
    verify_internal_auth(x_api_key)
    session = await ensure_mcp_session()

    try:
        arguments = await request.json()
    except Exception:
        arguments = {}

    if not isinstance(arguments, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="O corpo da requisição deve ser um objeto JSON contendo os argumentos."
        )

    try:
        result = await session.call_tool(tool_name, arguments)
        return {"result": result.model_dump()}
    except Exception as e:
        logger.error(f"Erro ao executar a ferramenta '{tool_name}': {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))
