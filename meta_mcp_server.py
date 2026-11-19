"""
Meta Ads MCP Server Implementation.
Exposes MCP Resources and MCP Tools for AI agents (Claude, Cursor, N8N, Flowise).
"""

import json
import logging
from typing import Optional, Dict, Any, List

from mcp import types
from mcp.server.lowlevel import Server

from meta_database import MetaDatabase
from meta_client import MetaGraphClient
from storage import StorageManager

logger = logging.getLogger("mcp-proxy.meta_mcp")


class MetaAdsMCPServer:
    def __init__(self, storage: StorageManager, meta_db: MetaDatabase):
        self.storage = storage
        self.meta_db = meta_db

        self.server = Server(
            "meta-ads-mcp-server",
            version="1.0.0",
            instructions=(
                "Meta Ads MCP Server. Fornece ferramentas (tools) para consultar métricas de campanhas, "
                "conjuntos de anúncios, anúncios, pausar/ativar anúncios e consultar webhooks em tempo real "
                "(leads capturados, fadiga criativa e anúncios com problemas). Fornece também resources "
                "para leitura imediata do histórico recente."
            ),
            on_list_resources=self._on_list_resources,
            on_read_resource=self._on_read_resource,
            on_list_tools=self._on_list_tools,
            on_call_tool=self._on_call_tool,
        )

    def _get_client_for_tenant(self, tenant_id: str) -> MetaGraphClient:
        tokens = self.storage.load_tokens("meta", tenant_id)
        if not tokens or not tokens.get("access_token"):
            raise RuntimeError(
                f"Nenhum token da Meta Ads configurado para o tenant '{tenant_id}'. "
                "Faça login via OAuth no painel ou configure o token de acesso."
            )
        return MetaGraphClient(tokens["access_token"])

    # ==========================================
    # 1. MCP Resources
    # ==========================================
    async def list_resources(self, tenant_id: str) -> types.ListResourcesResult:
        resources = [
            types.Resource(
                uri="meta://ad_account/recent_alerts",
                name="Meta Ad Account Recent Alerts",
                description="Histórico recente de alertas recebidos via webhook (anúncios reprovados, falhas de cobrança, etc.)",
                mimeType="application/json"
            ),
            types.Resource(
                uri="meta://ad_account/creative_fatigue",
                name="Meta Creative Fatigue Alerts",
                description="Alertas de fadiga de criativos recebidos via webhook em tempo real.",
                mimeType="application/json"
            ),
            types.Resource(
                uri="meta://leads/today",
                name="Meta Leads Captured Today",
                description="Leads recebidos e hidratados no banco de dados via webhook leadgen.",
                mimeType="application/json"
            ),
            types.Resource(
                uri="meta://webhooks/raw_events",
                name="Recent Meta Webhook Ingested Events",
                description="Todos os eventos brutos de webhook da Meta nas últimas 24 horas.",
                mimeType="application/json"
            )
        ]
        return types.ListResourcesResult(resources=resources)

    async def read_resource(self, tenant_id: str, uri: str) -> types.ReadResourceResult:
        uri_str = str(uri)
        if uri_str == "meta://ad_account/recent_alerts":
            data = self.meta_db.get_recent_alerts(tenant_id, limit=30)
        elif uri_str == "meta://ad_account/creative_fatigue":
            all_alerts = self.meta_db.get_recent_alerts(tenant_id, limit=50)
            data = [a for a in all_alerts if a.get("alert_type") == "creative_fatigue"]
        elif uri_str == "meta://leads/today":
            data = self.meta_db.get_recent_leads(tenant_id, limit=50)
        elif uri_str == "meta://webhooks/raw_events":
            data = self.meta_db.get_webhook_events(tenant_id, hours=24, limit=50)
        else:
            raise ValueError(f"Resource URI desconhecido: {uri_str}")

        text_content = json.dumps(data, ensure_ascii=False, indent=2)
        return types.ReadResourceResult(
            contents=[types.TextResourceContents(uri=uri_str, mimeType="application/json", text=text_content)]
        )

    # ==========================================
    # 2. MCP Tools
    # ==========================================
    async def list_tools(self, tenant_id: str) -> types.ListToolsResult:
        tools = [
            types.Tool(
                name="get_webhook_events",
                description=(
                    "Consulta o banco de dados local do proxy por eventos recebidos via Meta Webhooks "
                    "(ex: leads, reprovação de anúncios 'with_issues_ad_objects', 'creative_fatigue'). "
                    "Permite ao agente saber exatamente o que aconteceu recentemente sem estourar rate-limits."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "event_type": {
                            "type": "string",
                            "description": "Filtro por tipo de evento (ex: 'leadgen', 'with_issues_ad_objects', 'creative_fatigue', 'ad_account'). Opcional."
                        },
                        "hours": {
                            "type": "integer",
                            "description": "Janela de tempo em horas para buscar eventos (padrão: 24).",
                            "default": 24
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Quantidade máxima de registros a retornar (padrão: 50).",
                            "default": 50
                        }
                    }
                }
            ),
            types.Tool(
                name="get_ad_accounts",
                description="Lista todas as contas de anúncio (Ad Accounts) acessíveis com o token da Meta.",
                inputSchema={"type": "object", "properties": {}}
            ),
            types.Tool(
                name="get_campaign_insights",
                description=(
                    "Obtém métricas consolidadas em tempo real da Meta Graph API (spend, impressões, cliques, CPC, CPM, CTR, conversões). "
                    "Preenche a lacuna que os webhooks não cobrem (performance contínua)."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "ad_account_id": {
                            "type": "string",
                            "description": "ID da conta de anúncios (ex: 'act_123456789' ou '123456789')."
                        },
                        "date_preset": {
                            "type": "string",
                            "description": "Período da consulta: 'today', 'yesterday', 'last_3d', 'last_7d', 'last_14d', 'last_30d', 'this_month', 'last_month'.",
                            "default": "last_7d"
                        },
                        "level": {
                            "type": "string",
                            "description": "Nível de agregação dos dados: 'account', 'campaign', 'adset' ou 'ad'. Padrão: 'campaign'.",
                            "default": "campaign"
                        }
                    },
                    "required": ["ad_account_id"]
                }
            ),
            types.Tool(
                name="get_campaigns",
                description="Lista as campanhas de uma conta com status, objetivo, orçamento e status efetivo.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "ad_account_id": {
                            "type": "string",
                            "description": "ID da conta de anúncios (ex: 'act_123456789')."
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Quantidade máxima de campanhas (padrão 50).",
                            "default": 50
                        }
                    },
                    "required": ["ad_account_id"]
                }
            ),
            types.Tool(
                name="get_ad_sets",
                description="Lista conjuntos de anúncios (AdSets) de uma conta ou de uma campanha específica.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "ad_account_id": {
                            "type": "string",
                            "description": "ID da conta de anúncios."
                        },
                        "campaign_id": {
                            "type": "string",
                            "description": "ID da campanha para filtrar (opcional)."
                        },
                        "limit": {
                            "type": "integer",
                            "default": 50
                        }
                    },
                    "required": ["ad_account_id"]
                }
            ),
            types.Tool(
                name="get_ads",
                description="Lista anúncios individuais de uma conta ou conjunto, incluindo links do criativo e problemas identificados.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "ad_account_id": {
                            "type": "string",
                            "description": "ID da conta de anúncios."
                        },
                        "adset_id": {
                            "type": "string",
                            "description": "ID do conjunto para filtrar (opcional)."
                        },
                        "limit": {
                            "type": "integer",
                            "default": 50
                        }
                    },
                    "required": ["ad_account_id"]
                }
            ),
            types.Tool(
                name="update_ad_status",
                description=(
                    "Altera o status de um Anúncio, Conjunto de Anúncios ou Campanha diretamente na Meta Graph API. "
                    "Permite ao agente pausar imediatamente anúncios fadigados ou reprovados e ativar campanhas."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "object_id": {
                            "type": "string",
                            "description": "ID do anúncio, conjunto de anúncios ou campanha na Meta."
                        },
                        "status": {
                            "type": "string",
                            "description": "Novo status desejado: 'ACTIVE', 'PAUSED' ou 'ARCHIVED'.",
                            "enum": ["ACTIVE", "PAUSED", "ARCHIVED"]
                        }
                    },
                    "required": ["object_id", "status"]
                }
            ),
            types.Tool(
                name="get_leads",
                description="Consulta os leads capturados via formulários do Facebook/Instagram Lead Ads persistidos pelo webhook.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "limit": {
                            "type": "integer",
                            "description": "Quantidade máxima de leads (padrão 50).",
                            "default": 50
                        }
                    }
                }
            ),
            types.Tool(
                name="get_ad_alerts",
                description="Consulta alertas registrados sobre anúncios com problemas, reprovações ou fadiga de criativo.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "unresolved_only": {
                            "type": "boolean",
                            "description": "Se verdadeiro, traz apenas alertas ainda não resolvidos.",
                            "default": False
                        },
                        "limit": {
                            "type": "integer",
                            "default": 50
                        }
                    }
                }
            )
        ]
        return types.ListToolsResult(tools=tools)

    async def call_tool(self, tenant_id: str, tool_name: str, arguments: Dict[str, Any]) -> types.CallToolResult:
        try:
            if tool_name == "get_webhook_events":
                event_type = arguments.get("event_type")
                hours = int(arguments.get("hours", 24))
                limit = int(arguments.get("limit", 50))
                data = self.meta_db.get_webhook_events(tenant_id, event_type=event_type, hours=hours, limit=limit)
                return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(data, ensure_ascii=False, indent=2))])

            elif tool_name == "get_leads":
                limit = int(arguments.get("limit", 50))
                data = self.meta_db.get_recent_leads(tenant_id, limit=limit)
                return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(data, ensure_ascii=False, indent=2))])

            elif tool_name == "get_ad_alerts":
                unresolved_only = bool(arguments.get("unresolved_only", False))
                limit = int(arguments.get("limit", 50))
                data = self.meta_db.get_recent_alerts(tenant_id, limit=limit, unresolved_only=unresolved_only)
                return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(data, ensure_ascii=False, indent=2))])

            # Ferramentas que fazem chamadas ao Meta Graph API
            client = self._get_client_for_tenant(tenant_id)
            try:
                if tool_name == "get_ad_accounts":
                    accounts = await client.get_ad_accounts()
                    return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(accounts, ensure_ascii=False, indent=2))])

                elif tool_name == "get_campaign_insights":
                    ad_account_id = arguments.get("ad_account_id", "")
                    date_preset = arguments.get("date_preset", "last_7d")
                    level = arguments.get("level", "campaign")
                    insights = await client.get_campaign_insights(ad_account_id, date_preset=date_preset, level=level)
                    self.meta_db.cache_insights(tenant_id, ad_account_id, level, date_preset, insights)
                    return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(insights, ensure_ascii=False, indent=2))])

                elif tool_name == "get_campaigns":
                    ad_account_id = arguments.get("ad_account_id", "")
                    limit = int(arguments.get("limit", 50))
                    campaigns = await client.get_campaigns(ad_account_id, limit=limit)
                    return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(campaigns, ensure_ascii=False, indent=2))])

                elif tool_name == "get_ad_sets":
                    ad_account_id = arguments.get("ad_account_id", "")
                    campaign_id = arguments.get("campaign_id")
                    limit = int(arguments.get("limit", 50))
                    adsets = await client.get_ad_sets(ad_account_id, campaign_id=campaign_id, limit=limit)
                    return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(adsets, ensure_ascii=False, indent=2))])

                elif tool_name == "get_ads":
                    ad_account_id = arguments.get("ad_account_id", "")
                    adset_id = arguments.get("adset_id")
                    limit = int(arguments.get("limit", 50))
                    ads = await client.get_ads(ad_account_id, adset_id=adset_id, limit=limit)
                    return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(ads, ensure_ascii=False, indent=2))])

                elif tool_name == "update_ad_status":
                    object_id = arguments.get("object_id", "")
                    status = arguments.get("status", "")
                    res = await client.update_object_status(object_id, status)
                    return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(res, ensure_ascii=False, indent=2))])

                else:
                    raise ValueError(f"Tool desconhecida: '{tool_name}'")
            finally:
                await client.close()

        except Exception as e:
            logger.error(f"[Meta MCP] Erro em tools/call '{tool_name}' ({tenant_id}): {e}")
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=f"Erro ao executar '{tool_name}': {e}")],
                isError=True,
            )

    # Server callbacks internos para uso direto se necessário
    async def _on_list_resources(self, ctx: Any, params: Optional[types.PaginatedRequestParams] = None) -> types.ListResourcesResult:
        return await self.list_resources("default")

    async def _on_read_resource(self, ctx: Any, params: types.ReadResourceRequestParams) -> types.ReadResourceResult:
        return await self.read_resource("default", str(params.uri))

    async def _on_list_tools(self, ctx: Any, params: Optional[types.PaginatedRequestParams] = None) -> types.ListToolsResult:
        return await self.list_tools("default")

    async def _on_call_tool(self, ctx: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
        return await self.call_tool("default", params.name, params.arguments or {})
