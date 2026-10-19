import os
import json
import time
import secrets
import logging
from typing import Optional, Dict, Any, List

logger = logging.getLogger("mcp-proxy.storage")

DEFAULT_DATA_DIR = os.getenv("DATA_DIR", "/app/data")
if not os.path.isabs(DEFAULT_DATA_DIR):
    DEFAULT_DATA_DIR = os.path.abspath(DEFAULT_DATA_DIR)

INTERNAL_API_KEY = os.getenv("INTERNAL_API_KEY", "minha_chave_secreta_123")


class StorageManager:
    def __init__(self, data_dir: str = DEFAULT_DATA_DIR):
        self.data_dir = data_dir
        self.tokens_dir = os.path.join(self.data_dir, "tokens")
        self.tenants_file = os.path.join(self.data_dir, "tenants.json")
        self.legacy_tokens_file = os.path.join(self.data_dir, "tokens.json")

        os.makedirs(self.data_dir, exist_ok=True)
        os.makedirs(self.tokens_dir, exist_ok=True)
        self._ensure_default_tenant()
        self._migrate_legacy_tokens_if_needed()

    def _ensure_default_tenant(self):
        """Garante que o tenant 'default' exista no tenants.json."""
        tenants = self.get_all_tenants()
        if "default" not in tenants:
            tenants["default"] = {
                "id": "default",
                "name": "Conta Principal (Default)",
                "api_key": INTERNAL_API_KEY,
                "created_at": int(time.time()),
                "description": "Tenant padrão para Lovable e Uscreen"
            }
            self._save_tenants(tenants)
            logger.info("Tenant 'default' inicializado em tenants.json")

    def _migrate_legacy_tokens_if_needed(self):
        """Migra data/tokens.json legado para data/tokens/lovable_default.json se não existir."""
        target_lovable = self.get_token_path("lovable", "default")
        if os.path.exists(self.legacy_tokens_file) and not os.path.exists(target_lovable):
            try:
                with open(self.legacy_tokens_file, "r", encoding="utf-8") as f:
                    legacy_data = json.load(f)
                if legacy_data.get("access_token") or legacy_data.get("token"):
                    self.save_tokens("lovable", "default", legacy_data)
                    logger.info("Tokens legados de data/tokens.json migrados para lovable_default.json")
            except Exception as e:
                logger.warning(f"Erro ao migrar tokens legados: {e}")

    def get_all_tenants(self) -> Dict[str, Any]:
        if not os.path.exists(self.tenants_file):
            return {}
        try:
            with open(self.tenants_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("tenants", {})
        except Exception as e:
            logger.error(f"Erro ao ler tenants.json: {e}")
            return {}

    def _save_tenants(self, tenants: Dict[str, Any]):
        tmp_path = self.tenants_file + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump({"tenants": tenants}, f, indent=2)
        os.replace(tmp_path, self.tenants_file)

    def get_tenant(self, tenant_id: str) -> Optional[Dict[str, Any]]:
        tenants = self.get_all_tenants()
        return tenants.get(tenant_id)

    def create_or_update_tenant(
        self,
        tenant_id: str,
        name: str,
        api_key: Optional[str] = None,
        description: str = ""
    ) -> Dict[str, Any]:
        tenant_slug = tenant_id.strip().lower()
        if not tenant_slug or not tenant_slug.replace("_", "").replace("-", "").isalnum():
            raise ValueError("ID do tenant deve conter apenas letras, números, '-' ou '_'")

        tenants = self.get_all_tenants()
        existing = tenants.get(tenant_slug, {})

        key = api_key.strip() if api_key else existing.get("api_key")
        if not key:
            key = f"sk_{tenant_slug}_{secrets.token_urlsafe(18)}"

        tenant_info = {
            "id": tenant_slug,
            "name": name.strip() or f"Tenant {tenant_slug}",
            "api_key": key,
            "description": description.strip(),
            "created_at": existing.get("created_at", int(time.time())),
            "updated_at": int(time.time())
        }
        tenants[tenant_slug] = tenant_info
        self._save_tenants(tenants)
        return tenant_info

    def delete_tenant(self, tenant_id: str) -> bool:
        if tenant_id == "default":
            raise ValueError("Não é permitido excluir o tenant 'default'.")
        tenants = self.get_all_tenants()
        if tenant_id in tenants:
            del tenants[tenant_id]
            self._save_tenants(tenants)
            # Remove arquivos de token associados
            self.delete_tokens("lovable", tenant_id)
            self.delete_tokens("uscreen", tenant_id)
            return True
        return False

    def find_tenant_by_api_key(self, api_key: str) -> Optional[Dict[str, Any]]:
        if not api_key:
            return None
        # Se for a Master Key interna, concede acesso master
        if api_key == INTERNAL_API_KEY:
            return {"id": "*", "name": "Master Admin", "api_key": INTERNAL_API_KEY, "is_master": True}

        tenants = self.get_all_tenants()
        for t in tenants.values():
            if t.get("api_key") == api_key:
                return t
        return None

    def get_token_path(self, service: str, tenant_id: str) -> str:
        filename = f"{service.lower()}_{tenant_id.lower()}.json"
        return os.path.join(self.tokens_dir, filename)

    def load_tokens(self, service: str, tenant_id: str) -> Optional[Dict[str, Any]]:
        path = self.get_token_path(service, tenant_id)
        if not os.path.exists(path):
            # Fallback para o arquivo legado se for lovable + default
            if service.lower() == "lovable" and tenant_id.lower() == "default" and os.path.exists(self.legacy_tokens_file):
                path = self.legacy_tokens_file
            else:
                return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Erro ao ler tokens de {path}: {e}")
            return None

    def save_tokens(self, service: str, tenant_id: str, token_data: Dict[str, Any]):
        path = self.get_token_path(service, tenant_id)
        tmp_path = path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(token_data, f, indent=2)
        os.replace(tmp_path, path)

        # Se for lovable default, espelha em tokens.json para scripts legados
        if service.lower() == "lovable" and tenant_id.lower() == "default":
            try:
                legacy_tmp = self.legacy_tokens_file + ".tmp"
                with open(legacy_tmp, "w", encoding="utf-8") as f:
                    json.dump(token_data, f, indent=2)
                os.replace(legacy_tmp, self.legacy_tokens_file)
            except Exception:
                pass

    def delete_tokens(self, service: str, tenant_id: str) -> bool:
        path = self.get_token_path(service, tenant_id)
        if os.path.exists(path):
            try:
                os.remove(path)
                return True
            except Exception as e:
                logger.warning(f"Erro ao remover {path}: {e}")
        return False

    def list_all_token_records(self) -> List[Dict[str, Any]]:
        """Lista o status de tokens de todos os pares (serviço, tenant) existentes."""
        records = []
        tenants = self.get_all_tenants()
        services = ["lovable", "uscreen"]

        for t_id, t_info in tenants.items():
            for s in services:
                tokens = self.load_tokens(s, t_id)
                has_tokens = tokens is not None and bool(tokens.get("access_token") or tokens.get("token"))
                expires_at = tokens.get("expires_at") if tokens else None
                expires_in = int(expires_at - time.time()) if expires_at else None
                has_refresh = bool(tokens.get("refresh_token")) if tokens else False

                records.append({
                    "service": s,
                    "tenant_id": t_id,
                    "tenant_name": t_info.get("name", t_id),
                    "connected": has_tokens,
                    "has_refresh_token": has_refresh,
                    "expires_at": expires_at,
                    "expires_in_seconds": expires_in,
                    "is_expired": (expires_in is not None and expires_in <= 0) if has_tokens else None
                })
        return records
