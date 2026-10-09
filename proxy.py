import os
import json
import time
import base64
import asyncio
import logging
import threading
import urllib.error
import urllib.parse
import urllib.request
from contextlib import asynccontextmanager, AsyncExitStack
from typing import Optional, Dict, Any, Callable, Awaitable

from fastapi import FastAPI, HTTPException, Request, Header, status
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client, create_mcp_http_client
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
LOVABLE_RAW_URL = os.getenv("LOVABLE_MCP_URL") or os.getenv("LOVABLE_SSE_URL") or "https://mcp.lovable.dev"

# Remove '/sse' caso tenha sido herdado de configuração antiga
if LOVABLE_RAW_URL.endswith("/sse"):
    LOVABLE_MCP_URL = LOVABLE_RAW_URL[:-4]
else:
    LOVABLE_MCP_URL = LOVABLE_RAW_URL

TOKEN_ENDPOINT = os.getenv("TOKEN_ENDPOINT", "https://lovable.dev/oauth/token")
# Renova o token quando faltar menos que isso para expirar (segundos)
REFRESH_MARGIN = int(os.getenv("REFRESH_MARGIN_SECONDS", "300"))
# Intervalo máximo entre verificações do loop de renovação (segundos)
REFRESH_CHECK_INTERVAL = int(os.getenv("REFRESH_CHECK_INTERVAL_SECONDS", "60"))

AUTH_ERROR_TERMS = ("401", "Unauthorized", "Not authenticated", "invalid_token")

mcp_session: Optional[ClientSession] = None
exit_stack: Optional[AsyncExitStack] = None
current_session_token: Optional[str] = None

_refresh_thread_lock = threading.Lock()
_connect_lock: Optional[asyncio.Lock] = None
_refresh_task: Optional[asyncio.Task] = None


def is_auth_error(e: BaseException) -> bool:
    msg = f"{type(e).__name__}: {e}"
    return any(term in msg for term in AUTH_ERROR_TERMS)


