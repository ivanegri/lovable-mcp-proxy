# Lovable MCP – Base de Conhecimento de Ferramentas

> Documento de referência para o agente que consome o **Lovable MCP Proxy Bridge**.
> Gerado a partir da resposta real de `GET /tools` (41 ferramentas). Esquemas completos em `tools_raw.json`.

---

## 1. Como chamar as ferramentas (via proxy)

| Ação | Método | URL | Body |
|---|---|---|---|
| Listar ferramentas | `GET` | `http://216.22.5.204:8899/tools` | – |
| Executar ferramenta | `POST` | `http://216.22.5.204:8899/tools/{tool_name}` | JSON com os argumentos |
| Status do proxy | `GET` | `http://216.22.5.204:8899/health` | – |
| Reconectar ao Lovable | `POST` | `http://216.22.5.204:8899/reconnect` | – |

**Headers obrigatórios** (exceto `/health`):
```
x-api-key: <SUA_INTERNAL_API_KEY>
Content-Type: application/json
```

**Exemplo:**
```bash
curl -X POST http://216.22.5.204:8899/tools/list_projects \
  -H "x-api-key: <SUA_INTERNAL_API_KEY>" \
  -H "Content-Type: application/json" \
  -d '{"workspace_id": "ws_123", "query": "landing", "limit": 10}'
```

**Formato da resposta:**
```json
{
  "result": {
    "content": [{ "type": "text", "text": "..." }],
    "structured_content": { ... },
    "is_error": false
  }
}
```
- Leia primeiro `structured_content` (JSON estruturado); se ausente, faça parse de `content[0].text`.
- `is_error: true` → a ferramenta falhou; a mensagem está em `content[0].text`.

**Códigos HTTP do proxy:**
| Código | Significado |
|---|---|
| 200 | OK |
| 401 | `x-api-key` ausente ou inválida |
| 500 | Erro ao executar a ferramenta (veja `detail`) |
| 502 | Proxy sem conexão com o Lovable → chamar `POST /reconnect` |

> [!IMPORTANT]
> Ferramentas como `send_message`, `create_project (wait=true)`, `respond_to_approval` e `remix_project` podem levar **até 600 s**.
> Configure o timeout HTTP do agente para **≥ 620 000 ms**, ou use `wait: false` + polling com `get_message`.

---

## 2. Conceitos essenciais

- **Workspace** → contém projetos, knowledge, skills e conectores. Quase tudo exige `workspace_id`.
- **Project** → app full-stack TypeScript (React + Tailwind + shadcn/ui). Identificado por `project_id`.
- **Message** → instrução em linguagem natural ao agente de IA do Lovable, que escreve o código.
- **Status do projeto** (`in_progress` / `completed` / `failed`) refere-se apenas à criação do scaffold, **não** ao fim do trabalho do agente. Para saber se o agente terminou use `project.agentFinished` (em `get_project`) ou `response.status` (em `get_message`).
- **awaiting_input** → o agente pausou esperando decisão humana (aprovação de ferramenta ou check-in de créditos).
- **Knowledge** → instruções customizadas (máx. 10 000 caracteres) que guiam o agente do Lovable, no nível de workspace ou de projeto.

---

## 3. Fluxos recomendados

### 3.1 Descobrir contexto (sempre primeiro)
1. `get_me` → perfil + até 100 workspaces.
2. `list_workspaces` (se `has_more`) → obter `workspace_id`.
3. `list_projects` com `workspace_id` → obter `project_id`.

### 3.2 Criar um app novo
1. (Opcional) `list_template_projects` / `list_design_systems`.
2. `create_project` com `workspace_id` + `initial_message` (descrição detalhada do app).
3. Guardar `projectId` e `message_id` retornados.
4. Polling com `get_message` até `response.status` ∈ `completed | stopped | error | awaiting_input`.
5. `get_project` → `preview_url`, `editor_url`, screenshot.
6. (Opcional) `deploy_project` → URL pública em `*.lovable.app`.

### 3.3 Alterar um app existente
1. `send_message` com `project_id` + `message` (descreva **o que** quer, não **como** implementar).
   - Use `plan_mode: true` para discutir/planejar sem editar código.
2. Ver o que mudou: `get_diff` com `message_id`.
3. Se a resposta vier `awaiting_input` → ver seção 3.5.

### 3.4 Execução assíncrona (recomendado para agentes com timeout curto)
1. `send_message` com `"wait": false` → retorna `message_id` e `thread_id`.
2. Repetir `get_message` (`project_id`, `message_id`, `thread_id`) a cada 10–20 s.
3. Terminal quando `response.status` = `completed`, `stopped`, `error` ou `awaiting_input`.

