"""
Omie ERP MCP Server (local).

Carrega o catálogo oficial gerado a partir de `https://developer.omie.com.br/service-list/`
e dos WSDLs de todos os serviços (`omie_specs/catalog.json`: 138 endpoints únicos cobrindo
as 152 entradas das 7 categorias e 500 métodos).

Expõe 25 tools compactas para manter compatibilidade com todos os clientes MCP/LLMs:
- 4 tools de descoberta + execução dinâmica (cobertura de 100% dos 138 serviços e 500 métodos):
    - omie_list_services
    - omie_get_service_methods
    - omie_get_method_schema
    - omie_call
- 21 atalhos diretos para as operações mais comuns do ERP (Empresas, Clientes, Produtos,
  Estoque, Pedidos de Venda, NF-e, Finanças, Contas a Pagar/Receber, OS e CRM).

Mesmo contrato do YungasMCPServer / SultsMCPServer:
    list_tools(tenant_id) / list_resources(tenant_id) / call_tool(tenant_id, tool_name, arguments)
"""

import os
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from mcp import types

from storage import StorageManager
from omie_client import OmieClient

logger = logging.getLogger("mcp-proxy.omie_mcp")

CATALOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "omie_specs", "catalog.json")


def _load_catalog() -> Dict[str, Any]:
    if not os.path.exists(CATALOG_PATH):
        logger.warning(f"[Omie MCP] Catálogo não encontrado em {CATALOG_PATH}")
        return {"services": [], "total_services": 0, "total_methods": 0}
    with open(CATALOG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


CATALOG: Dict[str, Any] = _load_catalog()
SERVICES: List[Dict[str, Any]] = CATALOG.get("services", [])
SERVICES_BY_PATH: Dict[str, Dict[str, Any]] = {s["path"]: s for s in SERVICES}

# Atalhos diretos: (tool_name, service_path, call, description, default_param)
SHORTCUTS: List[Tuple[str, str, str, str, Dict[str, Any]]] = [
    # Geral
    (
        "omie_listar_empresas",
        "/api/v1/geral/empresas/",
        "ListarEmpresas",
        "Lista empresas cadastradas no Omie. Aceita pagina e registros_por_pagina em param.",
        {"pagina": 1, "registros_por_pagina": 50},
    ),
    (
        "omie_listar_clientes",
        "/api/v1/geral/clientes/",
        "ListarClientes",
        "Lista clientes, fornecedores e transportadoras. Aceita pagina, registros_por_pagina, clientesFiltro em param.",
        {"pagina": 1, "registros_por_pagina": 50},
    ),
    (
        "omie_consultar_cliente",
        "/api/v1/geral/clientes/",
        "ConsultarCliente",
        "Consulta um cliente pelo codigo_cliente_omie ou codigo_cliente_integracao em param.",
        {},
    ),
    (
        "omie_upsert_cliente",
        "/api/v1/geral/clientes/",
        "UpsertCliente",
        "Inclui ou altera um cliente/fornecedor no Omie (informe os campos de cadastro em param).",
        {},
    ),
    # Produtos & Estoque
    (
        "omie_listar_produtos",
        "/api/v1/geral/produtos/",
        "ListarProdutos",
        "Lista produtos cadastrados. Aceita pagina, registros_por_pagina, filtrar_apenas_omiepdv em param.",
        {"pagina": 1, "registros_por_pagina": 50, "apenas_importado_api": "N", "filtrar_apenas_omiepdv": "N"},
    ),
    (
        "omie_consultar_produto",
        "/api/v1/geral/produtos/",
        "ConsultarProduto",
        "Consulta um produto por codigo_produto, codigo_produto_integracao ou codigo em param.",
        {},
    ),
    (
        "omie_upsert_produto",
        "/api/v1/geral/produtos/",
        "UpsertProduto",
        "Inclui ou altera um produto no Omie (informe os campos do produto em param).",
        {},
    ),
    (
        "omie_posicao_estoque",
        "/api/v1/estoque/consulta/",
        "ListarPosEstoque",
        "Consulta a posição de estoque dos produtos. Informe nPagina, nRegPorPagina e dDataPosicao (dd/mm/aaaa) em param.",
        {"nPagina": 1, "nRegPorPagina": 50},
    ),
    # Vendas & NF-e
    (
        "omie_listar_pedidos_venda",
        "/api/v1/produtos/pedido/",
        "ListarPedidos",
        "Lista pedidos de venda. Aceita pagina, registros_por_pagina, etapa, filtrar_por_data_de/ate em param.",
        {"pagina": 1, "registros_por_pagina": 50, "apenas_importado_api": "N"},
    ),
    (
        "omie_consultar_pedido_venda",
        "/api/v1/produtos/pedido/",
        "ConsultarPedido",
        "Consulta um pedido de venda por codigo_pedido, codigo_pedido_integracao ou numero_pedido em param.",
        {},
    ),
    (
        "omie_incluir_pedido_venda",
        "/api/v1/produtos/pedido/",
        "IncluirPedido",
        "Inclui um novo pedido de venda no Omie (consulte o schema com omie_get_method_schema se precisar dos campos exatos).",
        {},
    ),
    (
        "omie_listar_nf",
        "/api/v1/produtos/nfconsultar/",
        "ListarNF",
        "Lista Notas Fiscais (NF-e). Aceita pagina, registros_por_pagina, dEmiInicial/dEmiFinal em param.",
        {"pagina": 1, "registros_por_pagina": 50},
    ),
    # Finanças
    (
        "omie_resumo_financas",
        "/api/v1/financas/resumo/",
        "ObterResumoFinancas",
        "Obtém o resumo financeiro do dia (contas correntes, a pagar, a receber). Informe dDia (dd/mm/aaaa) em param.",
        {},
    ),
    (
        "omie_listar_contas_pagar",
        "/api/v1/financas/contapagar/",
        "ListarContasPagar",
        "Lista lançamentos de contas a pagar. Aceita pagina, registros_por_pagina, filtrar_por_data_de/ate em param.",
        {"pagina": 1, "registros_por_pagina": 50},
    ),
    (
        "omie_incluir_conta_pagar",
        "/api/v1/financas/contapagar/",
        "IncluirContaPagar",
        "Inclui um lançamento de conta a pagar no Omie (informe os campos em param).",
        {},
    ),
    (
        "omie_listar_contas_receber",
        "/api/v1/financas/contareceber/",
        "ListarContasReceber",
        "Lista lançamentos de contas a receber. Aceita pagina, registros_por_pagina, filtrar_por_data_de/ate em param.",
        {"pagina": 1, "registros_por_pagina": 50},
    ),
    (
        "omie_incluir_conta_receber",
        "/api/v1/financas/contareceber/",
        "IncluirContaReceber",
        "Inclui um lançamento de conta a receber no Omie (informe os campos em param).",
        {},
    ),
    (
        "omie_listar_contas_correntes",
        "/api/v1/geral/contacorrente/",
        "ListarContasCorrentes",
        "Lista contas correntes cadastradas no Omie.",
        {"pagina": 1, "registros_por_pagina": 50},
    ),
    # Serviços & OS
    (
        "omie_listar_os",
        "/api/v1/servicos/os/",
        "ListarOS",
        "Lista Ordens de Serviço (OS). Aceita pagina, registros_por_pagina, filtrar_por_data_de/ate em param.",
        {"pagina": 1, "registros_por_pagina": 50},
    ),
    (
        "omie_incluir_os",
        "/api/v1/servicos/os/",
        "IncluirOS",
        "Inclui uma nova Ordem de Serviço (OS) no Omie.",
        {},
    ),
    # CRM
    (
        "omie_listar_oportunidades",
        "/api/v1/crm/oportunidades/",
        "ListarOportunidades",
        "Lista oportunidades do CRM Omie. Aceita pagina e registros_por_pagina em param.",
        {"pagina": 1, "registros_por_pagina": 50},
    ),
]

_SHORTCUTS_BY_NAME = {s[0]: s for s in SHORTCUTS}


def _normalize_path(path: str) -> str:
    p = (path or "").strip()
    if p.startswith("http://") or p.startswith("https://"):
        idx = p.find("/api/")
        if idx != -1:
            p = p[idx:]
    p = "/" + p.strip("/") + "/"
    return p


def _text(data: Any) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=json.dumps(data, ensure_ascii=False, indent=2))]
    )


