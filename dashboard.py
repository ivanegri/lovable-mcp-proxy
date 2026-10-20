"""
Web Dashboard para Gerenciamento Multi-Tenant e Autenticação OAuth do MCP Proxy Hub
"""

def get_dashboard_html() -> str:
    return r"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>MCP Multi-Tenant Bridge | Lovable & Uscreen Hub</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
  <style>
    :root {
      --bg: #090d16;
      --card-bg: rgba(17, 24, 39, 0.7);
      --card-border: rgba(255, 255, 255, 0.08);
      --card-hover: rgba(255, 255, 255, 0.12);
      --text: #f3f4f6;
      --text-muted: #9ca3af;
      --lovable: #ec4899;
      --lovable-gradient: linear-gradient(135deg, #ec4899 0%, #8b5cf6 100%);
      --uscreen: #06b6d4;
      --uscreen-gradient: linear-gradient(135deg, #06b6d4 0%, #3b82f6 100%);
      --success: #10b981;
      --warning: #f59e0b;
      --danger: #ef4444;
      --radius: 14px;
    }

    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: 'Outfit', -apple-system, BlinkMacSystemFont, sans-serif;
      background: var(--bg);
      background-image: 
        radial-gradient(at 0% 0%, rgba(236, 72, 153, 0.12) 0px, transparent 50%),
        radial-gradient(at 100% 100%, rgba(6, 182, 212, 0.12) 0px, transparent 50%);
      color: var(--text);
      min-height: 100vh;
      display: flex;
      flex-direction: column;
    }

    .container {
      max-width: 1200px;
      margin: 0 auto;
      padding: 28px 20px;
      width: 100%;
    }

    /* Header */
    header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding-bottom: 24px;
      border-bottom: 1px solid var(--card-border);
      margin-bottom: 32px;
      flex-wrap: wrap;
      gap: 16px;
    }

    .brand {
      display: flex;
      align-items: center;
      gap: 14px;
    }

    .logo-badge {
      width: 44px;
      height: 44px;
      border-radius: 12px;
      background: linear-gradient(135deg, #8b5cf6, #ec4899);
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 22px;
      box-shadow: 0 4px 20px rgba(139, 92, 246, 0.4);
    }

    .brand-title h1 {
      font-size: 22px;
      font-weight: 700;
      letter-spacing: -0.02em;
      background: linear-gradient(to right, #fff, #cbd5e1);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
    }

    .brand-title p {
      font-size: 13px;
      color: var(--text-muted);
    }

    .header-actions {
      display: flex;
      align-items: center;
      gap: 12px;
    }

    .btn {
      padding: 10px 18px;
      border-radius: 10px;
      font-size: 14px;
      font-weight: 600;
      cursor: pointer;
      border: none;
      display: inline-flex;
      align-items: center;
      gap: 8px;
      transition: all 0.2s ease;
      text-decoration: none;
    }

    .btn-primary {
      background: linear-gradient(135deg, #3b82f6, #6366f1);
      color: white;
      box-shadow: 0 4px 14px rgba(99, 102, 241, 0.3);
    }
    .btn-primary:hover {
      transform: translateY(-1px);
      box-shadow: 0 6px 20px rgba(99, 102, 241, 0.45);
    }

    .btn-secondary {
      background: rgba(255, 255, 255, 0.06);
      color: var(--text);
      border: 1px solid var(--card-border);
    }
    .btn-secondary:hover {
      background: rgba(255, 255, 255, 0.1);
      border-color: var(--card-hover);
    }

    .btn-sm {
      padding: 6px 12px;
      font-size: 12px;
      border-radius: 8px;
    }

    .btn-lovable {
      background: var(--lovable-gradient);
      color: white;
      box-shadow: 0 4px 14px rgba(236, 72, 153, 0.35);
    }
    .btn-lovable:hover {
      transform: translateY(-1px);
      box-shadow: 0 6px 20px rgba(236, 72, 153, 0.5);
    }

    .btn-uscreen {
      background: var(--uscreen-gradient);
      color: white;
      box-shadow: 0 4px 14px rgba(6, 182, 212, 0.35);
    }
    .btn-uscreen:hover {
      transform: translateY(-1px);
      box-shadow: 0 6px 20px rgba(6, 182, 212, 0.5);
    }

    .btn-danger-outline {
      background: transparent;
      color: #f87171;
      border: 1px solid rgba(239, 68, 68, 0.3);
    }
    .btn-danger-outline:hover {
      background: rgba(239, 68, 68, 0.15);
      border-color: var(--danger);
    }

    /* Toast Notification */
    .toast-banner {
      padding: 14px 20px;
      border-radius: 12px;
      margin-bottom: 24px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      animation: slideDown 0.3s ease;
    }
    .toast-success {
      background: rgba(16, 185, 129, 0.15);
      border: 1px solid rgba(16, 185, 129, 0.35);
      color: #34d399;
    }
    .toast-error {
      background: rgba(239, 68, 68, 0.15);
      border: 1px solid rgba(239, 68, 68, 0.35);
      color: #f87171;
    }

    /* Stats bar */
    .stats-bar {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 16px;
      margin-bottom: 32px;
    }

    .stat-card {
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: var(--radius);
      padding: 18px 20px;
      backdrop-filter: blur(12px);
      display: flex;
      align-items: center;
      gap: 16px;
    }

    .stat-icon {
      width: 44px;
      height: 44px;
      border-radius: 10px;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 20px;
    }

    .stat-meta h3 {
      font-size: 20px;
      font-weight: 700;
    }
    .stat-meta p {
      font-size: 13px;
      color: var(--text-muted);
    }

    /* Tenant Grid */
    .tenants-grid {
      display: flex;
      flex-direction: column;
      gap: 24px;
    }

    .tenant-card {
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: var(--radius);
      padding: 24px;
      backdrop-filter: blur(14px);
      transition: border-color 0.2s ease;
    }
    .tenant-card:hover {
      border-color: var(--card-hover);
    }

    .tenant-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 12px;
      padding-bottom: 18px;
      border-bottom: 1px solid var(--card-border);
      margin-bottom: 20px;
    }

    .tenant-info {
      display: flex;
      align-items: center;
      gap: 12px;
    }

    .tenant-title {
      font-size: 18px;
      font-weight: 700;
    }

    .tenant-badge {
      background: rgba(255, 255, 255, 0.08);
      color: #cbd5e1;
      padding: 4px 10px;
      border-radius: 20px;
      font-size: 12px;
      font-family: 'JetBrains Mono', monospace;
    }

    .api-key-box {
      display: flex;
      align-items: center;
      gap: 8px;
      background: rgba(0, 0, 0, 0.4);
      padding: 6px 12px;
      border-radius: 8px;
      border: 1px solid var(--card-border);
      font-family: 'JetBrains Mono', monospace;
      font-size: 12px;
      color: #94a3b8;
    }

    /* Services inside Tenant */
    .services-container {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(340px, 1fr));
      gap: 20px;
    }

    .service-box {
      background: rgba(0, 0, 0, 0.25);
      border: 1px solid var(--card-border);
      border-radius: 12px;
      padding: 20px;
      display: flex;
      flex-direction: column;
      justify-content: space-between;
      position: relative;
      overflow: hidden;
    }

    .service-box::before {
      content: '';
      position: absolute;
      top: 0;
      left: 0;
      right: 0;
      height: 3px;
    }

    .service-lovable::before {
      background: var(--lovable-gradient);
    }

    .service-uscreen::before {
      background: var(--uscreen-gradient);
    }

    .service-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 14px;
    }

    .service-name {
      display: flex;
      align-items: center;
      gap: 10px;
      font-size: 16px;
      font-weight: 600;
    }

    .status-pill {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 4px 10px;
      border-radius: 20px;
      font-size: 12px;
      font-weight: 500;
    }

    .status-connected {
      background: rgba(16, 185, 129, 0.15);
      color: #34d399;
      border: 1px solid rgba(16, 185, 129, 0.3);
    }
    .status-connected::before {
      content: '';
      width: 7px;
      height: 7px;
      background: #10b981;
      border-radius: 50%;
      box-shadow: 0 0 8px #10b981;
    }

    .status-disconnected {
      background: rgba(239, 68, 68, 0.12);
      color: #f87171;
      border: 1px solid rgba(239, 68, 68, 0.25);
    }
    .status-disconnected::before {
      content: '';
      width: 7px;
      height: 7px;
      background: #ef4444;
      border-radius: 50%;
    }

    .service-details {
      font-size: 13px;
      color: var(--text-muted);
      margin-bottom: 18px;
      line-height: 1.6;
    }

    .service-actions {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
    }

    /* Modal */
    .modal-overlay {
      position: fixed;
      top: 0; left: 0; right: 0; bottom: 0;
      background: rgba(0, 0, 0, 0.75);
      backdrop-filter: blur(8px);
      display: none;
      align-items: center;
      justify-content: center;
      z-index: 1000;
      padding: 20px;
    }

    .modal-card {
      background: #111827;
      border: 1px solid var(--card-border);
      border-radius: var(--radius);
      max-width: 650px;
      width: 100%;
      padding: 28px;
      box-shadow: 0 20px 40px rgba(0,0,0,0.6);
      max-height: 85vh;
      overflow-y: auto;
    }

    .modal-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 20px;
    }

    .modal-header h2 {
      font-size: 20px;
      font-weight: 700;
    }

    .close-btn {
      background: none;
      border: none;
      color: var(--text-muted);
      font-size: 20px;
      cursor: pointer;
    }

    .form-group {
      margin-bottom: 16px;
    }

    .form-group label {
      display: block;
      font-size: 13px;
      font-weight: 500;
      color: #cbd5e1;
      margin-bottom: 6px;
    }

    .form-control {
      width: 100%;
      padding: 10px 14px;
      background: rgba(0, 0, 0, 0.4);
      border: 1px solid var(--card-border);
      border-radius: 8px;
      color: white;
      font-size: 14px;
      font-family: inherit;
    }
    .form-control:focus {
      outline: none;
      border-color: #6366f1;
    }

    /* Code Snippet Box */
    .code-block {
      background: #060911;
      border: 1px solid var(--card-border);
      border-radius: 8px;
      padding: 14px;
      font-family: 'JetBrains Mono', monospace;
      font-size: 12px;
      color: #e2e8f0;
      overflow-x: auto;
      position: relative;
      margin-top: 10px;
      white-space: pre-wrap;
      word-break: break-all;
    }

    .copy-snippet-btn {
      position: absolute;
      top: 8px;
      right: 8px;
      background: rgba(255, 255, 255, 0.1);
      border: none;
      color: #fff;
      padding: 4px 8px;
      border-radius: 6px;
      font-size: 11px;
      cursor: pointer;
    }

    @keyframes slideDown {
      from { opacity: 0; transform: translateY(-10px); }
      to { opacity: 1; transform: translateY(0); }
    }
  </style>