### 3.5 Tratando pausas (`awaiting_input`)
- Se `awaiting_input.requires_secure_form = true` → enviar o usuário para `editor_url`. **Nunca** coletar credenciais no chat.
- Se for aprovação de ferramenta → mostrar ao humano `awaiting_input.tool_id` e `params`, pedir decisão e chamar `respond_to_approval` com:
  - `message_id` = o `message_id` **de topo** do `send_message`/`create_project` que pausou;
  - `event_id` = `awaiting_input.event_id`;
  - `decision` = `approved` ou `rejected`.
- Se não houver objeto `awaiting_input` → é check-in de créditos; só pode ser respondido no editor Lovable.
- **Nunca** decida sozinho e **nunca** responda pausa com `send_message` (isso a cancela/substitui).
- Segredos (secrets, chaves Stripe) só podem ser aprovados no editor Lovable.

### 3.6 Banco de dados
1. `get_database_status` → verificar se está habilitado.
2. `enable_database` se necessário (30–60 s, uma única vez).
3. `query_database` com SQL. Prefira `SELECT`; escritas afetam produção permanentemente.

### 3.7 Enviar arquivos (imagens, mockups)
1. `get_file_upload_url` com `file_name` (+ `content_type`) → `upload_url` e `file_id`.
2. `PUT` do conteúdo binário em `upload_url` (fora do proxy).
3. Passar `files: [{"file_id": "..."}]` em `send_message` ou `create_project`.

---

## 4. Referência completa das ferramentas

Legenda: **(obrig.)** = parâmetro obrigatório · 🔒 = somente leitura · ⚠️ = destrutiva/irreversível

### 4.1 Conta e Workspaces

#### `get_me` 🔒
Perfil do usuário autenticado e os primeiros 100 workspaces. Se `has_more = true`, use `list_workspaces`.
- Sem parâmetros.
```json
{}
```

#### `list_workspaces` 🔒
Lista workspaces do usuário (resumo). Use para descobrir `workspace_id`.
| Parâmetro | Tipo | Descrição |
|---|---|---|
| `limit` | number | Padrão 50, máx. 100 |
| `offset` | number | Padrão 0 (ignorado se `cursor`) |
| `cursor` | string | Cursor de paginação |

#### `get_workspace` 🔒
Detalhes: nome, plano, nº de projetos e papel do usuário.
| Parâmetro | Tipo | Descrição |
|---|---|---|
| `workspace_id` **(obrig.)** | string | ID do workspace |

---

### 4.2 Projetos – descoberta

#### `list_projects` 🔒
Busca/lista projetos (busca fuzzy por nome, descrição IA, criador ou e-mail).
| Parâmetro | Tipo | Descrição |
|---|---|---|
| `workspace_id` **(obrig.)** | string | ID do workspace |
| `query` | string | Termos de busca (todos devem casar) |
| `visibility` | string | CSV: `restricted`, `workspace_edit`, `workspace_view` |
| `publish_status` | enum | `published`, `workspace`, `public`, `not_published` |
| `folder_id` | string | Filtrar por pasta |
| `viewed_by_me` | boolean | Só projetos vistos pelo usuário |
| `limit` | number | Padrão 50, máx. 100 |
| `cursor` | string | Paginação |
```json
{ "workspace_id": "ws_123", "query": "dashboard vendas", "limit": 20 }
```

#### `list_design_systems` 🔒
Design systems (componentes, tokens, estilos) reutilizáveis do workspace.
| `workspace_id` **(obrig.)** | string |
|---|---|

#### `list_template_projects` 🔒
Templates disponíveis para usar como `template_project_id` no `create_project`.
| `workspace_id` **(obrig.)** | string |
|---|---|

#### `get_project` 🔒
Detalhes do projeto: `editor_url`, `preview_url`, `latest_commit_sha`, screenshot (se `completed`) e `project.agentFinished`.
| `project_id` **(obrig.)** | string |
|---|---|

#### `render_project_widget` 🔒
Dados de bootstrap do widget de progresso. Chamar somente após `create_project`.
| `project_id` **(obrig.)** | string |
|---|---|

---

### 4.3 Projetos – criação e gestão