def read_token_data() -> Dict[str, Any]:
    with open(TOKEN_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def try_refresh_token(failed_token: Optional[str] = None) -> Optional[str]:
    """Tenta renovar o access_token usando o refresh_token se disponível.

    Se `failed_token` for informado e o arquivo já contiver um access_token diferente
    (outra requisição já renovou), retorna o token atual sem chamar a API novamente.
    Isso evita queimar um refresh_token rotativo com chamadas concorrentes.
    """
    with _refresh_thread_lock:
        return _try_refresh_token_locked(failed_token)


def _try_refresh_token_locked(failed_token: Optional[str]) -> Optional[str]:
    if not os.path.exists(TOKEN_FILE) or not os.path.exists(CLIENT_FILE):
        logger.error(f"⚠️ Não é possível renovar: {TOKEN_FILE} ou {CLIENT_FILE} ausente.")
        return None
    try:
        if failed_token:
            current = read_token_data()
            current_access = current.get("access_token")
            expires_at = current.get("expires_at") or 0
            if current_access and current_access != failed_token and time.time() < expires_at - REFRESH_MARGIN:
                logger.info("Token já foi renovado por outra requisição; reutilizando.")
                return current_access

        with open(TOKEN_FILE, "r", encoding="utf-8") as f:
            token_data = json.load(f)
        refresh_token = token_data.get("refresh_token")
        if not refresh_token:
            logger.error("⚠️ Não há refresh_token em tokens.json. Rode 'python3 auth_login.py' novamente.")
            return None

        with open(CLIENT_FILE, "r", encoding="utf-8") as f:
            client_data = json.load(f)
        client_id = client_data.get("client_id")
        client_secret = client_data.get("client_secret")
        if not client_id or not client_secret:
            logger.error("⚠️ client_id/client_secret ausentes em oauth_client.json.")
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
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                new_tokens = json.load(resp)
        except urllib.error.HTTPError as he:
            body = ""
            try:
                body = he.read().decode("utf-8", errors="replace")[:500]
            except Exception:
                pass
            logger.error(f"⚠️ Lovable recusou o refresh ({he.code}): {body}")
            if "invalid_grant" in body:
                logger.error(
                    "❌ refresh_token inválido/expirado/revogado. "
                    "É necessário rodar 'python3 auth_login.py' novamente na VPS."
                )
            return None

        new_access = new_tokens.get("access_token")
        if not new_access:
            logger.error(f"⚠️ Resposta de refresh sem access_token: {list(new_tokens.keys())}")
            return None

        token_data["access_token"] = new_access
        if new_tokens.get("refresh_token"):
            token_data["refresh_token"] = new_tokens["refresh_token"]
        token_data["expires_at"] = int(time.time()) + int(new_tokens.get("expires_in", 3600))
        token_data["refreshed_at"] = int(time.time())

        # Escrita atômica para não corromper o arquivo (e o refresh_token rotativo)
        tmp_path = TOKEN_FILE + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(token_data, f, indent=2)
        os.replace(tmp_path, TOKEN_FILE)

        logger.info("✅ Token renovado e persistido com sucesso via refresh_token!")
        return new_access
    except Exception as e:
        logger.error(f"⚠️ Falha ao renovar token com refresh_token: {e}")
        return None


def load_token(force_refresh: bool = False) -> str:
    """Carrega o token OAuth a partir do arquivo persistido, renovando automaticamente se expirado."""
    if not os.path.exists(TOKEN_FILE):
        raise RuntimeError(f"Arquivo de token não encontrado em: {TOKEN_FILE}")
    data = read_token_data()

    # Se solicitado ou se o token estiver próximo de expirar
    expires_at = data.get("expires_at")
    if force_refresh or (expires_at and time.time() > (expires_at - REFRESH_MARGIN)):
        refreshed = try_refresh_token(data.get("access_token") if force_refresh else None)
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


def get_connect_lock() -> asyncio.Lock:
    global _connect_lock
    if _connect_lock is None:
        _connect_lock = asyncio.Lock()
    return _connect_lock


async def connect_mcp(retry_on_auth_failure: bool = True, force_refresh: bool = False):
    """Inicializa ou reinicializa a conexão MCP (serializado por lock)."""
    async with get_connect_lock():
        await _connect_mcp_locked(retry_on_auth_failure, force_refresh)


async def _connect_mcp_locked(retry_on_auth_failure: bool = True, force_refresh: bool = False):
    """Inicializa ou reinicializa a conexão MCP com o Lovable usando Streamable HTTP."""
    global mcp_session, exit_stack, current_session_token
    token = await asyncio.to_thread(load_token, force_refresh)
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    # Fecha conexão prévia se houver
    if exit_stack is not None:
        old_stack = exit_stack
        mcp_session = None
        exit_stack = None
        try:
            await old_stack.aclose()
        except BaseException as e:  # anyio pode lançar CancelledError/ExceptionGroup
            logger.warning(f"Erro ao fechar conexão MCP anterior: {e!r}")

    stack = AsyncExitStack()
    try:
        logger.info(f"Conectando ao Lovable MCP em: {LOVABLE_MCP_URL}")
        http_client = create_mcp_http_client(headers=headers)
        
        streams = await stack.enter_async_context(
            streamable_http_client(url=LOVABLE_MCP_URL, http_client=http_client)
        )
        read_stream, write_stream = streams

        # Inicializa a sessão MCP com os streams
        session = await stack.enter_async_context(
            ClientSession(read_stream, write_stream)
        )
        await session.initialize()

        mcp_session = session
        exit_stack = stack
        current_session_token = token
        logger.info("✅ Conectado ao Lovable MCP com sucesso!")
    except Exception as e:
        try:
            await stack.aclose()
        except BaseException:
            pass
        mcp_session = None
        exit_stack = None

        # Se falhou por não autenticado (401) e temos refresh_token, tenta renovar e reconectar uma vez
        if retry_on_auth_failure and is_auth_error(e):
            logger.warning("Falha de autenticação no Lovable. Tentando renovar com refresh_token...")
            if await asyncio.to_thread(try_refresh_token, token):
                return await _connect_mcp_locked(retry_on_auth_failure=False)

        logger.error(f"❌ Erro de conexão com Lovable MCP: {e}")
        raise e


async def ensure_mcp_session() -> ClientSession:
    """Garante que a sessão MCP está ativa e com token válido antes de executar chamadas."""
    if mcp_session is None:
        logger.info("Sessão MCP não inicializada. Tentando conectar sob demanda...")
        await connect_mcp()
    elif token_needs_refresh():
        logger.info("Token da sessão próximo de expirar. Renovando e reconectando...")
        await connect_mcp()
    return mcp_session


def token_needs_refresh() -> bool:
    """True se o token em uso expirou/vai expirar ou se o arquivo tem um token diferente do da sessão."""
    try:
        data = read_token_data()
    except Exception:
        return False
    expires_at = data.get("expires_at")
    if expires_at and time.time() > expires_at - REFRESH_MARGIN:
        return True
    # tokens.json foi atualizado externamente (ex.: auth_login.py) -> reconecta com o novo
    file_token = data.get("access_token") or data.get("token")
    return bool(current_session_token and file_token and file_token != current_session_token)


async def run_with_session(
    op: Callable[[ClientSession], Awaitable[Any]],
    retry_any_error: bool = False
) -> Any:
    """Executa uma operação MCP; em caso de 401 renova token, reconecta e tenta de novo.

    Com `retry_any_error=True` (só para operações idempotentes) também reconecta em outros erros.
    """
    session = await ensure_mcp_session()
    try:
        return await op(session)
    except Exception as e:
        if is_auth_error(e):
            logger.warning(f"Erro de autenticação na chamada MCP ({e}). Renovando token e reconectando...")
            await connect_mcp(force_refresh=True)
        elif not retry_any_error:
            raise
        else:
            logger.warning(f"Falha na chamada MCP ({e!r}). Reconectando e tentando novamente...")
            await connect_mcp()
        return await op(mcp_session)


async def token_refresh_loop():
    """Loop em background que renova o token antes de expirar e reconecta a sessão."""
    logger.info("⏱️ Loop de renovação automática de token iniciado.")
    while True:
        sleep_for = REFRESH_CHECK_INTERVAL
        try:
            if os.path.exists(TOKEN_FILE):
                if token_needs_refresh():
                    logger.info("⏱️ Renovação proativa do token...")
                    await connect_mcp()
                else:
                    expires_at = read_token_data().get("expires_at")
                    if expires_at:
                        remaining = expires_at - REFRESH_MARGIN - time.time()
                        sleep_for = max(5, min(REFRESH_CHECK_INTERVAL, remaining))
                    if mcp_session is None:
                        await connect_mcp()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"⚠️ Erro no loop de renovação de token: {e}")
        await asyncio.sleep(sleep_for)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global exit_stack, _refresh_task
    try:
        await connect_mcp()
    except Exception as e:
        logger.warning(
            f"⚠️ Não foi possível conectar ao Lovable MCP na inicialização: {e}\n"
            "O servidor continuará ativo. Execute 'python3 auth_login.py' "
            "ou insira o token em 'data/tokens.json' e chame /reconnect."
        )
    _refresh_task = asyncio.create_task(token_refresh_loop())
    yield
    if _refresh_task is not None:
        _refresh_task.cancel()
        try:
            await _refresh_task
        except BaseException:
            pass
    if exit_stack is not None:
        await exit_stack.aclose()
        logger.info("Conexão MCP finalizada com sucesso.")


