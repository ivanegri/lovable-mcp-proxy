# 🚀 MCP Multi-Tenant Bridge Hub (Lovable & Uscreen)

Gateway REST conteinerizado (Docker + FastAPI) para conectar múltiplos agentes de IA (N8N, Flowise, LangChain, Cursor, Claude ou scripts customizados) aos servidores MCP oficiais do **Lovable** (`https://mcp.lovable.dev`) e do **Uscreen** (`https://app.uscreen.tv/mcp`) de forma **multi-tenant**, segura e com **isolamento total** entre serviços e contas.

---

## 🌟 Principais Recursos

- **Multi-Serviço & Multi-Tenant**: Conecte várias contas do Lovable e várias contas do Uscreen no mesmo contêiner Docker.
- **Isolamento Total**: Se a conexão do Lovable expirar ou falhar, o Uscreen continua funcionando sem qualquer dependência (e vice-versa).
- **Painel Visual Web (1-Click OAuth)**: Acesse `http://<ip-ou-host>:8000/` no navegador para gerenciar tenants e conectar contas com apenas um clique!
- **Auto-Refresh de Tokens**: Renovação proativa em segundo plano antes da expiração usando `refresh_token` OAuth 2.1 com PKCE (Zero Downtime).
- **Chaves de API Isoladas por Tenant**: Cada tenant possui sua própria `x-api-key`, impedindo que um agente acesse dados de outro tenant.
- **Compatibilidade Retroativa**: Suporta as rotas legadas `/tools` além das rotas tipadas `/{service}/{tenant}/tools`.

---

## 📐 1. Arquitetura da Solução

```text
┌─────────────────────────────────┐       ┌─────────────────────────────────┐
│     Agente A (Lovable)          │       │      Agente B (Uscreen)         │
│  Header: x-api-key: sk_tenant_1 │       │  Header: x-api-key: sk_tenant_2 │
└────────────────┬────────────────┘       └────────────────┬────────────────┘
                 │ POST /lovable/tools/...                 │ POST /uscreen/tools/...
                 ▼                                         ▼
┌───────────────────────────────────────────────────────────────────────────┐
│                    MCP MULTI-TENANT BRIDGE (Docker)                       │
│                                                                           │
│   Web Dashboard em http://localhost:8000/ (Autenticação 1-Clique)         │
│                                                                           │
│  ┌─────────────────────────────────────────────────────────────────────┐  │
│  │ FastAPI Router & Dispatcher                                         │  │
│  │ 1. Valida x-api-key (Isolamento estrito entre tenants)              │  │
│  │ 2. Pool de Sessões MCP Independentes (Async Locks por Tenant)       │  │
│  │ 3. Background Loop de Auto-Renovação Proativa                       │  │
│  └──────────────────┬───────────────────────────────┬──────────────────┘  │
│                     │                               │                     │
│    Sessão MCP Lovable (Tenant 1)     Sessão MCP Uscreen (Tenant 2)        │
└─────────────┬───────────────────────────────────────┬─────────────────────┘
              │ Bearer OAuth + PKCE                   │ Bearer OAuth + PKCE
              ▼                                       ▼
 ┌──────────────────────────┐           ┌──────────────────────────┐
 │    Lovable MCP Server    │           │    Uscreen MCP Server    │
 │  https://mcp.lovable.dev │           │  https://app.uscreen.tv  │
 └──────────────────────────┘           └──────────────────────────┘
```

---

## ⚡ 2. Início Rápido

### Passo 1: Configurar Variáveis de Ambiente
Copie o arquivo de exemplo caso ainda não tenha feito:
```bash
cp .env.example .env
```
Edite `.env`:
- `INTERNAL_API_KEY`: Sua chave mestra administrativa.
- `PORT`: Porta do serviço (padrão: `8000`).
- `BIND_IP`: `0.0.0.0` para todas as interfaces ou o IP da sua VPN (Tailscale/WireGuard).

### Passo 2: Iniciar com Docker Compose
```bash
docker compose up -d --build
```

O contêiner subirá com o servidor e o painel web ativos.

---

## 🌐 3. Autenticação Fácil via Painel Web (Recomendado)

Abra no seu navegador:
```text
http://localhost:8000/
(ou http://IP_DO_SERVIDOR:8000/)
```

1. **Visualizar Tenants**: Você verá o tenant `default` já criado e suas conexões com Lovable e Uscreen.
2. **Conectar Uscreen ou Lovable**:
   - Clique em **"🚀 Conectar Uscreen"** ou **"🚀 Conectar Lovable"**.
   - Você será redirecionado para a tela oficial de login e consentimento da plataforma.
   - Após autorizar, você voltará ao painel com a conexão **Conectada e Ativa**!