</head>
<body>

  <div class="container">
    <!-- Header -->
    <header>
      <div class="brand">
        <div class="logo-badge">⚡</div>
        <div class="brand-title">
          <h1>MCP Multi-Tenant Bridge</h1>
          <p>Gateway unificado Lovable & Uscreen com isolamento total</p>
        </div>
      </div>
      <div class="header-actions">
        <button class="btn btn-secondary" onclick="openApiKeyModal()">🔑 Master Key</button>
        <button class="btn btn-primary" onclick="openNewTenantModal()">+ Novo Tenant</button>
      </div>
    </header>

    <!-- Notification Toast if redirected from OAuth -->
    <div id="toast-container"></div>

    <!-- Stats Bar -->
    <div class="stats-bar">
      <div class="stat-card">
        <div class="stat-icon" style="background: rgba(236, 72, 153, 0.15); color: #ec4899;">💖</div>
        <div class="stat-meta">
          <h3 id="stat-lovable-connected">0</h3>
          <p>Contas Lovable Ativas</p>
        </div>
      </div>
      <div class="stat-card">
        <div class="stat-icon" style="background: rgba(6, 182, 212, 0.15); color: #06b6d4;">📺</div>
        <div class="stat-meta">
          <h3 id="stat-uscreen-connected">0</h3>
          <p>Contas Uscreen Ativas</p>
        </div>
      </div>
      <div class="stat-card">
        <div class="stat-icon" style="background: rgba(16, 185, 129, 0.15); color: #10b981;">🛡️</div>
        <div class="stat-meta">
          <h3 id="stat-total-tenants">0</h3>
          <p>Tenants Registrados</p>
        </div>
      </div>
    </div>

    <!-- Tenants Section -->
    <h2 style="font-size: 18px; margin-bottom: 16px; font-weight: 600;">Seus Tenants & Conexões</h2>
    <div id="tenants-list" class="tenants-grid">
      <div style="text-align: center; padding: 40px; color: var(--text-muted);">
        Carregando status dos tenants...
      </div>
    </div>
  </div>

  <!-- Modal: Novo Tenant -->
  <div id="modal-new-tenant" class="modal-overlay">
    <div class="modal-card">
      <div class="modal-header">
        <h2>Criar Novo Tenant</h2>
        <button class="close-btn" onclick="closeModal('modal-new-tenant')">&times;</button>
      </div>
      <form id="form-new-tenant" onsubmit="handleCreateTenant(event)">
        <div class="form-group">
          <label>Identificador Único (Slug)</label>
          <input type="text" id="new-tenant-id" class="form-control" placeholder="ex: cliente_acme ou filial_sp" required>
        </div>
        <div class="form-group">
          <label>Nome Amigável</label>
          <input type="text" id="new-tenant-name" class="form-control" placeholder="ex: Acme Corp" required>
        </div>
        <div class="form-group">
          <label>API Key Personalizada (Opcional - deixe vazio para gerar automático)</label>
          <input type="text" id="new-tenant-key" class="form-control" placeholder="sk_...">
        </div>
        <div style="display: flex; justify-content: flex-end; gap: 10px; margin-top: 24px;">
          <button type="button" class="btn btn-secondary" onclick="closeModal('modal-new-tenant')">Cancelar</button>
          <button type="submit" class="btn btn-primary">Salvar Tenant</button>
        </div>
      </form>
    </div>
  </div>

  <!-- Modal: Setup / Configuração do Agente -->
  <div id="modal-agent-setup" class="modal-overlay">
    <div class="modal-card">
      <div class="modal-header">
        <h2 id="setup-modal-title">Configurar Agente de IA</h2>
        <button class="close-btn" onclick="closeModal('modal-agent-setup')">&times;</button>
      </div>
      <p style="font-size: 14px; color: var(--text-muted); margin-bottom: 16px;">
        Use estes detalhes no seu framework de agentes (N8N, Flowise, LangChain, Cursor ou cURL). As requisições serão roteadas com total isolamento.
      </p>

      <div class="form-group">
        <label>⭐ URL MCP nativa (Streamable HTTP) — use com token fixo:</label>
        <div class="code-block" id="setup-mcp-url"></div>
      </div>

      <div class="form-group">
        <label>Config para clientes MCP (Claude / Cursor / VS Code):</label>
        <div class="code-block" id="setup-mcp-json"></div>
      </div>

      <div class="form-group">
        <label>URL para Listar Ferramentas (GET):</label>
        <div class="code-block" id="setup-url-list"></div>
      </div>

      <div class="form-group">
        <label>URL para Executar Ferramenta (POST):</label>
        <div class="code-block" id="setup-url-call"></div>
      </div>

      <div class="form-group">
        <label>Header de Autenticação Necessário:</label>
        <div class="code-block" id="setup-header"></div>
      </div>

      <div class="form-group">
        <label>Exemplo de Chamada cURL:</label>
        <div class="code-block" id="setup-curl"></div>
      </div>

      <div style="display: flex; justify-content: flex-end; margin-top: 20px;">
        <button class="btn btn-secondary" onclick="closeModal('modal-agent-setup')">Fechar</button>
      </div>
    </div>
  </div>

  <!-- Modal: Visualizador de Ferramentas -->
  <div id="modal-tools" class="modal-overlay">
    <div class="modal-card" style="max-width: 750px;">
      <div class="modal-header">
        <h2 id="tools-modal-title">Ferramentas Disponíveis</h2>
        <button class="close-btn" onclick="closeModal('modal-tools')">&times;</button>
      </div>
      <div id="tools-modal-content" style="max-height: 60vh; overflow-y: auto;">
        <p style="color: var(--text-muted);">Consultando ferramentas no servidor MCP oficial...</p>
      </div>
      <div style="display: flex; justify-content: flex-end; margin-top: 20px;">
        <button class="btn btn-secondary" onclick="closeModal('modal-tools')">Fechar</button>
      </div>
    </div>
  </div>

  <script>
    // Recupera Master Key salva no navegador se houver
    let masterKey = localStorage.getItem('mcp_master_key') || '';

    // Verifica parâmetros de URL para feedback pós-OAuth
    window.addEventListener('DOMContentLoaded', () => {
      const urlParams = new URLSearchParams(window.location.search);
      const connected = urlParams.get('connected');
      const tenant = urlParams.get('tenant');
      const error = urlParams.get('error');

      if (connected) {
        showToast(`🎉 Sucesso! Serviço ${connected.toUpperCase()} conectado para o tenant "${tenant}".`, 'success');
        window.history.replaceState({}, document.title, window.location.pathname);
      } else if (error) {
        showToast(`❌ Falha na autorização: ${error}`, 'error');
        window.history.replaceState({}, document.title, window.location.pathname);
      }

      loadTenantsData();
    });

    function showToast(message, type = 'success') {
      const container = document.getElementById('toast-container');
      container.innerHTML = `
        <div class="toast-banner ${type === 'success' ? 'toast-success' : 'toast-error'}">
          <span>${message}</span>
          <button style="background:none; border:none; color:inherit; cursor:pointer;" onclick="this.parentElement.remove()">&times;</button>
        </div>
      `;
    }

    function getAuthHeaders() {
      const headers = { 'Content-Type': 'application/json' };
      if (masterKey) headers['x-api-key'] = masterKey;
      return headers;
    }

    async function loadTenantsData() {
      try {
        const resp = await fetch('/api/tenants', { headers: getAuthHeaders() });
        if (resp.status === 401) {
          askForMasterKey();
          return;
        }
        const data = await resp.json();
        renderDashboard(data);
      } catch (err) {
        console.error(err);
        document.getElementById('tenants-list').innerHTML = `
          <div style="padding: 30px; text-align: center; color: var(--danger);">
            Erro ao carregar dados do servidor: ${err.message}
          </div>
        `;
      }
    }

    function renderDashboard(data) {
      const tenants = data.tenants || {};
      const tokensStatus = data.tokens_status || [];

      let lovableCount = 0;
      let uscreenCount = 0;
      const tenantKeys = Object.keys(tenants);

      tokensStatus.forEach(t => {
        if (t.connected) {
          if (t.service === 'lovable') lovableCount++;
          if (t.service === 'uscreen') uscreenCount++;
        }
      });

      document.getElementById('stat-lovable-connected').innerText = lovableCount;
      document.getElementById('stat-uscreen-connected').innerText = uscreenCount;
      document.getElementById('stat-total-tenants').innerText = tenantKeys.length;

      const container = document.getElementById('tenants-list');
      if (tenantKeys.length === 0) {
        container.innerHTML = '<div style="text-align: center; color: var(--text-muted); padding: 40px;">Nenhum tenant cadastrado.</div>';
        return;
      }

      container.innerHTML = tenantKeys.map(tId => {
        const tenant = tenants[tId];
        const lovableStatus = tokensStatus.find(s => s.tenant_id === tId && s.service === 'lovable') || {};
        const uscreenStatus = tokensStatus.find(s => s.tenant_id === tId && s.service === 'uscreen') || {};

        return `
          <div class="tenant-card">
            <div class="tenant-header">
              <div class="tenant-info">
                <span class="tenant-title">${escapeHtml(tenant.name)}</span>
                <span class="tenant-badge">ID: ${escapeHtml(tId)}</span>
              </div>
              <div style="display: flex; align-items: center; gap: 12px;">
                <div class="api-key-box">
                  <span>API Key:</span>
                  <span id="key-${tId}">${escapeHtml(tenant.api_key)}</span>
                  <button class="btn btn-secondary btn-sm" onclick="copyText('${escapeHtml(tenant.api_key)}')">Copiar</button>
                </div>
                ${tId !== 'default' ? `<button class="btn btn-danger-outline btn-sm" onclick="deleteTenant('${tId}')">Excluir</button>` : ''}
              </div>
            </div>

            <div class="services-container">
              <!-- Lovable Service -->
              <div class="service-box service-lovable">
                <div class="service-header">
                  <div class="service-name">
                    <span style="color: var(--lovable); font-size: 18px;">💖</span> Lovable MCP
                  </div>
                  <span class="status-pill ${lovableStatus.connected ? 'status-connected' : 'status-disconnected'}">
                    ${lovableStatus.connected ? (lovableStatus.is_expired ? 'Token Expirado' : 'Conectado') : 'Desconectado'}
                  </span>
                </div>
                <div class="service-details">
                  ${lovableStatus.connected ? `
                    Token ativo. Auto-renovação: <strong>${lovableStatus.has_refresh_token ? 'Ligada' : 'Sem Refresh Token'}</strong>.<br>
                    Expira em: <strong>${formatSeconds(lovableStatus.expires_in_seconds)}</strong>
                  ` : 'Nenhuma conta do Lovable vinculada a este tenant.'}
                </div>
                <div class="service-actions">
                  <a href="/oauth/login?service=lovable&tenant=${tId}" class="btn btn-lovable btn-sm">
                    ${lovableStatus.connected ? '🔄 Reconectar / Trocar' : '🚀 Conectar Lovable'}
                  </a>
                  ${lovableStatus.connected ? `
                    <button class="btn btn-secondary btn-sm" onclick="inspectTools('lovable', '${tId}')">🛠️ Ver Tools</button>
                    <button class="btn btn-secondary btn-sm" onclick="showAgentSetup('lovable', '${tId}', '${tenant.api_key}')">🤖 Setup Agente</button>
                    <button class="btn btn-danger-outline btn-sm" onclick="disconnectService('lovable', '${tId}')">Desconectar</button>
                  ` : ''}
                </div>
              </div>

              <!-- Uscreen Service -->
              <div class="service-box service-uscreen">
                <div class="service-header">
                  <div class="service-name">
                    <span style="color: var(--uscreen); font-size: 18px;">📺</span> Uscreen MCP
                  </div>
                  <span class="status-pill ${uscreenStatus.connected ? 'status-connected' : 'status-disconnected'}">
                    ${uscreenStatus.connected ? (uscreenStatus.is_expired ? 'Token Expirado' : 'Conectado') : 'Desconectado'}
                  </span>
                </div>
                <div class="service-details">
                  ${uscreenStatus.connected ? `
                    Token ativo. Auto-renovação: <strong>${uscreenStatus.has_refresh_token ? 'Ligada' : 'Sem Refresh Token'}</strong>.<br>
                    Expira em: <strong>${formatSeconds(uscreenStatus.expires_in_seconds)}</strong>
                  ` : 'Nenhuma conta do Uscreen vinculada a este tenant.'}
                </div>
                <div class="service-actions">
                  <a href="/oauth/login?service=uscreen&tenant=${tId}" class="btn btn-uscreen btn-sm">
                    ${uscreenStatus.connected ? '🔄 Reconectar / Trocar' : '🚀 Conectar Uscreen'}
                  </a>
                  ${uscreenStatus.connected ? `
                    <button class="btn btn-secondary btn-sm" onclick="inspectTools('uscreen', '${tId}')">🛠️ Ver Tools</button>
                    <button class="btn btn-secondary btn-sm" onclick="showAgentSetup('uscreen', '${tId}', '${tenant.api_key}')">🤖 Setup Agente</button>
                    <button class="btn btn-danger-outline btn-sm" onclick="disconnectService('uscreen', '${tId}')">Desconectar</button>
                  ` : ''}
                </div>
              </div>
            </div>
          </div>
        `;
      }).join('');
    }

    function openNewTenantModal() {
      document.getElementById('modal-new-tenant').style.display = 'flex';
    }

    function closeModal(id) {
      document.getElementById(id).style.display = 'none';
    }

    function askForMasterKey() {
      const key = prompt('Informe a INTERNAL_API_KEY para gerenciar o proxy:');
      if (key) {
        masterKey = key;
        localStorage.setItem('mcp_master_key', key);
        loadTenantsData();
      }
    }

    function openApiKeyModal() {
      askForMasterKey();
    }

    async function handleCreateTenant(e) {
      e.preventDefault();
      const id = document.getElementById('new-tenant-id').value.trim();
      const name = document.getElementById('new-tenant-name').value.trim();
      const apiKey = document.getElementById('new-tenant-key').value.trim();

      try {
        const resp = await fetch('/api/tenants', {
          method: 'POST',
          headers: getAuthHeaders(),
          body: JSON.stringify({ id, name, api_key: apiKey || null })
        });
        if (!resp.ok) {
          const err = await resp.json();
          alert('Erro ao criar tenant: ' + (err.detail || JSON.stringify(err)));
          return;
        }
        closeModal('modal-new-tenant');
        document.getElementById('form-new-tenant').reset();
        showToast(`Tenant "${name}" adicionado com sucesso!`);
        loadTenantsData();
      } catch (err) {
        alert('Erro ao salvar tenant: ' + err.message);
      }
    }

    async function deleteTenant(id) {
      if (!confirm(`Tem certeza que deseja excluir o tenant "${id}" e desconectar suas contas?`)) return;
      try {
        const resp = await fetch(`/api/tenants/${id}`, {
          method: 'DELETE',
          headers: getAuthHeaders()
        });
        if (!resp.ok) {
          const err = await resp.json();
          alert('Erro ao excluir: ' + err.detail);
          return;
        }
        showToast(`Tenant "${id}" excluído.`);
        loadTenantsData();
      } catch (err) {
        alert('Erro: ' + err.message);
      }
    }

    async function disconnectService(service, tenant) {
      if (!confirm(`Deseja revogar a conexão de ${service.toUpperCase()} para "${tenant}"?`)) return;
      try {
        const resp = await fetch(`/api/tokens/${service}/${tenant}`, {
          method: 'DELETE',
          headers: getAuthHeaders()
        });
        showToast(`Conexão de ${service.toUpperCase()} removida.`);
        loadTenantsData();
      } catch (err) {
        alert('Erro: ' + err.message);
      }
    }

    function showAgentSetup(service, tenant, apiKey) {
      const base = window.location.origin;
      const listUrl = `${base}/${service}/${tenant}/tools`;
      const callUrl = `${base}/${service}/${tenant}/tools/{tool_name}`;
      const header = `x-api-key: ${apiKey}`;
      const curlSample = `curl -X POST "${base}/${service}/${tenant}/tools/NOME_DA_TOOL" \\\\
  -H "Content-Type: application/json" \\\\
  -H "x-api-key: ${apiKey}" \\\\
  -d '{"parametro": "valor"}'`;

      document.getElementById('setup-modal-title').innerText = `Setup Agente: ${service.toUpperCase()} (${tenant})`;
      const mcpUrl = `${base}/${service}/mcp`;
      const mcpJson = JSON.stringify({ mcpServers: { [`${service}-${tenant}`]: { type: 'http', url: mcpUrl, headers: { Authorization: `Bearer ${apiKey}` } } } }, null, 2);
      document.getElementById('setup-mcp-url').innerHTML = `<code>${mcpUrl}</code> <button class="copy-snippet-btn" onclick="copyText('${mcpUrl}')">Copiar</button>`;
      document.getElementById('setup-mcp-json').innerHTML = `<code>${escapeHtml(mcpJson)}</code>`;
      document.getElementById('setup-mcp-json').onclick = () => copyText(mcpJson);
      document.getElementById('setup-url-list').innerHTML = `<code>${listUrl}</code> <button class="copy-snippet-btn" onclick="copyText('${listUrl}')">Copiar</button>`;
      document.getElementById('setup-url-call').innerHTML = `<code>${callUrl}</code> <button class="copy-snippet-btn" onclick="copyText('${callUrl}')">Copiar</button>`;
      document.getElementById('setup-header').innerHTML = `<code>${header}</code> <button class="copy-snippet-btn" onclick="copyText('${header}')">Copiar</button>`;
      document.getElementById('setup-curl').innerHTML = `<code>${curlSample}</code> <button class="copy-snippet-btn" onclick="copyText(\`${curlSample}\`)">Copiar</button>`;

      document.getElementById('modal-agent-setup').style.display = 'flex';
    }

    async function inspectTools(service, tenant) {
      document.getElementById('tools-modal-title').innerText = `Ferramentas MCP: ${service.toUpperCase()} (${tenant})`;
      document.getElementById('tools-modal-content').innerHTML = '<p style="color: var(--text-muted); padding: 20px;">Consultando ferramentas em tempo real...</p>';
      document.getElementById('modal-tools').style.display = 'flex';

      try {
        const resp = await fetch(`/${service}/${tenant}/tools`, { headers: getAuthHeaders() });
        if (!resp.ok) {
          const err = await resp.json();
          document.getElementById('tools-modal-content').innerHTML = `<p style="color: var(--danger);">Erro ao listar ferramentas: ${err.detail || JSON.stringify(err)}</p>`;
          return;
        }
        const data = await resp.json();
        const tools = data.tools || [];
        if (tools.length === 0) {
          document.getElementById('tools-modal-content').innerHTML = '<p style="color: var(--text-muted);">Nenhuma ferramenta retornada pelo servidor.</p>';
          return;
        }

        document.getElementById('tools-modal-content').innerHTML = tools.map(t => `
          <div style="background: rgba(0,0,0,0.3); border: 1px solid var(--card-border); border-radius: 8px; padding: 14px; margin-bottom: 12px;">
            <div style="font-weight: 700; color: #fff; margin-bottom: 4px; font-family: 'JetBrains Mono', monospace;">${escapeHtml(t.name)}</div>
            <div style="font-size: 13px; color: var(--text-muted); margin-bottom: 8px;">${escapeHtml(t.description || 'Sem descrição')}</div>
            <details style="font-size: 12px; color: #94a3b8;">
              <summary style="cursor: pointer; color: #60a5fa;">Ver Schema de Argumentos (JSON)</summary>
              <pre style="background: #090d16; padding: 8px; border-radius: 6px; margin-top: 6px; overflow-x: auto;">${escapeHtml(JSON.stringify(t.inputSchema || {}, null, 2))}</pre>
            </details>
          </div>
        `).join('');
      } catch (err) {
        document.getElementById('tools-modal-content').innerHTML = `<p style="color: var(--danger);">Falha de comunicação: ${err.message}</p>`;
      }
    }

    function copyText(str) {
      navigator.clipboard.writeText(str).then(() => {
        showToast('Copiado para a área de transferência!');
      }).catch(err => {
        alert('Texto: ' + str);
      });
    }

    function formatSeconds(secs) {
      if (secs === null || secs === undefined) return 'N/D';
      if (secs <= 0) return 'Expirado';
      const m = Math.floor(secs / 60);
      const s = secs % 60;
      if (m > 60) {
        const h = Math.floor(m / 60);
        return `${h}h ${m % 60}m`;
      }
      return `${m}m ${s}s`;
    }

    function escapeHtml(str) {
      if (!str) return '';
      return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
    }
  </script>
</body>
</html>
"""
