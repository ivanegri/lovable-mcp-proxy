"""
Cliente e Integração com Meta Graph API v21.0.
Suporta consultas de campanhas, conjuntos de anúncios, anúncios, insights de métricas,
gerenciamento de status (pausar/ativar anúncios) e hidratação de webhooks (leads e alertas).
"""

import logging
from typing import Optional, Dict, Any, List
import httpx

logger = logging.getLogger("mcp-proxy.meta_client")

GRAPH_API_VERSION = "v21.0"
GRAPH_API_BASE = f"https://graph.facebook.com/{GRAPH_API_VERSION}"


class MetaGraphClient:
    def __init__(self, access_token: str):
        self.access_token = access_token
        self.base_url = GRAPH_API_BASE
        self.client = httpx.AsyncClient(timeout=30.0)

    async def close(self):
        await self.client.aclose()

    def _auth_params(self, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        p = dict(params or {})
        p["access_token"] = self.access_token
        return p

    async def get_ad_accounts(self) -> List[Dict[str, Any]]:
        """Lista as contas de anúncio acessíveis pelo token do usuário."""
        url = f"{self.base_url}/me/adaccounts"
        params = self._auth_params({
            "fields": "id,account_id,name,account_status,currency,spend_cap,amount_spent,business_name"
        })
        resp = await self.client.get(url, params=params)
        data = resp.json()
        if "error" in data:
            raise RuntimeError(f"Erro Meta Graph API: {data['error'].get('message')}")
        return data.get("data", [])

    async def get_campaign_insights(
        self,
        ad_account_id: str,
        date_preset: str = "last_7d",
        level: str = "campaign",
        fields: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """Consulta métricas consolidadas (spend, cpc, cpm, ctr, impressions, clicks, conversions) da conta de anúncio."""
        acc_id = ad_account_id if ad_account_id.startswith("act_") else f"act_{ad_account_id}"
        url = f"{self.base_url}/{acc_id}/insights"

        default_fields = [
            "campaign_id", "campaign_name", "adset_id", "adset_name", "ad_id", "ad_name",
            "spend", "impressions", "clicks", "cpc", "cpm", "ctr", "conversions", "actions", "cost_per_action_type"
        ]
        fields_str = ",".join(fields or default_fields)

        params = self._auth_params({
            "date_preset": date_preset,
            "level": level,
            "fields": fields_str,
            "limit": 100
        })

        resp = await self.client.get(url, params=params)
        data = resp.json()
        if "error" in data:
            raise RuntimeError(f"Erro Meta Graph API ({data['error'].get('type')}): {data['error'].get('message')}")
        return data.get("data", [])

    async def get_campaigns(
        self,
        ad_account_id: str,
        effective_status: Optional[List[str]] = None,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Lista as campanhas da conta com status, objetivo, orçamento e performance recente."""
        acc_id = ad_account_id if ad_account_id.startswith("act_") else f"act_{ad_account_id}"
        url = f"{self.base_url}/{acc_id}/campaigns"

        params = {
            "fields": "id,name,status,effective_status,objective,daily_budget,lifetime_budget,start_time,stop_time,created_time",
            "limit": limit
        }
        if effective_status:
            params["effective_status"] = str(effective_status)

        resp = await self.client.get(url, params=self._auth_params(params))
        data = resp.json()
        if "error" in data:
            raise RuntimeError(f"Erro Meta Graph API: {data['error'].get('message')}")
        return data.get("data", [])

    async def get_ad_sets(
        self,
        ad_account_id: str,
        campaign_id: Optional[str] = None,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Lista conjuntos de anúncios de uma conta ou campanha específica."""
        if campaign_id:
            url = f"{self.base_url}/{campaign_id}/adsets"
        else:
            acc_id = ad_account_id if ad_account_id.startswith("act_") else f"act_{ad_account_id}"
            url = f"{self.base_url}/{acc_id}/adsets"

        params = {
            "fields": "id,name,status,effective_status,campaign_id,daily_budget,lifetime_budget,billing_event,optimization_goal,bid_amount",
            "limit": limit
        }
        resp = await self.client.get(url, params=self._auth_params(params))
        data = resp.json()
        if "error" in data:
            raise RuntimeError(f"Erro Meta Graph API: {data['error'].get('message')}")
        return data.get("data", [])

    async def get_ads(
        self,
        ad_account_id: str,
        adset_id: Optional[str] = None,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Lista anúncios individuais de uma conta ou conjunto."""
        if adset_id:
            url = f"{self.base_url}/{adset_id}/ads"
        else:
            acc_id = ad_account_id if ad_account_id.startswith("act_") else f"act_{ad_account_id}"
            url = f"{self.base_url}/{acc_id}/ads"

        params = {
            "fields": "id,name,status,effective_status,adset_id,campaign_id,creative{id,name,title,body,image_url},issues_info",
            "limit": limit
        }
        resp = await self.client.get(url, params=self._auth_params(params))
        data = resp.json()
        if "error" in data:
            raise RuntimeError(f"Erro Meta Graph API: {data['error'].get('message')}")
        return data.get("data", [])

    async def update_object_status(self, object_id: str, status: str) -> Dict[str, Any]:
        """Atualiza o status de uma Campanha, Conjunto de Anúncios ou Anúncio individual (ex: 'PAUSED' ou 'ACTIVE')."""
        valid_statuses = {"ACTIVE", "PAUSED", "ARCHIVED"}
        status_clean = status.upper().strip()
        if status_clean not in valid_statuses:
            raise ValueError(f"Status '{status}' inválido. Válidos: {list(valid_statuses)}")

        url = f"{self.base_url}/{object_id}"
        resp = await self.client.post(url, data=self._auth_params({"status": status_clean}))
        data = resp.json()
        if "error" in data:
            raise RuntimeError(f"Erro Meta Graph API ao atualizar status: {data['error'].get('message')}")
        return {"success": data.get("success", True), "object_id": object_id, "new_status": status_clean}

    async def hydrate_lead(self, leadgen_id: str) -> Dict[str, Any]:
        """Hidrata um leadgen_id recebido via Webhook buscando nome, e-mail, telefone e perguntas preenchidas."""
        url = f"{self.base_url}/{leadgen_id}"
        params = self._auth_params({
            "fields": "id,created_time,ad_id,ad_name,adset_id,adset_name,campaign_id,campaign_name,form_id,field_data"
        })
        resp = await self.client.get(url, params=params)
        data = resp.json()
        if "error" in data:
            raise RuntimeError(f"Erro Meta Graph API ao hidratar lead {leadgen_id}: {data['error'].get('message')}")
        return data

    async def hydrate_ad_issues(self, ad_id: str) -> Dict[str, Any]:
        """Hidrata detalhes de desaprovação ou issues_info de um anúncio."""
        url = f"{self.base_url}/{ad_id}"
        params = self._auth_params({
            "fields": "id,name,status,effective_status,issues_info,recommendations,creative"
        })
        resp = await self.client.get(url, params=params)
        data = resp.json()
        if "error" in data:
            raise RuntimeError(f"Erro Meta Graph API ao hidratar anúncio {ad_id}: {data['error'].get('message')}")
        return data