3. **Novo Tenant**:
   - Clique em **"+ Novo Tenant"** para adicionar uma nova empresa ou loja.
   - Cada tenant recebe uma `x-api-key` própria.
4. **Setup do Agente**:
   - Clique em **"🤖 Setup Agente"** em qualquer serviço para copiar a URL exata e o comando cURL pronto para uso.

---

## 💻 4. Autenticação via Linha de Comando (CLI Alternativo)

Se preferir autenticar via terminal:

```bash
# Conectar Lovable para o tenant default:
./auth_login.py --service lovable --tenant default

# Conectar Uscreen para o tenant default:
./auth_login.py --service uscreen --tenant default

# Conectar Uscreen para um tenant específico:
./auth_login.py --service uscreen --tenant loja_filial_sp
```

---

## 🤖 5. Como Conectar seus Agentes de IA

### Roteamento por Caminho (Recomendado)

Cada agente pode se comunicar diretamente com seu serviço e tenant correspondente:

### ⭐ Endpoint MCP Nativo com Token Fixo (Recomendado)

O proxy também é um **servidor MCP de verdade** (Streamable HTTP / JSON-RPC). Qualquer cliente MCP
(Claude, Cursor, VS Code, N8N MCP Client, Flowise...) conecta direto com uma **chave fixa**: sem OAuth
no agente, sem expiração. O proxy cuida do OAuth com Lovable/Uscreen por trás.

| URL | Tenant |
| :--- | :--- |
| `https://SEU_DOMINIO/uscreen/mcp` | Definido pela chave |
| `https://SEU_DOMINIO/lovable/mcp` | Definido pela chave |
| `https://SEU_DOMINIO/{service}/{tenant}/mcp` | Explícito (Master Key ou chave do próprio tenant) |
| `https://SEU_DOMINIO/mcp` | Serviço via header `x-service` (padrão: `lovable`) |

Autenticação: `Authorization: Bearer SUA_CHAVE` **ou** `x-api-key: SUA_CHAVE`.

Exemplo de configuração (Claude Desktop / Cursor / VS Code):
```json
{
  "mcpServers": {
    "uscreen": {
      "type": "http",
      "url": "https://mcp.encomendafacil.shop/uscreen/mcp",
      "headers": { "Authorization": "Bearer SUA_CHAVE_DO_TENANT" }
    },
    "lovable": {
      "type": "http",
      "url": "https://mcp.encomendafacil.shop/lovable/mcp",
      "headers": { "Authorization": "Bearer SUA_CHAVE_DO_TENANT" }
    }
  }
}
```

Teste rápido via cURL:
```bash
curl -X POST "https://mcp.encomendafacil.shop/uscreen/mcp" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "Authorization: Bearer SUA_CHAVE_DO_TENANT" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
```

> O painel web mostra a URL MCP e o JSON pronto no botão **"🤖 Setup Agente"**.

### Rotas REST (alternativa)

#### Uscreen:
- **Listar Ferramentas:**
  ```bash
  curl -X GET "http://localhost:8000/uscreen/default/tools" \
    -H "x-api-key: SUA_CHAVE_DO_TENANT"
  ```
- **Executar Ferramenta:**
  ```bash
  curl -X POST "http://localhost:8000/uscreen/default/tools/NOME_DA_TOOL" \
    -H "Content-Type: application/json" \
    -H "x-api-key: SUA_CHAVE_DO_TENANT" \
    -d '{"parametro": "valor"}'
  ```

#### Lovable:
- **Listar Ferramentas:**
  ```bash
  curl -X GET "http://localhost:8000/lovable/default/tools" \
    -H "x-api-key: SUA_CHAVE_DO_TENANT"
  ```
- **Executar Ferramenta:**
  ```bash
  curl -X POST "http://localhost:8000/lovable/default/tools/list_workspaces" \
    -H "Content-Type: application/json" \
    -H "x-api-key: SUA_CHAVE_DO_TENANT" \
    -d '{}'
  ```

---

## 📁 6. Estrutura de Arquivos

```text
MCP_Lovable/
├── proxy.py              # Aplicação principal FastAPI com roteamento e isolamento
├── providers.py          # Especificação OAuth 2.1 (PKCE) de Lovable e Uscreen
├── session_manager.py    # Pool de sessões MCP persistentes e auto-refresh
├── storage.py            # Persistência de tenants e tokens em /data
├── dashboard.py          # Interface visual web moderna para autenticação
├── auth_login.py         # Utilitário CLI para autenticação via terminal
├── Dockerfile            # Imagem de produção otimizada
├── docker-compose.yml    # Orquestração do serviço
├── test_endpoints.sh     # Script para testes automatizados
└── data/                 # Volume persistente
    ├── tenants.json      # Metadados e chaves dos tenants
    ├── clients/          # Cache de clientes OAuth dinâmicos
    └── tokens/           # Tokens isolados ({service}_{tenant}.json)
```

