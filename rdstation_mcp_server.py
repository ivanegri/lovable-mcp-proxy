"""
RD Station MCP Server (local).

Expõe como tools MCP os principais endpoints da RD Station Marketing API v2 (`/platform/*`,
`/integrations/*`) e da RD Station CRM API v2 (`/crm/v2/*`), mais a tool genérica
`rdstation_request` para qualquer outro endpoint em `https://api.rd.services`.

Mesmo contrato do YungasMCPServer / SultsMCPServer / OmieMCPServer:
    list_tools(tenant_id) / list_resources(tenant_id) / call_tool(tenant_id, tool_name, arguments)
"""

import json
import logging
import urllib.parse
from typing import Any, Dict, List, Tuple

from mcp import types

from storage import StorageManager
from rdstation_client import RDStationClient

logger = logging.getLogger("mcp-proxy.rdstation_mcp")

# (tool_name, method, path, description, [path_params])
ENDPOINTS: List[Tuple[str, str, str, str, List[str]]] = [
    # ---- RD Station Marketing: Contatos & Leads ----
    ("rd_get_contact_by_email", "GET", "/platform/contacts/email:{email}", "Busca um contato/lead no RD Station Marketing pelo e-mail.", ["email"]),
    ("rd_get_contact_by_uuid", "GET", "/platform/contacts/{uuid}", "Busca um contato/lead no RD Station Marketing pelo UUID.", ["uuid"]),
    ("rd_create_contact", "POST", "/platform/contacts", "Cria um novo contato/lead no RD Station Marketing.", []),
    ("rd_update_contact_by_email", "PATCH", "/platform/contacts/email:{email}", "Atualiza dados de um contato/lead pelo e-mail.", ["email"]),
    ("rd_update_contact_by_uuid", "PATCH", "/platform/contacts/{uuid}", "Atualiza dados de um contato/lead pelo UUID.", ["uuid"]),
    ("rd_delete_contact_by_uuid", "DELETE", "/platform/contacts/{uuid}", "Remove um contato/lead pelo UUID.", ["uuid"]),
    ("rd_add_contact_tags", "POST", "/platform/contacts/email:{email}/tag", "Adiciona tags a um contato pelo e-mail. body: {tags: ['tag1', ...]}.", ["email"]),
    ("rd_get_contact_events", "GET", "/platform/contacts/{uuid}/events", "Lista eventos (conversões, oportunidades etc.) de um contato pelo UUID.", ["uuid"]),
    ("rd_get_contact_funnel", "GET", "/platform/contacts/{uuid}/funnels/default", "Consulta o estágio atual do funil de um contato pelo UUID.", ["uuid"]),
    ("rd_update_contact_funnel", "PUT", "/platform/contacts/{uuid}/funnels/default", "Atualiza o estágio no funil (lifecycle_stage, opportunity, contact_owner_email) de um contato.", ["uuid"]),
    ("rd_list_contact_fields", "GET", "/platform/contacts/fields", "Lista todos os campos padrão e personalizados de contatos no RD Station Marketing.", []),
    ("rd_create_contact_field", "POST", "/platform/contacts/fields", "Cria um campo personalizado de contato no RD Station Marketing.", []),
    # ---- RD Station Marketing: Eventos & Conversões ----
    ("rd_send_event", "POST", "/platform/events", "Envia um evento CDP (CONVERSION, OPPORTUNITY, SALE, OPPORTUNITY_LOST, etc.). body: {event_type, event_family: 'CDP', payload: {...}}.", []),
    # ---- RD Station Marketing: Segmentações ----
    ("rd_list_segmentations", "GET", "/platform/segmentations", "Lista segmentações de leads da conta. query: page, page_size, order.", []),
    ("rd_list_segmentation_contacts", "GET", "/platform/segmentations/{segmentation_id}/contacts", "Lista contatos pertencentes a uma segmentação. query: page, page_size.", ["segmentation_id"]),
    # ---- RD Station Marketing: Analytics ----
    ("rd_analytics_funnel", "GET", "/platform/analytics/funnel", "Retorna dados consolidados do funil (visitantes, leads, oportunidades, vendas). query: start_date, end_date.", []),
    ("rd_analytics_emails", "GET", "/platform/analytics/emails", "Retorna estatísticas de campanhas de e-mail marketing. query: start_date, end_date, campaign_id.", []),
    ("rd_analytics_workflow_emails", "GET", "/platform/analytics/workflow_emails", "Retorna estatísticas de e-mails de fluxos de automação. query: start_date, end_date, workflow_id.", []),
    ("rd_analytics_conversions", "GET", "/platform/analytics/conversions", "Retorna estatísticas de conversão (Landing Pages, Forms, Pop-ups). query: start_date, end_date, assets_type.", []),
    # ---- RD Station Marketing: Webhooks ----
    ("rd_list_webhooks", "GET", "/integrations/webhooks", "Lista webhooks cadastrados na conta RD Station.", []),
    ("rd_create_webhook", "POST", "/integrations/webhooks", "Cria um webhook na conta RD Station (ex.: WEBHOOK.CONVERTED, WEBHOOK.MARKED_OPPORTUNITY).", []),
    ("rd_delete_webhook", "DELETE", "/integrations/webhooks/{uuid}", "Remove um webhook pelo UUID.", ["uuid"]),
    # ---- RD Station CRM v2: Negociações (Deals) ----
    ("rd_crm_list_deals", "GET", "/crm/v2/deals", "CRM v2: lista negociações/oportunidades. query: page, limit, filter[status], filter[pipeline_id], etc.", []),
    ("rd_crm_get_deal", "GET", "/crm/v2/deals/{id}", "CRM v2: consulta detalhes de uma negociação pelo ID.", ["id"]),
    ("rd_crm_create_deal", "POST", "/crm/v2/deals", "CRM v2: cria uma nova negociação.", []),
    ("rd_crm_update_deal", "PUT", "/crm/v2/deals/{id}", "CRM v2: atualiza uma negociação pelo ID.", ["id"]),
    # ---- RD Station CRM v2: Empresas (Organizations) ----
    ("rd_crm_list_organizations", "GET", "/crm/v2/organizations", "CRM v2: lista empresas cadastradas no CRM.", []),
    ("rd_crm_get_organization", "GET", "/crm/v2/organizations/{id}", "CRM v2: consulta detalhes de uma empresa pelo ID.", ["id"]),
    ("rd_crm_create_organization", "POST", "/crm/v2/organizations", "CRM v2: cria uma empresa no CRM.", []),
    ("rd_crm_update_organization", "PUT", "/crm/v2/organizations/{id}", "CRM v2: atualiza uma empresa no CRM pelo ID.", ["id"]),
    # ---- RD Station CRM v2: Contatos ----
    ("rd_crm_list_contacts", "GET", "/crm/v2/contacts", "CRM v2: lista contatos cadastrados no CRM.", []),
    ("rd_crm_get_contact", "GET", "/crm/v2/contacts/{id}", "CRM v2: consulta detalhes de um contato no CRM pelo ID.", ["id"]),
    ("rd_crm_create_contact", "POST", "/crm/v2/contacts", "CRM v2: cria um contato no CRM.", []),
    ("rd_crm_update_contact", "PUT", "/crm/v2/contacts/{id}", "CRM v2: atualiza um contato no CRM pelo ID.", ["id"]),
    # ---- RD Station CRM v2: Pipelines, Tarefas & Usuários ----
    ("rd_crm_list_pipelines", "GET", "/crm/v2/pipelines", "CRM v2: lista funis/pipelines e suas etapas.", []),
    ("rd_crm_list_tasks", "GET", "/crm/v2/tasks", "CRM v2: lista tarefas agendadas no CRM.", []),
    ("rd_crm_create_task", "POST", "/crm/v2/tasks", "CRM v2: cria uma tarefa no CRM.", []),
    ("rd_crm_list_users", "GET", "/crm/v2/users", "CRM v2: lista usuários/vendedores da conta no CRM.", []),
]

