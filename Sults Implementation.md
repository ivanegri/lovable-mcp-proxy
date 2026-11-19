# Integração Sults (API v1 + v2) no MCP Gateway

Adicionar o serviço **`sults`** ao gateway (`/sults/mcp` e `/sults/{tenant}/mcp`), no mesmo modelo do Yungas e do Meta: servidor MCP local, sem upstream, credenciais por tenant.

## O que a documentação mostra

A documentação (Mintlify) publica as **especificações OpenAPI completas**. Dá para baixá-las e gerar as tools a partir delas, em vez de escrever endpoint por endpoint.

| Versão | Spec | Endpoints | Módulos |
|---|---|---|---|
| **v1** | `/api-reference/openapi.json` | **27** | Unidades (`/v1/...`), Chamados, Checklist, Compras, Implantação, Projetos, Expansão (`/api/v1/...`) |
| **v2** | `/api-reference/openapi-v2.json` | **61** | Compras (produtos, mix, pedidos), Comunicados, Chamados, Checklist (avaliações, questionários, planos de ação, equipe), Arquivos |
| **Total** | | **88** | |

- **Autenticação:** token estático no header `Authorization`, **puro, sem `Bearer`**. Não há login, refresh nem expiração automática.
- **Dois tokens independentes:** o token da v1 não funciona na v2, e vice-versa. Ambos são gerados por um administrador em *Segurança > API > Gestão de Tokens*.
- **Limite:** **2 requisições por segundo, por IP**, vale igual nas duas versões. O 429 vem como HTML, sem `Retry-After`.
- **Fora do escopo:** não existem webhooks. As páginas de Landing Pages, Make e Zapier são integrações de leads, não APIs.

> [!IMPORTANT]
> O limite é por **IP do servidor**, não por token. Todos os tenants do proxy compartilham a mesma cota de 2 req/s. Por isso o plano inclui um limitador global, para um tenant não derrubar os outros com 429.

## Abordagem: tools geradas a partir da spec

As duas specs são salvas no repositório. As tools são montadas na inicialização, sem tabela escrita à mão. Isso garante cobertura de **todos** os endpoints, com parâmetros e tipos exatos, e permite atualizar a API substituindo o arquivo JSON.

- **Nome:** `sults_v1_<operationId>` e `sults_v2_<operationId>` (ex.: `sults_v2_listarChamadosV2`). O prefixo da versão evita colisão entre operações de mesmo nome.
- **`inputSchema`:** parâmetros de path e query viram propriedades. O corpo JSON vira a propriedade `body`, com o schema da spec, com `$ref` resolvido.
- **Descrição:** summary e description da spec, mais o aviso de qual token a tool usa.
- **Upload (`POST /api/v2/attachment/upload`, multipart):** aceita `file_base64` e `filename` e monta o multipart.
- **Tool genérica `sults_request(version, method, path, query, body)`:** para qualquer caso que a spec não cubra.

## Mudanças propostas

#### [NEW] [sults_specs/openapi-v1.json e openapi-v2.json](file:///home/ivan/Documentos/Ivan/MCP_Lovable/sults_specs)
- Cópias das specs oficiais, versionadas no repositório.

#### [NEW] [sults_client.py](file:///home/ivan/Documentos/Ivan/MCP_Lovable/sults_client.py)
- Cliente httpx com `Authorization` puro, escolhendo o token v1 ou v2 conforme a versão da tool.
- **Limitador global** de ~2 req/s (espaçamento de 0,5 s entre chamadas, compartilhado entre tenants).
- 429 e 5xx: backoff exponencial de 1 s, 2 s, 4 s, 8 s.
- Erro 401 com mensagem clara: token ausente, inválido ou da gestão errada.
- Validação de credencial: um GET leve (v1: `/v1/empresas`, v2: listagem de categorias de questionário) para checar o token ao cadastrar.

#### [NEW] [sults_mcp_server.py](file:///home/ivan/Documentos/Ivan/MCP_Lovable/sults_mcp_server.py)
- Carrega as specs, resolve `$ref`, gera as 88 tools e a `sults_request`.
- Mesma interface do Yungas: `list_tools(tenant)` e `call_tool(tenant, ...)`.

#### [MODIFY] providers.py, mcp_gateway.py, storage.py, proxy.py
- Registrar `"sults"` em `PROVIDERS` e encaminhar para o servidor local.
- Incluir `sults` no status de tokens e na exclusão de tenant.
- Endpoint `POST /api/tenants/{tenant}/sults/credentials` (valida e salva `token_v1` e/ou `token_v2`), mais as rotas REST de tools e reconexão.

#### [MODIFY] [dashboard.py](file:///home/ivan/Documentos/Ivan/MCP_Lovable/dashboard.py)
- Card **"Sults MCP"** por tenant, com formulário de dois campos (Token v1 e Token v2, ambos opcionais, mas ao menos um), Ver Tools, Setup Agente e Desconectar. O card mostra quais versões estão ativas.

#### [MODIFY] Dockerfile, docker-compose.yml, .env.example, README.md
- **`COPY` dos novos módulos e da pasta `sults_specs/`** no Dockerfile. Esse erro aconteceu na Yungas e foi corrigido no commit `e75e855`.
- Variáveis de fallback para o tenant `default`: `SULTS_TOKEN_V1`, `SULTS_TOKEN_V2`.

## Perguntas em aberto

1. **Tokens:** você já tem os tokens v1 e v2, ou só um deles? Cada versão só funciona se o respectivo token estiver cadastrado.
2. **Escrita:** manter todas as tools de escrita (criar chamado, cancelar pedido, remover etc.), como na Yungas? Minha proposta é sim.
3. **Tamanho do `tools/list`:** com os schemas de corpo completos, a listagem das 88 tools fica grande. Minha proposta é limitar a profundidade dos schemas de corpo (a spec completa continua valendo na validação pela Sults). Se preferir, deixo o schema completo.

## Plano de verificação
- `py_compile` e conferência de que as 88 tools foram geradas (27 v1 + 61 v2).
- Teste local em Docker (build, `tools/list` REST e MCP nativo, erro de token ausente, validação de token inválido contra a Sults real, painel).
- Teste do limitador: 6 chamadas seguidas devem respeitar ~2 req/s.
- Com tokens reais: `sults_v1_*` de listagem de unidades e `sults_v2_*` de listagem de chamados.