#### `create_project`
Cria um novo projeto Lovable e envia a primeira mensagem ao agente.
| Parâmetro | Tipo | Descrição |
|---|---|---|
| `initial_message` **(obrig.)** | string | Descrição do que construir |
| `workspace_id` | string | Opcional se houver só 1 workspace elegível; caso contrário retorna `available_workspaces` |
| `files` | array | `[{ "file_id": "...", "file_name"?, "mime_type"?, "type"? }]` |
| `template_project_id` | string | Template (de `list_template_projects`) |
| `design_systems` | array | `[{ "project_id": "<id de list_design_systems>" }]` (só o 1º é aplicado) |
| `sandbox_template` | string | Avançado – só se o usuário pedir explicitamente |
| `wait` | boolean | Padrão `false` (retorna na hora com `projectId`) |
| `timeout_seconds` | integer | 1–600, padrão 600 |
```json
{
  "workspace_id": "ws_123",
  "initial_message": "Crie uma landing page para uma clínica odontológica com hero, serviços, depoimentos e formulário de agendamento. Visual moderno em tons de azul.",
  "wait": false
}
```

#### `initiate_project` *(obsoleta)*
Alias legado de `create_project`. **Não usar.**
| `prompt` **(obrig.)** | string (100 a 100 000 caracteres) |
|---|---|

#### `remix_project`
Faz fork de um projeto existente para um workspace. Aguarda a conclusão.
| Parâmetro | Tipo | Descrição |
|---|---|---|
| `project_id` **(obrig.)** | string | Projeto de origem |
| `workspace_id` **(obrig.)** | string | Workspace de destino |
| `project_name` | string | Nome do novo projeto |
| `include_history` | boolean | Copiar histórico de chat (padrão `false`) |
| `include_custom_knowledge` | boolean | Copiar knowledge (padrão `false`) |
| `timeout_seconds` | integer | 1–600, padrão 600 |

#### `deploy_project`
Publica o projeto em produção (`*.lovable.app`). Retorna a URL pública.
| Parâmetro | Tipo | Descrição |
|---|---|---|
| `project_id` **(obrig.)** | string | ID do projeto |
| `name` | string | Slug da URL publicada |
```json
{ "project_id": "proj_abc", "name": "clinica-sorriso" }
```

#### `set_project_visibility`
Define quem pode abrir o projeto no editor.
| Parâmetro | Tipo | Descrição |
|---|---|---|
| `project_id` **(obrig.)** | string | |
| `visibility` **(obrig.)** | enum | `workspace_edit` · `workspace_view` (Business+) · `restricted` (Business+). (`draft`, `private`, `public` são obsoletos) |

#### `set_folder_visibility`
Visibilidade de pasta (propaga para os projetos dela). Pastas aninhadas não podem mudar.
| Parâmetro | Tipo | Descrição |
|---|---|---|
| `workspace_id` **(obrig.)** | string | |
| `folder_id` **(obrig.)** | string | |
| `visibility` **(obrig.)** | enum | `personal` (Business/Enterprise) · `workspace` |

#### `move_projects_to_folder`
Move projetos para uma pasta (remove associações anteriores; pode alterar visibilidade).
| Parâmetro | Tipo | Descrição |
|---|---|---|
| `workspace_id` **(obrig.)** | string | |
| `folder_id` **(obrig.)** | string | Pasta destino |
| `project_ids` **(obrig.)** | string[] | 1 a 30 IDs |

---

### 4.4 Chat com o agente Lovable

#### `send_message`
Envia instrução ao agente de IA do projeto (escreve código, instala pacotes, cria páginas, configura auth, integrações, corrige bugs).
| Parâmetro | Tipo | Descrição |
|---|---|---|
| `project_id` **(obrig.)** | string | |
| `message` **(obrig.)** | string | 1 a 100 000 caracteres, linguagem natural |
| `wait` | boolean | Padrão `true` |
| `timeout_seconds` | integer | 1–600, padrão 600 |
| `plan_mode` | boolean | Só planeja, sem editar código (padrão `false`) |
| `files` | array | `[{ "file_id": "..." }]` – imagens/designs |
```json
{
  "project_id": "proj_abc",
  "message": "Adicione uma página de login com e-mail e senha usando Supabase Auth.",
  "wait": false
}
```
Retorno: resposta do agente, log de atividade, edits, `message_id`, `thread_id`, `commit_sha`. Status `awaiting_input` = pausa humana (ver 3.5).

#### `get_message` 🔒
Status/conteúdo de uma mensagem. Use para polling.
| Parâmetro | Tipo | Descrição |
|---|---|---|
| `project_id` **(obrig.)** | string | |
| `message_id` **(obrig.)** | string | De `send_message` |
| `thread_id` | string | De `send_message` |

