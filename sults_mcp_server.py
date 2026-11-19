"""
Sults MCP Server (local).

Carrega as especificações OpenAPI oficiais da Sults (API v1 e API v2) em `sults_specs/`,
resolve `$ref` e gera automaticamente todas as tools MCP (27 v1 + 61 v2 = 88 tools), mais a
tool genérica `sults_request`.

Mesmo contrato do YungasMCPServer / MetaAdsMCPServer:
    list_tools(tenant_id) / list_resources(tenant_id) / call_tool(tenant_id, tool_name, arguments)
"""

import os
import json
import base64
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from mcp import types

from storage import StorageManager
from sults_client import SultsClient

logger = logging.getLogger("mcp-proxy.sults_mcp")

SPECS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sults_specs")
SPEC_FILES = (
    ("v1", os.path.join(SPECS_DIR, "openapi-v1.json")),
    ("v2", os.path.join(SPECS_DIR, "openapi-v2.json")),
)
HTTP_METHODS = ("get", "post", "put", "patch", "delete")
MAX_BODY_SCHEMA_DEPTH = 3


@dataclass
class SultsEndpoint:
    name: str
    version: str
    method: str
    path: str
    description: str
    input_schema: Dict[str, Any]
    path_params: List[str] = field(default_factory=list)
    query_params: List[str] = field(default_factory=list)
    has_body: bool = False
    is_multipart_upload: bool = False


