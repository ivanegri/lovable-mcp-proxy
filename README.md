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