class OmieMCPServer:
    def __init__(self, storage: StorageManager):
        self.storage = storage
        self.client = OmieClient(storage)
        self._tools_cache: List[types.Tool] = self._build_tools_list()

    def _build_tools_list(self) -> List[types.Tool]:
        categories = sorted({c for s in SERVICES for c in s.get("categories", [s.get("category", "")]) if c})
        tools: List[types.Tool] = [
            types.Tool(
                name="omie_list_services",
                description=(
                    f"[Omie Catálogo] Lista os {len(SERVICES)} serviços oficiais da API Omie "
                    f"({CATALOG.get('total_methods', 0)} métodos no total). Filtre por categoria ou busca por palavra-chave "
                    "para descobrir o `service_path` a usar em `omie_get_service_methods` ou `omie_call`."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "category": {
                            "type": "string",
                            "description": f"Filtro opcional por categoria. Categorias: {', '.join(categories)}.",
                        },
                        "search": {
                            "type": "string",
                            "description": "Palavra-chave opcional para buscar por nome, descrição, path ou método (ex.: 'boleto', 'nfse', 'estoque', 'fornecedor').",
                        },
                    },
                },
            ),
            types.Tool(
                name="omie_get_service_methods",
                description=(
                    "[Omie Catálogo] Lista todos os métodos (`call`) disponíveis para um serviço Omie "
                    "(ex.: '/api/v1/geral/clientes/', '/api/v1/financas/contapagar/')."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "service_path": {
                            "type": "string",
                            "description": "Caminho do serviço (ex.: '/api/v1/geral/clientes/').",
                        },
                    },
                    "required": ["service_path"],
                },
            ),
            types.Tool(
                name="omie_get_method_schema",
                description=(
                    "[Omie Catálogo] Retorna os campos esperados no objeto `param` de qualquer método (`call`) "
                    "da API Omie, extraídos diretamente do WSDL oficial."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "service_path": {
                            "type": "string",
                            "description": "Caminho do serviço (ex.: '/api/v1/produtos/pedido/').",
                        },
                        "call": {
                            "type": "string",
                            "description": "Nome exato do método (ex.: 'IncluirPedido', 'ListarClientes').",
                        },
                    },
                    "required": ["service_path", "call"],
                },
            ),
            types.Tool(
                name="omie_call",
                description=(
                    "[Omie] Executa QUALQUER método (`call`) em QUALQUER um dos 138 serviços da API Omie. "
                    "Injeta app_key/app_secret automaticamente e aplica rate limit. Use `omie_list_services` e "
                    "`omie_get_method_schema` para descobrir o path, o método e os campos de `param`."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "service_path": {
                            "type": "string",
                            "description": "Caminho do serviço Omie (ex.: '/api/v1/geral/clientes/').",
                        },
                        "call": {
                            "type": "string",
                            "description": "Nome do método da API (ex.: 'ListarClientes').",
                        },
                        "param": {
                            "type": "object",
                            "description": "Objeto de parâmetros enviado dentro de `param: [param]`.",
                        },
                    },
                    "required": ["service_path", "call"],
                },
            ),
        ]

        for name, svc_path, call_name, desc, _defaults in SHORTCUTS:
            tools.append(
                types.Tool(
                    name=name,
                    description=f"[Omie] {desc} ({svc_path} -> {call_name})",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "param": {
                                "type": "object",
                                "description": f"Parâmetros do método `{call_name}` (opcional para listagens padrão).",
                            }
                        },
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

            if tool_name == "omie_list_services":
                cat_filter = (args.get("category") or "").strip().lower()
                search = (args.get("search") or "").strip().lower()
                matches = []
                for s in SERVICES:
                    cats = [c.lower() for c in s.get("categories", [s.get("category", "")])]
                    if cat_filter and not any(cat_filter in c for c in cats):
                        continue
                    if search:
                        haystack = " ".join([
                            s.get("name", ""),
                            " ".join(s.get("aliases", [])),
                            s.get("description", ""),
                            s.get("path", ""),
                            " ".join(s.get("methods", {}).keys()),
                        ]).lower()
                        if search not in haystack:
                            continue
                    matches.append({
                        "name": s["name"],
                        "categories": s.get("categories", [s.get("category", "")]),
                        "path": s["path"],
                        "description": s.get("description", ""),
                        "methods_count": len(s.get("methods", {})),
                        "methods": list(s.get("methods", {}).keys()),
                    })
                return _text({
                    "total_matched": len(matches),
                    "services": matches,
                })

            if tool_name == "omie_get_service_methods":
                norm = _normalize_path(args["service_path"])
                svc = SERVICES_BY_PATH.get(norm)
                if not svc:
                    raise ValueError(
                        f"Serviço '{norm}' não encontrado no catálogo Omie. Use `omie_list_services` para buscar o path exato."
                    )
                return _text({
                    "name": svc["name"],
                    "path": svc["path"],
                    "description": svc.get("description", ""),
                    "methods": list(svc.get("methods", {}).keys()),
                })

            if tool_name == "omie_get_method_schema":
                norm = _normalize_path(args["service_path"])
                call_name = (args.get("call") or "").strip()
                svc = SERVICES_BY_PATH.get(norm)
                if not svc:
                    raise ValueError(f"Serviço '{norm}' não encontrado no catálogo Omie.")
                methods = svc.get("methods", {})
                if call_name not in methods:
                    # Tenta busca case-insensitive
                    ci = {k.lower(): k for k in methods}
                    if call_name.lower() in ci:
                        call_name = ci[call_name.lower()]
                    else:
                        raise ValueError(
                            f"Método '{call_name}' não encontrado em '{norm}'. Disponíveis: {list(methods.keys())}"
                        )
                return _text({
                    "service_path": norm,
                    "call": call_name,
                    "param_schema": methods[call_name],
                })

            if tool_name == "omie_call":
                norm = _normalize_path(args["service_path"])
                call_name = (args.get("call") or "").strip()
                if not call_name:
                    raise ValueError("Parâmetro obrigatório 'call' ausente.")
                param = args.get("param")
                if param is not None and not isinstance(param, dict):
                    raise ValueError("O campo 'param' deve ser um objeto JSON.")
                data = await self.client.call(tenant_id, norm, call_name, param=param or {})
                return _text(data)

            shortcut = _SHORTCUTS_BY_NAME.get(tool_name)
            if not shortcut:
                raise ValueError(f"Tool desconhecida: '{tool_name}'")

            _name, svc_path, call_name, _desc, defaults = shortcut
            user_param = args.get("param")
            if user_param is None:
                # Também aceita parâmetros passados diretamente na raiz caso o agente não envolva em `param`
                extra = {k: v for k, v in args.items() if k != "param"}
                user_param = extra if extra else {}
            if not isinstance(user_param, dict):
                raise ValueError("O campo 'param' deve ser um objeto JSON.")

            merged_param = dict(defaults)
            merged_param.update(user_param)
            data = await self.client.call(tenant_id, svc_path, call_name, param=merged_param)
            return _text(data)

        except Exception as e:
            logger.error(f"[Omie MCP] Erro em tools/call '{tool_name}' ({tenant_id}): {e}")
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=f"Erro ao executar '{tool_name}': {e}")],
                isError=True,
            )