app = FastAPI(
    title="Lovable MCP Proxy Bridge",
    description="Proxy REST seguro para integração com o servidor Lovable MCP",
    version="1.2.0",
    lifespan=lifespan
)


@app.get("/")
async def root():
    """Endpoint raiz para verificação rápida de status do proxy."""
    return {
        "service": "Lovable MCP Proxy Bridge",
        "status": "online",
        "mcp_connected": mcp_session is not None,
        "endpoints": {
            "health": "/health",
            "tools": "/tools",
            "call_tool": "/tools/{tool_name}"
        }
    }


@app.get("/health")
@app.get("/health/")
async def health_check():
    """Verifica a integridade do proxy e a conexão com o Lovable MCP."""
    is_connected = mcp_session is not None
    token_exists = os.path.exists(TOKEN_FILE)
    has_refresh = False
    expires_in = None
    if token_exists:
        try:
            data = read_token_data()
            has_refresh = bool(data.get("refresh_token"))
            if data.get("expires_at"):
                expires_in = int(data["expires_at"] - time.time())
        except Exception:
            pass

    return {
        "status": "healthy" if is_connected else "degraded",
        "mcp_connected": is_connected,
        "token_file_present": token_exists,
        "auto_refresh_enabled": has_refresh,
        "token_expires_in_seconds": expires_in,
        "refresh_loop_running": _refresh_task is not None and not _refresh_task.done()
    }


@app.post("/reconnect")
@app.post("/reconnect/")
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
@app.get("/tools/")
async def list_tools(x_api_key: Optional[str] = Header(None, alias="x-api-key")):
    """Lista todas as ferramentas disponíveis no servidor MCP do Lovable."""
    verify_internal_auth(x_api_key)
    try:
        response = await run_with_session(lambda s: s.list_tools(), retry_any_error=True)
        return {"tools": [t.model_dump() for t in response.tools]}
    except Exception as e:
        logger.error(f"Erro ao listar ferramentas: {e}")
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e))


@app.post("/tools/{tool_name}")
@app.post("/tools/{tool_name}/")
async def call_tool(
    tool_name: str,
    request: Request,
    x_api_key: Optional[str] = Header(None, alias="x-api-key")
):
    """Executa uma ferramenta específica no Lovable MCP."""
    verify_internal_auth(x_api_key)

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
        result = await run_with_session(lambda s: s.call_tool(tool_name, arguments))
        return {"result": result.model_dump()}
    except Exception as e:
        logger.error(f"Erro ao executar a ferramenta '{tool_name}': {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))