def _resolve_ref(spec: Dict[str, Any], ref: str) -> Any:
    """Resolve um ponteiro JSON local (#/components/...)."""
    if not ref.startswith("#/"):
        return {}
    node: Any = spec
    for part in ref[2:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return {}
    return node


def _simplify_schema(
    spec: Dict[str, Any],
    schema: Any,
    depth: int = 0,
    max_depth: int = MAX_BODY_SCHEMA_DEPTH,
    seen_refs: Optional[Set[str]] = None,
) -> Dict[str, Any]:
    """Resolve `$ref` e limita a profundidade do JSON Schema para manter tools/list compacto."""
    if not isinstance(schema, dict):
        return {"type": "object"}

    seen = set(seen_refs or ())
    if "$ref" in schema:
        ref = schema["$ref"]
        if ref in seen or depth >= max_depth:
            return {"type": "object", "description": f"Referência {ref.split('/')[-1]}"}
        seen.add(ref)
        target = _resolve_ref(spec, ref)
        return _simplify_schema(spec, target, depth=depth, max_depth=max_depth, seen_refs=seen)

    for comb in ("allOf", "oneOf", "anyOf"):
        if comb in schema and isinstance(schema[comb], list) and schema[comb]:
            if comb == "allOf":
                merged_props: Dict[str, Any] = {}
                merged_req: List[str] = []
                for sub in schema[comb]:
                    resolved = _simplify_schema(spec, sub, depth=depth, max_depth=max_depth, seen_refs=seen)
                    merged_props.update(resolved.get("properties", {}))
                    for r in resolved.get("required", []):
                        if r not in merged_req:
                            merged_req.append(r)
                out: Dict[str, Any] = {"type": "object"}
                if merged_props:
                    out["properties"] = merged_props
                if merged_req:
                    out["required"] = merged_req
                return out
            return _simplify_schema(spec, schema[comb][0], depth=depth, max_depth=max_depth, seen_refs=seen)

    result: Dict[str, Any] = {}
    schema_type = schema.get("type")
    if schema_type:
        result["type"] = schema_type
    elif "properties" in schema:
        result["type"] = "object"
    elif "items" in schema:
        result["type"] = "array"

    if "description" in schema and schema["description"]:
        result["description"] = str(schema["description"]).strip()
    if "enum" in schema and isinstance(schema["enum"], list):
        result["enum"] = schema["enum"]
    if "format" in schema and schema["format"]:
        result["format"] = schema["format"]
    if "default" in schema:
        result["default"] = schema["default"]

    if result.get("type") == "object" and "properties" in schema and isinstance(schema["properties"], dict):
        if depth < max_depth:
            props: Dict[str, Any] = {}
            for prop_name, prop_schema in schema["properties"].items():
                props[prop_name] = _simplify_schema(
                    spec, prop_schema, depth=depth + 1, max_depth=max_depth, seen_refs=seen
                )
            if props:
                result["properties"] = props
            if "required" in schema and isinstance(schema["required"], list):
                req = [r for r in schema["required"] if r in props]
                if req:
                    result["required"] = req
    elif result.get("type") == "array" and "items" in schema:
        if depth < max_depth:
            result["items"] = _simplify_schema(
                spec, schema["items"], depth=depth + 1, max_depth=max_depth, seen_refs=seen
            )
        else:
            result["items"] = {"type": "object"}

    return result or {"type": "object"}


def _build_endpoints_from_specs() -> List[SultsEndpoint]:
    endpoints: List[SultsEndpoint] = []

    for version, spec_path in SPEC_FILES:
        if not os.path.exists(spec_path):
            logger.warning(f"[Sults MCP] Spec não encontrada: {spec_path}")
            continue
        with open(spec_path, "r", encoding="utf-8") as f:
            spec = json.load(f)

        for path, path_item in spec.get("paths", {}).items():
            if not isinstance(path_item, dict):
                continue
            path_level_params = path_item.get("parameters") or []

            for method in HTTP_METHODS:
                op = path_item.get(method)
                if not isinstance(op, dict):
                    continue
                op_id = op.get("operationId")
                if not op_id:
                    continue

                tool_name = f"sults_{version}_{op_id}"
                summary = (op.get("summary") or "").strip()
                desc_body = (op.get("description") or "").strip()
                desc_parts = [p for p in (summary, desc_body) if p]
                desc_text = " — ".join(desc_parts) if desc_parts else f"{method.upper()} {path}"
                description = f"[Sults {version}] {desc_text} (Requer token {version}.)"

                properties: Dict[str, Any] = {}
                required: List[str] = []
                path_params: List[str] = []
                query_params: List[str] = []

                raw_params = list(path_level_params) + list(op.get("parameters") or [])
                for raw_p in raw_params:
                    p = _resolve_ref(spec, raw_p["$ref"]) if isinstance(raw_p, dict) and "$ref" in raw_p else raw_p
                    if not isinstance(p, dict):
                        continue
                    p_in = p.get("in")
                    p_name = p.get("name")
                    if not p_name or p_in not in ("path", "query"):
                        continue

                    p_schema = _simplify_schema(spec, p.get("schema") or {"type": "string"}, depth=0, max_depth=2)
                    if p.get("description") and "description" not in p_schema:
                        p_schema["description"] = str(p["description"]).strip()

                    properties[p_name] = p_schema
                    if p_in == "path":
                        if p_name not in path_params:
                            path_params.append(p_name)
                        if p_name not in required:
                            required.append(p_name)
                    elif p_in == "query":
                        if p_name not in query_params:
                            query_params.append(p_name)
                        if p.get("required") and p_name not in required:
                            required.append(p_name)

                has_body = False
                is_multipart_upload = False
                rb = op.get("requestBody")
                if isinstance(rb, dict) and "$ref" in rb:
                    rb = _resolve_ref(spec, rb["$ref"])
                if isinstance(rb, dict):
                    content = rb.get("content") or {}
                    if any("multipart/form-data" in ct for ct in content):
                        is_multipart_upload = True
                        properties["file_base64"] = {
                            "type": "string",
                            "description": "Conteúdo do arquivo codificado em Base64.",
                        }
                        properties["filename"] = {
                            "type": "string",
                            "description": "Nome do arquivo com extensão (ex.: 'documento.pdf').",
                        }
                        for req_f in ("file_base64", "filename"):
                            if req_f not in required:
                                required.append(req_f)
                    elif "application/json" in content:
                        has_body = True
                        body_schema = _simplify_schema(
                            spec,
                            content["application/json"].get("schema") or {"type": "object"},
                            depth=0,
                            max_depth=MAX_BODY_SCHEMA_DEPTH,
                        )
                        if rb.get("description") and "description" not in body_schema:
                            body_schema["description"] = str(rb["description"]).strip()
                        properties["body"] = body_schema
                        if rb.get("required") and "body" not in required:
                            required.append("body")

                input_schema: Dict[str, Any] = {
                    "type": "object",
                    "properties": properties,
                }
                if required:
                    input_schema["required"] = required

                endpoints.append(
                    SultsEndpoint(
                        name=tool_name,
                        version=version,
                        method=method.upper(),
                        path=path,
                        description=description,
                        input_schema=input_schema,
                        path_params=path_params,
                        query_params=query_params,
                        has_body=has_body,
                        is_multipart_upload=is_multipart_upload,
                    )
                )

    return endpoints


ENDPOINTS: List[SultsEndpoint] = _build_endpoints_from_specs()
_BY_NAME: Dict[str, SultsEndpoint] = {ep.name: ep for ep in ENDPOINTS}


def _text(data: Any) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=json.dumps(data, ensure_ascii=False, indent=2))]
    )