Interpretação:
- `status` de topo (`queued`/`accepted`/`running`) nunca é terminal – **ignore para decidir fim**.
- `response` ausente → agente ainda trabalhando.
- `response.status`: `completed` / `stopped` / `error` = terminal; `awaiting_input` = pausa humana.
- `queued` + `queue_pause_reason: "hitl_tool"` → mensagem rodará após a pausa ser respondida.

#### `respond_to_approval` ⚠️
Responde a uma aprovação pendente **após decisão humana**.
| Parâmetro | Tipo | Descrição |
|---|---|---|
| `project_id` **(obrig.)** | string | |
| `message_id` **(obrig.)** | string | `message_id` de topo da chamada que pausou (não `agent_response.message_id`) |
| `event_id` **(obrig.)** | string | `awaiting_input.event_id` |
| `decision` **(obrig.)** | enum | `approved` · `rejected` |
| `user_input` | object \| string | Aprovação: valores do `awaiting_input.input_schema`; rejeição: motivo. **Nunca** incluir segredos |
| `wait` | boolean | Padrão `true` |
| `timeout_seconds` | integer | 1–600 |

#### `list_messages` 🔒
Mensagens recentes do projeto (mais novas primeiro). Útil para achar `message_id`.
| Parâmetro | Tipo | Descrição |
|---|---|---|
| `project_id` **(obrig.)** | string | |
| `limit` | number | 1–100, padrão 30 |
| `cursor` | string | `pagination.next_cursor` |

---

### 4.5 Código e histórico

#### `get_diff` 🔒
Diff unificado de uma mensagem ou commit.
| Parâmetro | Tipo | Descrição |
|---|---|---|
| `project_id` **(obrig.)** | string | |
| `message_id` | string | Diff do que o agente mudou nessa mensagem |
| `sha` | string | Commit específico (alternativa) |
| `base_sha` | string | Base de comparação (padrão: commit pai) |

#### `list_files` 🔒
Lista arquivos do repositório (paginado).
| Parâmetro | Tipo | Descrição |
|---|---|---|
| `project_id` **(obrig.)** | string | |
| `ref` | string | Commit SHA, branch ou tag |
| `limit` | number | Padrão 50, máx. 100 |
| `cursor` | string | Paginação |

#### `read_file` 🔒
Conteúdo bruto de um arquivo.
| Parâmetro | Tipo | Descrição |
|---|---|---|
| `project_id` **(obrig.)** | string | |
| `path` **(obrig.)** | string | Ex.: `src/App.tsx` |
| `ref` | string | Commit SHA, branch ou tag |
```json
{ "project_id": "proj_abc", "path": "src/App.tsx" }
```

#### `list_edits` 🔒
Histórico de edições (cada uma com `commit_sha` para `get_diff`).
| Parâmetro | Tipo | Descrição |
|---|---|---|
| `project_id` **(obrig.)** | string | |
| `limit` | number | 1–50, padrão 20 |
| `cursor` | string | Paginação |
| `before` | string | Obsoleto – use `cursor` |

#### `get_file_upload_url` 🔒
URL pré-assinada para upload de arquivo (ver fluxo 3.7).
| Parâmetro | Tipo | Descrição |
|---|---|---|
| `file_name` **(obrig.)** | string | |
| `content_type` | string | MIME type (ex.: `image/png`) |

---

### 4.6 Knowledge (instruções customizadas)

#### `get_workspace_knowledge` 🔒
| `workspace_id` **(obrig.)** | string |
|---|---|

#### `set_workspace_knowledge` ⚠️
**Substitui** todo o knowledge do workspace. Leia antes com `get_workspace_knowledge` se quiser preservar.
| Parâmetro | Tipo | Descrição |
|---|---|---|
| `workspace_id` **(obrig.)** | string | |
| `content` **(obrig.)** | string | Markdown, máx. 10 000 caracteres (`""` limpa) |

#### `get_project_knowledge` 🔒
| `project_id` **(obrig.)** | string |
|---|---|

#### `set_project_knowledge` ⚠️
**Substitui** todo o knowledge do projeto.
| Parâmetro | Tipo | Descrição |
|---|---|---|
| `project_id` **(obrig.)** | string | |
| `content` **(obrig.)** | string | Markdown, máx. 10 000 caracteres |
```json
{ "project_id": "proj_abc", "content": "- Use sempre português do Brasil na UI\n- Paleta: #0F172A, #38BDF8\n- Auth via Supabase" }
```

---

### 4.7 Skills do workspace
Skills = instruções reutilizáveis em `skills/{nome}/SKILL.md`. Criar/editar/excluir exige papel **admin** ou **owner**.

