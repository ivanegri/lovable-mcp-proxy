# 🚀 Lovable MCP Proxy Bridge

Proxy REST conteinerizado (Docker + FastAPI) para conectar seu framework (N8N, Flowise, agentes de IA ou aplicações customizadas) ao **servidor MCP oficial do Lovable** (`https://mcp.lovable.dev/sse`) de forma segura, isolada e com suporte a rede privada/VPN.

---

## 📐 1. Arquitetura da Solução

```text
┌─────────────────────────┐
│ Seu Framework / Agente  │ (ex: N8N, Flowise, Python, cURL)
│ (Na mesma VPN / Rede)   │
└───────────┬─────────────┘
            │  HTTP POST / GET + Header "x-api-key: <INTERNAL_API_KEY>"
            ▼
┌─────────────────────────┐
│   Rede VPN Privada      │ (Tailscale / WireGuard / OpenVPN)
│   Host IP (ex: 10.8.0.5)│
└───────────┬─────────────┘
            │  Porta 8000 (atrelada ao IP da VPN)
            ▼
┌──────────────────────────────────────────────────────────┐
│  Host Docker (lovable-mcp-proxy)                         │
│  ┌────────────────────────────────────────────────────┐  │
│  │ FastAPI Proxy                                      │  │
│  │ 1. Valida x-api-key interna                        │  │
│  │ 2. Lê access_token persistido em volume            │  │
│  │ 3. Gerencia sessão MCP persistente                 │  │
│  └────────────────────────┬───────────────────────────┘  │
│                           │                              │
│  ┌────────────────────────┴───────────┐                  │
│  │ Volume Persistente (./data)        │                  │
│  │ └── tokens.json                    │                  │
│  └────────────────────────────────────┘                  │
└───────────────────────────┬──────────────────────────────┘
                            │  HTTPS + SSE (Server-Sent Events)
                            │  Authorization: Bearer <Lovable OAuth Token>
                            ▼
           ┌───────────────────────────────────┐
           │    Lovable MCP Server (Cloud)     │
           │    https://mcp.lovable.dev/sse    │
           └───────────────────────────────────┘
```

---

## 📁 2. Estrutura de Arquivos

```text
MCP_Lovable/
├── auth_login.py         # Assistente CLI para login OAuth 2.1 e captura automática do access/refresh token
├── proxy.py              # Aplicação FastAPI com conexão SSE MCP e auto-refresh de tokens
├── Dockerfile            # Imagem Docker baseada em python:3.11-slim
├── docker-compose.yml    # Orquestração do contêiner e mapeamento de volumes/portas
├── requirements.txt      # Dependências (fastapi, uvicorn, httpx, mcp)
├── .env.example          # Modelo de variáveis de ambiente
├── .env                  # Configurações ativas locais (chave interna, IP, porta)
├── .gitignore            # Protege tokens e arquivos sensíveis
├── test_endpoints.sh     # Script bash para validação rápida dos endpoints
└── data/
    ├── oauth_client.json # Credenciais do cliente OAuth local registradas dinamicamente
    ├── tokens.json.example
    └── tokens.json       # access_token e refresh_token (com rotação e renovação automática)
```

---

## ⚡ 3. Passo a Passo de Instalação e Execução

### Passo 1: Configurar Variáveis de Ambiente
Copie o modelo de ambiente se ainda não tiver feito:
```bash
cp .env.example .env
```
Edite o arquivo `.env`:
- `INTERNAL_API_KEY`: Defina uma chave secreta e forte para proteger o proxy de acessos não autorizados na VPN.
- `BIND_IP`: Se desejar que o proxy só escute no IP da VPN da máquina host (ex: `10.8.0.5` ou IP Tailscale `100.x.x.x`), preencha com o respectivo IP. Use `0.0.0.0` para escutar em todas as interfaces.
- `PORT`: Porta de serviço (padrão: `8000`).

### Passo 2: Obter o Token e Refresh Token do Lovable

Execute o assistente automático:
```bash
./auth_login.py
```
O script fará todo o processo:
1. Registra o cliente OAuth localmente no Lovable com suporte ao escopo `offline`.
2. Abre a tela oficial de login e autorização do Lovable no seu navegador.
3. Captura o código de autorização em `http://localhost:8080/callback`.
4. Troca o código pelo `access_token` e **`refresh_token`**.
5. Salva tudo automaticamente em `data/tokens.json` e notifica o contêiner Docker para conectar imediatamente!

> **✨ Renovação Automática (Zero Downtime)**: Com o `refresh_token` salvo, o próprio `proxy.py` renovará os tokens em segundo plano antes que expirem, sem necessidade de nova intervenção humana!

### Passo 3: Iniciar o Contêiner com Docker Compose
```bash
docker compose up -d --build
```

### Passo 4: Verificar a Conexão
Consulte os logs do contêiner para confirmar a conexão com o Lovable:
```bash
docker compose logs -f lovable-mcp-proxy
```
Você verá:
```text
✅ Conectado ao Lovable MCP com sucesso!
INFO: Uvicorn running on http://0.0.0.0:8000
```

---

## 🧪 4. Testando os Endpoints

Você pode executar o script utilitário incluído:
```bash
./test_endpoints.sh
```

Ou usar `curl` diretamente:

### 1. Healthcheck (Sem autenticação requerida)
```bash
curl -X GET http://localhost:8000/health
```
Resposta esperada:
```json
{
  "status": "healthy",
  "mcp_connected": true,
  "token_file_present": true
}
```

### 2. Listar Ferramentas do Lovable MCP
```bash
curl -X GET http://localhost:8000/tools \
  -H "x-api-key: minha_chave_secreta_123"
```