class SultsMCPServer:
    def __init__(self, storage: StorageManager):
        self.storage = storage
        self.client = SultsClient(storage)
        self._tools_cache: List[types.Tool] = self._build_tools_list()

    def _build_tools_list(self) -> List[types.Tool]:
        tools = [
            types.Tool(
                name=ep.name,
                description=ep.description,
                inputSchema=ep.input_schema,
            )
            for ep in ENDPOINTS
        ]
        tools.append(
            types.Tool(
                name="sults_request",
                description=(
                    "[Sults] Chamada genérica a qualquer endpoint da API Sults v1 ou v2 "
                    "não coberto pelas outras tools. Usa token v1 ou v2 conforme `version`."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "version": {
                            "type": "string",
                            "enum": ["v1", "v2"],
                            "description": "Versão da API Sults (define qual token usar: v1 ou v2).",
                        },
                        "method": {
                            "type": "string",
                            "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"],
                            "description": "Método HTTP.",
                        },
                        "path": {
                            "type": "string",
                            "description": "Caminho completo na API (ex.: '/v1/empresas' ou '/api/v2/chamado/ticket/list').",
                        },
                        "query": {
                            "type": "object",
                            "description": "Parâmetros de query string (opcional).",
                        },
                        "body": {
                            "type": "object",
                            "description": "Corpo JSON da requisição (opcional).",
                        },
                    },
                    "required": ["version", "method", "path"],
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
            if tool_name == "sults_request":
                data = await self.client.request(
                    tenant=tenant_id,
                    version=args["version"],
                    method=args["method"],
                    path=args["path"],
                    query=args.get("query") or {},
                    body=args.get("body"),
                )
                return _text(data)

            ep = _BY_NAME.get(tool_name)
            if not ep:
                raise ValueError(f"Tool desconhecida: '{tool_name}'")

            missing = [p for p in ep.path_params if args.get(p) is None or args.get(p) == ""]
            if missing:
                raise ValueError(f"Parâmetros de rota obrigatórios ausentes: {missing}")

            real_path = ep.path.format(**{p: str(args[p]) for p in ep.path_params})
            query = {q: args[q] for q in ep.query_params if q in args and args[q] is not None}

            if ep.is_multipart_upload:
                file_b64 = args.get("file_base64")
                filename = args.get("filename")
                if not file_b64 or not filename:
                    raise ValueError("Parâmetros obrigatórios ausentes: 'file_base64' e 'filename'.")
                try:
                    file_bytes = base64.b64decode(file_b64)
                except Exception as exc:
                    raise ValueError(f"'file_base64' inválido: {exc}")
                data = await self.client.request(
                    tenant=tenant_id,
                    version=ep.version,
                    method=ep.method,
                    path=real_path,
                    query=query,
                    files={"file": (str(filename), file_bytes)},
                )
                return _text(data)

            body = args.get("body") if ep.has_body else None
            data = await self.client.request(
                tenant=tenant_id,
                version=ep.version,
                method=ep.method,
                path=real_path,
                query=query,
                body=body,
            )
            return _text(data)
        except Exception as e:
            logger.error(f"[Sults MCP] Erro em tools/call '{tool_name}' ({tenant_id}): {e}")
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=f"Erro ao executar '{tool_name}': {e}")],
                isError=True,
            )