#### `list_workspace_skills` 🔒
| Parâmetro | Tipo | Descrição |
|---|---|---|
| `workspace_id` **(obrig.)** | string | |
| `include_markdown` | boolean | Incluir conteúdo completo (padrão `false`) |

#### `get_workspace_skill` 🔒
| Parâmetro | Tipo |
|---|---|
| `workspace_id` **(obrig.)** | string |
| `skill_name` **(obrig.)** | string |

#### `create_workspace_skill`
| Parâmetro | Tipo | Descrição |
|---|---|---|
| `workspace_id` **(obrig.)** | string | |
| `skill_name` **(obrig.)** | string | Igual ao `name` do frontmatter |
| `markdown` **(obrig.)** | string | SKILL.md completo com frontmatter |
```json
{
  "workspace_id": "ws_123",
  "skill_name": "form-validation",
  "markdown": "---\nname: form-validation\ndescription: Padrão de validação de formulários com zod\n---\n\n# Form Validation\nUse react-hook-form + zod..."
}
```

#### `update_workspace_skill` ⚠️
Substitui o SKILL.md inteiro. Mesmos parâmetros de `create_workspace_skill`.

#### `delete_workspace_skill` ⚠️
Remove a skill (irreversível).
| `workspace_id` **(obrig.)** | `skill_name` **(obrig.)** |
|---|---|

---

### 4.8 Banco de dados (Supabase / PostgreSQL)

#### `get_database_status` 🔒
| `project_id` **(obrig.)** | string |
|---|---|

#### `enable_database`
Provisiona o banco (30–60 s, só uma vez).
| `project_id` **(obrig.)** | string |
|---|---|

#### `query_database` ⚠️
Executa SQL (SELECT, INSERT, UPDATE, DELETE, DDL). Retorna linhas em JSON.
| Parâmetro | Tipo |
|---|---|
| `project_id` **(obrig.)** | string |
| `sql` **(obrig.)** | string |
```json
{ "project_id": "proj_abc", "sql": "SELECT id, email, created_at FROM profiles ORDER BY created_at DESC LIMIT 10;" }
```

---

### 4.9 Conectores (integrações)

#### `list_connectors` 🔒
Catálogo de conectores do workspace (standard/OAuth, seamless, MCP) com status ativo/inativo.
| `workspace_id` **(obrig.)** | string |
|---|---|

#### `list_custom_connectors` 🔒
Conectores já adicionados ao workspace (usáveis pelo agente).
| `workspace_id` **(obrig.)** | string |
|---|---|

#### `add_connector`
Retorna o **link do dashboard** para o usuário adicionar o conector (não adiciona programaticamente).
| Parâmetro | Tipo | Descrição |
|---|---|---|
| `connector_id` | string | Ex.: `linear`, `notion`. Omitir → página geral |

---

### 4.10 Analytics (projetos publicados)

#### `get_project_analytics` 🔒
Visitantes, pageviews, bounce rate, duração de sessão, quebras por página/origem/dispositivo/país.
| Parâmetro | Tipo | Descrição |
|---|---|---|
| `project_id` **(obrig.)** | string | |
| `start_date` **(obrig.)** | string | RFC 3339, ex.: `2026-10-01T00:00:00Z` |
| `end_date` **(obrig.)** | string | RFC 3339 |
| `granularity` | enum | `hourly` · `daily` (padrão) |

#### `get_project_analytics_trend` 🔒
Visitantes em tempo real (intervalos de 5 min nos últimos 30 min).
| `project_id` **(obrig.)** | string |
|---|---|

---

## 5. Regras de ouro para o agente

1. **Descubra IDs antes de agir**: `get_me` → `list_workspaces` → `list_projects`. Nunca invente IDs.
2. **Mensagens ao Lovable em linguagem natural**, descrevendo o resultado desejado, não a implementação.
3. **Operações longas**: prefira `wait: false` + polling com `get_message`.
4. **Nunca responda pausas sozinho**: `respond_to_approval` só com decisão explícita do usuário.
5. **Nunca colete segredos no chat**: redirecione para `editor_url`.
6. **Antes de sobrescrever** knowledge ou skills, leia o conteúdo atual.
7. **Banco de dados**: prefira `SELECT`; confirme com o usuário antes de `INSERT/UPDATE/DELETE/DROP`.
8. **`status: completed` do projeto ≠ agente terminou**: verifique `agentFinished` / `response.status`.
9. **Erro 502 do proxy** → chamar `POST /reconnect` e tentar novamente uma vez.
10. **Conectores** só podem ser adicionados pelo dashboard: use `add_connector` e entregue o link ao usuário.
