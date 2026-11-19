"""
Yungas MCP Server (local).

Expõe como tools MCP todos os endpoints da Yungas Public API v3 e da PEX API v1, mais uma tool
genérica `yungas_request`. Mesmo contrato do MetaAdsMCPServer: list_tools(tenant) / call_tool(tenant, ...).
"""

import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from mcp import types

from storage import StorageManager
from yungas_client import YungasClient, YungasError

logger = logging.getLogger("mcp-proxy.yungas_mcp")

# (nome, método, path, descrição, [path params], api)
# Convenção de entrada: path params como propriedades obrigatórias;
# `query` (objeto) para query string; `body` (objeto) para POST/PUT/PATCH.
_V3 = "v3"
_PEX = "pex"
ENDPOINTS: List[Tuple[str, str, str, str, List[str], str]] = [
    # Auth / usuário
    ("yungas_get_user_info", "GET", "__userinfo__", "Dados do usuário autenticado (userinfo OpenID).", [], _V3),
    # Users
    ("yungas_list_functions", "GET", "/functions", "Lista todas as funções (cargos) de usuários.", [], _V3),
    ("yungas_manage_user_groups", "PUT", "/users/agroup", "Gerencia os grupos de um usuário.", [], _V3),
    ("yungas_list_users", "GET", "/users/", "Lista usuários. query: page, page_size, franchises, groups.", [], _V3),
    ("yungas_get_user", "GET", "/users/{identifier}", "Busca usuário por e-mail ou telefone.", ["identifier"], _V3),
    ("yungas_invite_user", "POST", "/users/", "Convida/cria um usuário.", [], _V3),
    ("yungas_edit_user", "PATCH", "/users/", "Edita um usuário.", [], _V3),
    ("yungas_delete_user", "DELETE", "/users/", "Remove um usuário.", [], _V3),
    # Files
    ("yungas_get_signed_url", "GET", "/files/get_signed_url", "Gera URL assinada de arquivo. query: url, expires_in.", [], _V3),
    # Franchises
    ("yungas_list_franchises", "GET", "/franchises/", "Lista franquias. query: page, page_size, franchise_name, tag, include_custom_fields_data.", [], _V3),
    ("yungas_get_franchise", "GET", "/franchises/{franchise_id}", "Dados de uma franquia.", ["franchise_id"], _V3),
    ("yungas_create_franchise", "POST", "/franchises/", "Cria franquia.", [], _V3),
    ("yungas_edit_franchise", "PATCH", "/franchises/", "Edita franquia.", [], _V3),
    ("yungas_delete_franchise", "DELETE", "/franchises/", "Remove franquia.", [], _V3),
    ("yungas_manage_franchise_tags", "PATCH", "/franchises/tags", "Gerencia tags de franquias.", [], _V3),
    # Checklist
    ("yungas_list_checklist_reports", "GET", "/checklists/reports", "Lista relatórios de checklist.", [], _V3),
    ("yungas_send_autochecklist", "POST", "/checklists/autochecklist/{autochecklist_id}/send", "Envia um autochecklist.", ["autochecklist_id"], _V3),
    ("yungas_update_checklist_report_item", "PATCH", "/checklists/reports/{checklist_report_id}/item/{item_id}", "Atualiza item de relatório de checklist.", ["checklist_report_id", "item_id"], _V3),
    ("yungas_update_checklist_report", "PATCH", "/checklists/reports/{checklist_report_id}", "Atualiza relatório de checklist.", ["checklist_report_id"], _V3),
    # FAQ
    ("yungas_list_faq_categories", "GET", "/faq/categories", "Lista categorias do FAQ.", [], _V3),
    ("yungas_list_faq_topics", "GET", "/faq/topics", "Lista tópicos do FAQ.", [], _V3),
    ("yungas_list_faq_qas", "GET", "/faq/qas", "Lista perguntas e respostas do FAQ.", [], _V3),
    ("yungas_get_faq_qa", "GET", "/faq/qas/{qa_id}", "Detalhe de uma pergunta/resposta do FAQ.", ["qa_id"], _V3),
    # Helpdesk
    ("yungas_list_tickets", "GET", "/ticket", "Lista tickets.", [], _V3),
    ("yungas_get_ticket", "GET", "/ticket/detail", "Detalhe de ticket. query: ticket_id.", [], _V3),
    ("yungas_create_ticket", "POST", "/ticket/", "Cria ticket.", [], _V3),
    ("yungas_create_demand", "POST", "/ticket/demand", "Cria demanda.", [], _V3),
    ("yungas_send_ticket_message", "POST", "/ticket/messages", "Envia mensagem em ticket.", [], _V3),
    ("yungas_send_ticket_comment", "POST", "/ticket/comments", "Envia comentário interno em ticket.", [], _V3),
    ("yungas_close_ticket", "PATCH", "/ticket/{ticket_id}/close", "Fecha ticket.", ["ticket_id"], _V3),
    ("yungas_reopen_ticket", "PATCH", "/ticket/{ticket_id}/reopen", "Reabre ticket.", ["ticket_id"], _V3),
    ("yungas_edit_ticket", "PATCH", "/ticket/", "Edita ticket.", [], _V3),
    ("yungas_reassign_ticket_department", "PATCH", "/ticket/{ticket_id}/reassign-department", "Move ticket para outro departamento.", ["ticket_id"], _V3),
    ("yungas_reassign_ticket_user", "PATCH", "/ticket/{ticket_id}/reassign", "Atribui ticket a outro usuário.", ["ticket_id"], _V3),
    ("yungas_list_ticket_participants", "GET", "/ticket/{ticket_id}/franchisor-participants", "Lista participantes do ticket.", ["ticket_id"], _V3),
    ("yungas_add_ticket_participants", "POST", "/ticket/{ticket_id}/franchisor-participants", "Adiciona participantes ao ticket.", ["ticket_id"], _V3),
    ("yungas_remove_ticket_participant", "DELETE", "/ticket/{ticket_id}/franchisor-participants/{user_id}", "Remove participante do ticket.", ["ticket_id", "user_id"], _V3),
    ("yungas_get_department", "GET", "/departments/{department_id}", "Detalhe de departamento. query: disabled.", ["department_id"], _V3),
    ("yungas_list_departments", "GET", "/departments", "Lista departamentos. query: page_size, page, with_subdepartments, disabled.", [], _V3),
    ("yungas_get_subdepartment", "GET", "/departments/subdepartment/{subdepartment_id}", "Detalhe de subdepartamento.", ["subdepartment_id"], _V3),
    # Performance
    ("yungas_create_revenue", "POST", "/performance/revenue", "Registra faturamento.", [], _V3),
    # Store
    ("yungas_store_list_suppliers", "GET", "/store/suppliers", "Lista fornecedores.", [], _V3),
    ("yungas_store_list_categories", "GET", "/store/categories", "Lista categorias de produtos.", [], _V3),
    ("yungas_store_get_category", "GET", "/store/categories/{category_id}", "Detalhe de categoria.", ["category_id"], _V3),
    ("yungas_store_list_products", "GET", "/store/products", "Lista produtos.", [], _V3),
    ("yungas_store_list_payment_methods", "GET", "/store/payment-methods", "Lista formas de pagamento.", [], _V3),
    ("yungas_store_list_orders", "GET", "/store/orders", "Lista pedidos.", [], _V3),
    ("yungas_store_list_sub_orders", "GET", "/store/sub-orders", "Lista sub-pedidos. query: page, page_size.", [], _V3),
    ("yungas_store_create_supplier", "POST", "/store/suppliers", "Cria fornecedor.", [], _V3),
    ("yungas_store_create_category", "POST", "/store/categories", "Cria categoria.", [], _V3),
    ("yungas_store_create_product", "POST", "/store/products", "Cria produto.", [], _V3),
    ("yungas_store_create_order", "POST", "/store/orders", "Cria pedido.", [], _V3),
    ("yungas_store_edit_supplier", "PATCH", "/store/suppliers/{supplier_id}", "Edita fornecedor.", ["supplier_id"], _V3),
    ("yungas_store_edit_product", "PATCH", "/store/products/{product_id}", "Edita produto.", ["product_id"], _V3),
    ("yungas_store_edit_category", "PATCH", "/store/categories/{category_id}", "Edita categoria.", ["category_id"], _V3),
    ("yungas_store_edit_sub_order", "PATCH", "/store/sub-orders/{sub_order_id}", "Edita sub-pedido.", ["sub_order_id"], _V3),
    ("yungas_store_cancel_sub_order", "PATCH", "/store/sub-orders/{sub_order_id}/cancel", "Cancela sub-pedido.", ["sub_order_id"], _V3),
    ("yungas_store_delete_category", "DELETE", "/store/categories/{category_id}", "Remove categoria.", ["category_id"], _V3),
    ("yungas_store_set_sub_order_custom_status", "PATCH", "/store/sub-orders/{sub_order_id}/custom-status", "Atualiza status customizado de sub-pedido.", ["sub_order_id"], _V3),
    # News / Notice
    ("yungas_get_news", "GET", "/news/{news_id}", "Detalhe de uma notícia.", ["news_id"], _V3),
    ("yungas_list_news", "GET", "/news", "Lista notícias.", [], _V3),
    ("yungas_send_notice", "POST", "/notice/", "Envia um comunicado.", [], _V3),
    ("yungas_list_notices", "GET", "/notice", "Lista comunicados. query: page.", [], _V3),
    ("yungas_get_notice", "GET", "/notice/{notice_id}", "Detalhe de comunicado. query: paginate, page.", ["notice_id"], _V3),
    # PEX (API-KEY)
    ("yungas_pex_get", "GET", "/pex/{pex_id}", "PEX: dados gerais do programa.", ["pex_id"], _PEX),
    ("yungas_pex_get_periods", "GET", "/pex/{pex_id}/periods", "PEX: períodos.", ["pex_id"], _PEX),
    ("yungas_pex_get_franchises", "GET", "/pex/{pex_id}/franchises", "PEX: unidades.", ["pex_id"], _PEX),
    ("yungas_pex_get_kpis", "GET", "/pex/{pex_id}/kpis", "PEX: indicadores.", ["pex_id"], _PEX),
    ("yungas_pex_update_kpi_scores", "POST", "/pex/{pex_id}/kpi-scores", "PEX: envia/atualiza valores de indicadores.", ["pex_id"], _PEX),
    ("yungas_pex_recalculate", "POST", "/pex/{pex_id}/recalculate", "PEX: recalcula o PEX.", ["pex_id"], _PEX),
]

