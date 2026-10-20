import os
import time
import asyncio
import logging
from typing import Optional, Dict, Any, Tuple, Callable, Awaitable
from contextlib import AsyncExitStack

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client, create_mcp_http_client
from mcp.client.sse import sse_client

from providers import get_provider, refresh_access_token, list_known_clients
from storage import StorageManager

logger = logging.getLogger("mcp-proxy.sessions")

REFRESH_MARGIN = int(os.getenv("REFRESH_MARGIN_SECONDS", "300"))
REFRESH_CHECK_INTERVAL = int(os.getenv("REFRESH_CHECK_INTERVAL_SECONDS", "60"))
AUTH_ERROR_TERMS = ("401", "Unauthorized", "Not authenticated", "invalid_token", "invalid_grant")


def is_auth_error(e: BaseException) -> bool:
    msg = f"{type(e).__name__}: {e}"
    return any(term in msg for term in AUTH_ERROR_TERMS)


class TenantSessionInfo:
    def __init__(self, service: str, tenant_id: str):
        self.service = service
        self.tenant_id = tenant_id
        self.session: Optional[ClientSession] = None
        self.exit_stack: Optional[AsyncExitStack] = None
        self.current_token: Optional[str] = None
        self.lock = asyncio.Lock()
        self.last_connected_at: Optional[float] = None
        self.last_error: Optional[str] = None