_BY_NAME = {e[0]: e for e in ENDPOINTS}
_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _schema(method: str, path_params: List[str]) -> Dict[str, Any]:
    props: Dict[str, Any] = {
        p: {"type": "string", "description": f"Parâmetro de rota '{p}'."}
        for p in path_params
    }
    props["query"] = {"type": "object", "description": "Parâmetros de query string (opcional)."}
    if method in _WRITE_METHODS:
        props["body"] = {"type": "object", "description": "Corpo JSON da requisição (conforme documentação da RD Station)."}
    schema: Dict[str, Any] = {"type": "object", "properties": props}
    if path_params:
        schema["required"] = list(path_params)
    return schema


def _text(data: Any) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=json.dumps(data, ensure_ascii=False, indent=2))]
    )


class RDStationMCPServer:
    def __init__(self, storage: StorageManager):
        self.storage = storage
        self.client = RDStationClient(storage)
        self._tools_cache: List[types.Tool] = self._build_tools()

    def _build_tools(self) -> List[types.Tool]:
        tools = [
            types.Tool(
                name=name,
                description=f"[RD Station] {desc} ({method} {path})",
                inputSchema=_schema(method, pp),
            )
            for name, method, path, desc, pp in ENDPOINTS
        ]
        tools.append(
            types.Tool(
                name="rdstation_request",
                description=(
                    "[RD Station] Chamada genérica a qualquer endpoint da RD Station API "
                    "(Marketing `/platform/...`, `/integrations/...` ou CRM v2 `/crm/v2/...`) "
                    "usando o token OAuth gerenciado pelo proxy."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "method": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"]},
                        "path": {"type": "string", "description": "Caminho na API (ex.: '/platform/contacts/fields' ou '/crm/v2/deals')."},
                        "query": {"type": "object", "description": "Parâmetros de query string (opcional)."},
                        "body": {"type": "object", "description": "Corpo JSON da requisição (opcional)."},
                    },
                    "required": ["method", "path"],
                },
            )
        )
        return tools

    async def list_tools(self, tenant_id: str) -> types.ListToolsResult:
        return types.ListToolsResult(tools=self._tools_cache)

    async def list_resources(self, tenant_id: str) -> types.ListResourcesResult:
        return types.ListResourcesResult(resources=[])

    async def call_tool(self, tenant_id: str, tool_name: str, arguments: Dict[str, Any]) -> types.CallToolResult:
        try:
            args = arguments or {}
            query = args.get("query") or {}
            body = args.get("body")

            if tool_name == "rdstation_request":
                data = await self.client.request(
                    tenant=tenant_id,
                    method=args["method"],
                    path=args["path"],
                    query=query,
                    body=body,
                )
                return _text(data)

            ep = _BY_NAME.get(tool_name)
            if not ep:
                raise ValueError(f"Tool desconhecida: '{tool_name}'")
            _name, method, path, _desc, path_params = ep

            missing = [p for p in path_params if not args.get(p)]
            if missing:
                raise ValueError(f"Parâmetros obrigatórios ausentes: {missing}")

            encoded = {p: urllib.parse.quote(str(args[p]), safe="") for p in path_params}
            real_path = path.format(**encoded)
            data = await self.client.request(tenant=tenant_id, method=method, path=real_path, query=query, body=body)
            return _text(data)
        except Exception as e:
            logger.error(f"[RD Station MCP] Erro em tools/call '{tool_name}' ({tenant_id}): {e}")
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=f"Erro ao executar '{tool_name}': {e}")],
                isError=True,
            )