_BY_NAME = {e[0]: e for e in ENDPOINTS}
_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _schema(method: str, path_params: List[str]) -> Dict[str, Any]:
    props: Dict[str, Any] = {p: {"type": "string", "description": f"Parâmetro de rota '{p}'."} for p in path_params}
    props["query"] = {"type": "object", "description": "Parâmetros de query string (opcional)."}
    if method in _WRITE_METHODS:
        props["body"] = {"type": "object", "description": "Corpo JSON da requisição (veja a documentação da Yungas)."}
    return {"type": "object", "properties": props, "required": list(path_params)}


def _text(data: Any) -> types.CallToolResult:
    return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(data, ensure_ascii=False, indent=2))])


class YungasMCPServer:
    def __init__(self, storage: StorageManager):
        self.storage = storage
        self.client = YungasClient(storage)

    async def list_tools(self, tenant_id: str) -> types.ListToolsResult:
        tools = [
            types.Tool(name=name, description=f"[Yungas] {desc}", inputSchema=_schema(method, pp))
            for name, method, _path, desc, pp, _api in ENDPOINTS
        ]
        tools.append(types.Tool(
            name="yungas_request",
            description="[Yungas] Chamada genérica a qualquer endpoint da Public API v3 (ou PEX) não coberto pelas outras tools.",
            inputSchema={
                "type": "object",
                "properties": {
                    "method": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"]},
                    "path": {"type": "string", "description": "Caminho após /api/v3/public (ex: '/users/'), ou após /api/v1/public se api='pex'."},
                    "api": {"type": "string", "enum": ["v3", "pex"], "default": "v3"},
                    "query": {"type": "object"},
                    "body": {"type": "object"},
                },
                "required": ["method", "path"],
            },
        ))
        return types.ListToolsResult(tools=tools)

    async def list_resources(self, tenant_id: str) -> types.ListResourcesResult:
        return types.ListResourcesResult(resources=[])

    async def call_tool(self, tenant_id: str, tool_name: str, arguments: Dict[str, Any]) -> types.CallToolResult:
        try:
            query = arguments.get("query") or {}
            body = arguments.get("body")

            if tool_name == "yungas_request":
                data = await self.client.request(
                    tenant_id, arguments["method"], arguments["path"],
                    query=query, body=body, api=arguments.get("api", "v3"),
                )
                return _text(data)

            ep = _BY_NAME.get(tool_name)
            if not ep:
                raise ValueError(f"Tool desconhecida: '{tool_name}'")
            _name, method, path, _desc, path_params, api = ep

            if path == "__userinfo__":
                import httpx
                token, _ = await self.client._ensure_token(tenant_id)
                async with httpx.AsyncClient(timeout=20) as http:
                    r = await http.get(
                        "https://id.yungas.com.br/realms/yungas-prod/protocol/openid-connect/userinfo",
                        headers={"Authorization": f"Bearer {token}"},
                    )
                if r.status_code >= 400:
                    raise YungasError(f"userinfo -> HTTP {r.status_code}: {r.text[:300]}")
                return _text(r.json())

            missing = [p for p in path_params if not arguments.get(p)]
            if missing:
                raise ValueError(f"Parâmetros obrigatórios ausentes: {missing}")
            real_path = path.format(**{p: str(arguments[p]) for p in path_params})
            data = await self.client.request(tenant_id, method, real_path, query=query, body=body, api=api)
            return _text(data)
        except Exception as e:
            logger.error(f"[Yungas MCP] Erro em tools/call '{tool_name}' ({tenant_id}): {e}")
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=f"Erro ao executar '{tool_name}': {e}")],
                isError=True,
            )