---

## 🛡️ 7. Segurança e Isolamento

- **Proteção contra Cross-Tenant:** Se um agente enviar a `x-api-key` do `tenant_a`, o proxy rejeitará qualquer tentativa de acessar ferramentas do `tenant_b` com HTTP 403 Forbidden.
- **Master Admin Key:** A `INTERNAL_API_KEY` definida no `.env` possui acesso administrativo a todos os tenants e ao painel web.
- **Tokens Protegidos:** Todos os arquivos em `data/` são gravados atomicamente e ignorados pelo `.gitignore`.

## Yungas (APIs v3 + PEX em um único MCP)

Serviço `yungas`, acessível em `/yungas/mcp` e `/yungas/{tenant}/mcp` (mesma chave fixa dos demais).

- **Login:** painel → card "Yungas MCP" → *Login Yungas*. Informe `client_id` e `client_secret`
  (fornecidos por suporte@yungas.com.br), ambiente (production/staging), scopes e, opcionalmente, a API Key do PEX.
  As credenciais são validadas na hora e o token (client_credentials) é renovado automaticamente.
- **Fallback via `.env`** (tenant `default`): `YUNGAS_CLIENT_ID`, `YUNGAS_CLIENT_SECRET`, `YUNGAS_SCOPES`, `YUNGAS_ENV`, `YUNGAS_PEX_API_KEY`.
- **Tools:** uma por endpoint (`yungas_list_tickets`, `yungas_list_franchises`, `yungas_store_list_orders`, `yungas_pex_get_kpis`...)
  mais `yungas_request` para qualquer endpoint não coberto. Parâmetros: path params diretos, `query` e `body` como objetos.
- Limite da API: 1000 requisições / 5 min (o cliente faz retry em HTTP 405).

## Sults (API v1 + API v2 em um único MCP)

Serviço `sults`, acessível em `/sults/mcp` e `/sults/{tenant}/mcp` (mesma chave fixa dos demais).

- **Credenciais:** painel → card "Sults MCP" → *Configurar Sults*. Informe o Token v1 e/ou o Token v2
  (gerados em *Segurança > API > Gestão de Tokens* na Sults; os dois tokens são independentes).
  Cada token informado é validado contra a API antes de ser salvo.
- **Fallback via `.env`** (tenant `default`): `SULTS_TOKEN_V1`, `SULTS_TOKEN_V2`.
- **Tools:** 88 tools geradas automaticamente a partir das specs OpenAPI em `sults_specs/` (27 `sults_v1_*` + 61 `sults_v2_*`),
  incluindo upload multipart (`sults_v2_enviarArquivoV2` com `file_base64` e `filename`), mais a tool genérica `sults_request`.
- **Limite da API:** 2 requisições por segundo por IP (compartilhado entre todos os tenants via limitador global de 0,5 s + backoff exponencial em 429/5xx).

## Omie ERP (Catálogo completo de 138 serviços / 500 métodos)

Serviço `omie`, acessível em `/omie/mcp` e `/omie/{tenant}/mcp` (mesma chave fixa dos demais).

- **Credenciais:** painel → card "Omie ERP MCP" → *Configurar Omie*. Informe `APP_KEY` e `APP_SECRET`
  (gerados em *Meus Aplicativos* no Portal do Desenvolvedor Omie). Validados contra `ListarEmpresas` ao salvar.
- **Fallback via `.env`** (tenant `default`): `OMIE_APP_KEY`, `OMIE_APP_SECRET`.
- **Tools (25 no total, 100% dos serviços cobertos):**
  - 4 tools de catálogo e execução dinâmica baseadas nos WSDLs oficiais (`omie_specs/catalog.json`):
    `omie_list_services`, `omie_get_service_methods`, `omie_get_method_schema` e `omie_call`.
  - 21 atalhos diretos para as rotinas mais usadas do ERP (Empresas, Clientes, Produtos, Estoque,
    Pedidos de Venda, NF-e, Resumo Financeiro, Contas a Pagar/Receber, Contas Correntes, OS e CRM).
- **Limite da API:** semáforo global de 4 requisições simultâneas + espaçamento mínimo de 0,25 s (~4 req/s) e backoff exponencial em 429/5xx/faults de concorrência.