class SessionManager:
    def __init__(self, storage: StorageManager):
        self.storage = storage
        self._sessions: Dict[Tuple[str, str], TenantSessionInfo] = {}
        self._global_lock = asyncio.Lock()
        self._refresh_task: Optional[asyncio.Task] = None

    async def get_session_info(self, service: str, tenant_id: str) -> TenantSessionInfo:
        key = (service.lower(), tenant_id.lower())
        async with self._global_lock:
            if key not in self._sessions:
                self._sessions[key] = TenantSessionInfo(service=service.lower(), tenant_id=tenant_id.lower())
            return self._sessions[key]

    async def try_refresh_token(self, service: str, tenant_id: str, failed_token: Optional[str] = None) -> Optional[str]:
        """Tenta renovar o token OAuth do tenant especificado."""
        provider = get_provider(service)
        tokens = self.storage.load_tokens(service, tenant_id)
        if not tokens:
            logger.warning(f"Tokens ausentes para ({service}, {tenant_id})")
            return None

        current_access = tokens.get("access_token") or tokens.get("token")
        expires_at = tokens.get("expires_at") or 0
        refresh_token_str = tokens.get("refresh_token")

        # Se outro processo ou chamada concorrente já renovou
        if failed_token and current_access and current_access != failed_token and time.time() < expires_at - REFRESH_MARGIN:
            logger.info(f"Token ({service}, {tenant_id}) já renovado por outra requisição; reutilizando.")
            return current_access

        if not refresh_token_str:
            logger.error(f"⚠️ Sem refresh_token para ({service}, {tenant_id}). Reautenticação necessária.")
            return None

        # O refresh precisa do MESMO client_id que emitiu o token.
        # 1) client gravado junto do token (logins novos)  2) clientes conhecidos (tokens antigos)
        candidates = []
        if tokens.get("client_id"):
            candidates.append((tokens["client_id"], tokens.get("client_secret")))
        for c in list_known_clients(provider, self.storage.data_dir):
            if c not in candidates:
                candidates.append(c)
        if not candidates:
            logger.error(f"⚠️ Nenhum client OAuth conhecido para ({service}, {tenant_id}). Reconecte pelo painel.")
            return None

        logger.info(f"🔄 Renovando access_token de ({service}, {tenant_id}) via refresh_token...")
        new_tokens = None
        last_error = None
        for client_id, client_secret in candidates:
            try:
                new_tokens = await asyncio.to_thread(
                    refresh_access_token, provider, client_id, client_secret, refresh_token_str
                )
                tokens["client_id"] = client_id
                tokens["client_secret"] = client_secret
                break
            except Exception as e:
                last_error = e
                logger.warning(f"Refresh com client {client_id[:12]}... falhou: {e}")
        if new_tokens is None:
            logger.error(f"⚠️ Falha na renovação de token para ({service}, {tenant_id}): {last_error}")
            return None

        new_access = new_tokens.get("access_token")
        if not new_access:
            logger.error(f"Resposta de renovação sem access_token: {new_tokens}")
            return None

        tokens["access_token"] = new_access
        if new_tokens.get("refresh_token"):
            tokens["refresh_token"] = new_tokens["refresh_token"]
        tokens["expires_at"] = int(time.time()) + int(new_tokens.get("expires_in", 3600))
        tokens["refreshed_at"] = int(time.time())

        self.storage.save_tokens(service, tenant_id, tokens)
        logger.info(f"✅ Token de ({service}, {tenant_id}) renovado e salvo com sucesso!")
        return new_access

    def token_needs_refresh(self, service: str, tenant_id: str, current_token: Optional[str]) -> bool:
        tokens = self.storage.load_tokens(service, tenant_id)
        if not tokens:
            return False
        expires_at = tokens.get("expires_at")
        if expires_at and time.time() > (expires_at - REFRESH_MARGIN):
            return True
        file_token = tokens.get("access_token") or tokens.get("token")
        return bool(current_token and file_token and file_token != current_token)

    async def connect_session(self, service: str, tenant_id: str, force_refresh: bool = False) -> ClientSession:
        """Conecta ou reconecta a sessão MCP do tenant de forma thread-safe."""
        info = await self.get_session_info(service, tenant_id)
        async with info.lock:
            return await self._connect_locked(info, force_refresh)

    async def _connect_locked(self, info: TenantSessionInfo, force_refresh: bool = False) -> ClientSession:
        service = info.service
        tenant_id = info.tenant_id
        provider = get_provider(service)

        tokens = self.storage.load_tokens(service, tenant_id)
        if not tokens:
            raise RuntimeError(
                f"Conta não conectada. Acesse o painel ou execute a autenticação para o serviço '{service}' e tenant '{tenant_id}'."
            )

        token = tokens.get("access_token") or tokens.get("token")
        expires_at = tokens.get("expires_at")

        if force_refresh or (expires_at and time.time() > (expires_at - REFRESH_MARGIN)):
            refreshed = await self.try_refresh_token(service, tenant_id, token if force_refresh else None)
            if refreshed:
                token = refreshed

        if not token:
            raise RuntimeError(f"Nenhum access_token disponível para ({service}, {tenant_id}).")

        # Fecha sessão anterior se existir
        if info.exit_stack is not None:
            old_stack = info.exit_stack
            info.session = None
            info.exit_stack = None
            try:
                await old_stack.aclose()
            except BaseException as e:
                logger.warning(f"Erro ao fechar sessão anterior ({service}, {tenant_id}): {e!r}")

        stack = AsyncExitStack()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }

        try:
            logger.info(f"Conectando ao MCP {provider.name} ({service}, {tenant_id}) em: {provider.mcp_url}")
            http_client = create_mcp_http_client(headers=headers)

            try:
                # Tenta Streamable HTTP primeiro
                streams = await stack.enter_async_context(
                    streamable_http_client(url=provider.mcp_url, http_client=http_client)
                )
            except Exception as stream_err:
                logger.info(f"Streamable HTTP falhou para {provider.name} ({stream_err}), tentando conexão SSE...")
                streams = await stack.enter_async_context(
                    sse_client(url=provider.mcp_url, headers=headers)
                )

            read_stream, write_stream = streams
            session = await stack.enter_async_context(
                ClientSession(read_stream, write_stream)
            )
            await session.initialize()

            info.session = session
            info.exit_stack = stack
            info.current_token = token
            info.last_connected_at = time.time()
            info.last_error = None
            logger.info(f"✅ Conectado com sucesso ao MCP {provider.name} para o tenant '{tenant_id}'!")
            return session

        except Exception as e:
            try:
                await stack.aclose()
            except BaseException:
                pass
            info.session = None
            info.exit_stack = None
            info.last_error = str(e)

            if is_auth_error(e) and not force_refresh:
                logger.warning(f"Falha de autenticação ao conectar ({service}, {tenant_id}). Tentando renovar...")
                if await self.try_refresh_token(service, tenant_id, token):
                    return await self._connect_locked(info, force_refresh=True)

            logger.error(f"❌ Erro ao conectar ao MCP {provider.name} ({tenant_id}): {e}")
            raise e

    async def ensure_session(self, service: str, tenant_id: str) -> ClientSession:
        info = await self.get_session_info(service, tenant_id)
        if info.session is None or self.token_needs_refresh(service, tenant_id, info.current_token):
            return await self.connect_session(service, tenant_id)
        return info.session

    async def run_with_session(
        self,
        service: str,
        tenant_id: str,
        op: Callable[[ClientSession], Awaitable[Any]],
        retry_any_error: bool = False
    ) -> Any:
        session = await self.ensure_session(service, tenant_id)
        try:
            return await op(session)
        except Exception as e:
            if is_auth_error(e):
                logger.warning(f"Erro de autenticação na chamada ({service}, {tenant_id}): {e}. Renovando...")
                session = await self.connect_session(service, tenant_id, force_refresh=True)
                return await op(session)
            elif retry_any_error:
                logger.warning(f"Erro idempotente em ({service}, {tenant_id}): {e!r}. Reconectando e tentando novamente...")
                session = await self.connect_session(service, tenant_id)
                return await op(session)
            else:
                raise

    async def token_refresh_loop(self):
        """Loop contínuo em segundo plano para renovação proativa de tokens de todos os tenants."""
        logger.info("⏱️ Loop de renovação automática multi-tenant iniciado.")
        while True:
            try:
                records = self.storage.list_all_token_records()
                for rec in records:
                    if not rec.get("connected"):
                        continue
                    s = rec["service"]
                    t = rec["tenant_id"]
                    info = await self.get_session_info(s, t)
                    if self.token_needs_refresh(s, t, info.current_token):
                        logger.info(f"⏱️ Renovação proativa do token para ({s}, {t})...")
                        try:
                            await self.connect_session(s, t)
                        except Exception as ce:
                            logger.error(f"Erro na renovação proativa ({s}, {t}): {ce}")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Erro no ciclo do refresh loop: {e}")
            await asyncio.sleep(REFRESH_CHECK_INTERVAL)

    def start_background_task(self) -> asyncio.Task:
        if self._refresh_task is None or self._refresh_task.done():
            self._refresh_task = asyncio.create_task(self.token_refresh_loop())
        return self._refresh_task

    async def close_all(self):
        if self._refresh_task is not None:
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except BaseException:
                pass

        async with self._global_lock:
            for key, info in self._sessions.items():
                if info.exit_stack is not None:
                    try:
                        await info.exit_stack.aclose()
                        logger.info(f"Sessão MCP ({key[0]}, {key[1]}) encerrada.")
                    except BaseException as e:
                        logger.warning(f"Erro ao encerrar sessão {key}: {e}")
            self._sessions.clear()