### 3. Executar uma Ferramenta (Ex: `list_workspaces`)
```bash
curl -X POST http://localhost:8000/tools/list_workspaces \
  -H "Content-Type: application/json" \
  -H "x-api-key: minha_chave_secreta_123" \
  -d '{}'
```

### 4. Forçar Reconexão (Se atualizar o token em `tokens.json`)
```bash
curl -X POST http://localhost:8000/reconnect \
  -H "x-api-key: minha_chave_secreta_123"
```

---

## 🛠️ 5. Catálogo de Ferramentas Disponíveis no Lovable MCP

Todas as chamadas são feitas via `POST /tools/{tool_name}` enviando os argumentos em formato JSON no body:

### 🔹 1. Inspeção e Compreensão de Código
* **`list_files`**: Lista a árvore de arquivos e pastas do projeto.
  ```json
  {
    "project_id": "seu-project-id"
  }
  ```
* **`read_file`**: Lê o conteúdo bruto de um arquivo.
  ```json
  {
    "project_id": "seu-project-id",
    "path": "src/App.tsx"
  }
  ```
* **`get_diff`**: Obtém o diff unificado de uma alteração ou mensagem.
  ```json
  {
    "project_id": "seu-project-id",
    "message_id": "message-id-aqui"
  }
  ```
* **`list_edits`**: Lista o histórico de edições do projeto.
  ```json
  {
    "project_id": "seu-project-id"
  }
  ```

### 🔹 2. Gestão de Projetos e Workspaces
* **`list_workspaces`**: Lista todos os workspaces da sua conta.
  ```json
  {}
  ```
* **`get_workspace`**: Retorna informações detalhadas do workspace (plano, créditos, membros).
  ```json
  {
    "workspace_id": "workspace-id-aqui"
  }
  ```
* **`list_projects`**: Pesquisa e lista projetos no workspace.
  ```json
  {
    "workspace_id": "workspace-id-aqui",
    "query": "meu app"
  }
  ```
* **`get_project`**: Detalhes completos do projeto (URLs de editor e preview, screenshot).
  ```json
  {
    "project_id": "seu-project-id"
  }
  ```
* **`create_project`**: Cria um novo projeto a partir de um prompt inicial.
  ```json
  {
    "workspace_id": "workspace-id-aqui",
    "prompt": "Crie um dashboard moderno para controle de finanças pessoais com Next.js, Tailwind e Shadcn UI"
  }
  ```
* **`deploy_project`**: Publica o projeto no domínio de produção Lovable.
  ```json
  {
    "project_id": "seu-project-id"
  }
  ```
* **`remix_project`**: Faz fork/duplicação de um projeto para o seu workspace.
  ```json
  {
    "project_id": "projeto-origem-id",
    "workspace_id": "seu-workspace-id"
  }
  ```

### 🔹 3. Interação com o Agente do Lovable
* **`send_message`**: Envia instruções de alteração para o agente de IA do Lovable no projeto.
  ```json
  {
    "project_id": "seu-project-id",
    "message": "Adicione um gráfico de pizza mostrando a divisão de despesas por categoria na página principal"
  }
  ```
* **`get_message`**: Consulta o status e a resposta de uma instrução enviada.
  ```json
  {
    "project_id": "seu-project-id",
    "message_id": "message-id-aqui"
  }
  ```
* **`list_messages`**: Lista o histórico das últimas mensagens trocadas no projeto.
  ```json
  {
    "project_id": "seu-project-id"
  }
  ```

### 🔹 4. Banco de Dados Cloud (PostgreSQL)
* **`get_database_status`**: Checa se o banco de dados cloud está habilitado no projeto.
  ```json
  {
    "project_id": "seu-project-id"
  }
  ```
* **`enable_database`**: Ativa a infraestrutura de banco de dados na nuvem para o projeto.
  ```json
  {
    "project_id": "seu-project-id"
  }
  ```
* **`query_database`**: Executa comandos SQL (SELECT, INSERT, UPDATE, DDL).
  ```json
  {
    "project_id": "seu-project-id",
    "query": "SELECT table_name FROM information_schema.tables WHERE table_schema='public';"
  }
  ```

### 🔹 5. Conhecimento e Diretrizes (Knowledge & Skills)
* **`get_project_knowledge`** / **`set_project_knowledge`**: Lê ou altera diretrizes técnicas específicas do projeto.
* **`get_workspace_knowledge`** / **`set_workspace_knowledge`**: Lê ou altera padrões globais do workspace.
* **`list_workspace_skills`**: Lista habilidades customizadas cadastradas no workspace.

### 🔹 6. Analytics e Integrações
* **`get_project_analytics`**: Consulta estatísticas reais de acesso do projeto publicado.
* **`list_connectors`** / **`list_connections`**: Lista conectores e integrações (Slack, GitHub, Linear, etc.).

---

## 🔒 6. Recomendações de Segurança em Produção

1. **Restringir Interface de Rede (`BIND_IP`)**:
   - Defina `BIND_IP=10.x.x.x` (IP da VPN) no arquivo `.env` para garantir que o Docker não exponha a porta `8000` na interface pública (`0.0.0.0`).
2. **Rotação de Chaves**:
   - Mantenha `INTERNAL_API_KEY` com alta entropia (gerada via `openssl rand -hex 32`).
3. **Persistência Segura**:
   - O arquivo `data/tokens.json` é ignorado no git (`.gitignore`). Nunca realize commit de tokens ou credenciais no repositório.
4. **Reconexão Sem Downtime**:
   - Se o token expirar ou for renovado, basta atualizar o arquivo `data/tokens.json` no host e disparar `POST /reconnect`. O proxy recarrega a sessão sem necessidade de reiniciar o contêiner.
