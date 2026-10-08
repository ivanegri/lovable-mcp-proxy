#!/usr/bin/env python3
"""
Lovable OAuth Helper
--------------------
Script interativo para autenticar sua conta no Lovable, obter o access_token
e o refresh_token via OAuth 2.1 com PKCE e salvar automaticamente em data/tokens.json.
"""

import os
import json
import time
import base64
import hashlib
import secrets
import threading
import webbrowser
import urllib.parse
import urllib.request
from http.server import HTTPServer, BaseHTTPRequestHandler

# Diretórios e Arquivos
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
TOKENS_FILE = os.path.join(DATA_DIR, "tokens.json")
CLIENT_FILE = os.path.join(DATA_DIR, "oauth_client.json")

# Endpoints Oficiais do Lovable OAuth 2.1
REGISTRATION_ENDPOINT = "https://lovable.dev/oauth/register"
AUTHORIZE_ENDPOINT = "https://lovable.dev/oauth/authorize"
TOKEN_ENDPOINT = "https://lovable.dev/oauth/token"

CALLBACK_PORT = 8080
REDIRECT_URI = f"http://localhost:{CALLBACK_PORT}/callback"
SCOPES = "offline projects:read projects:write workspaces:read workspaces:write"

auth_code = None
auth_error = None
server_done = threading.Event()


class OAuthCallbackHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Suprime logs normais do HTTP server
        pass

    def do_GET(self):
        global auth_code, auth_error
        parsed_url = urllib.parse.urlparse(self.path)

        if parsed_url.path == "/callback":
            params = urllib.parse.parse_qs(parsed_url.query)
            if "code" in params:
                auth_code = params["code"][0]
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                html = """
                <html>
                <body style="font-family: sans-serif; display: flex; align-items: center; justify-content: center; height: 100vh; background: #0f172a; color: #f8fafc;">
                    <div style="text-align: center; padding: 40px; background: #1e293b; border-radius: 12px; box-shadow: 0 4px 20px rgba(0,0,0,0.5);">
                        <h1 style="color: #4ade80;">✅ Autenticado com Sucesso!</h1>
                        <p style="font-size: 16px; color: #cbd5e1;">O token do Lovable foi capturado. Você já pode fechar esta aba e retornar ao terminal.</p>
                    </div>
                </body>
                </html>
                """
                self.wfile.write(html.encode("utf-8"))
            elif "error" in params:
                auth_error = params.get("error_description", params["error"])[0]
                self.send_response(400)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(f"<h1>Erro na autenticação: {auth_error}</h1>".encode("utf-8"))

            server_done.set()
        else:
            self.send_response(404)
            self.end_headers()


def get_or_create_client():
    """Gera ou recupera as credenciais de cliente OAuth dinâmico no Lovable."""
    os.makedirs(DATA_DIR, exist_ok=True)
    if os.path.exists(CLIENT_FILE):
        try:
            with open(CLIENT_FILE, "r") as f:
                data = json.load(f)
                if data.get("client_id") and data.get("client_secret"):
                    return data["client_id"], data["client_secret"]
        except Exception:
            pass

    print("🔧 Registrando cliente OAuth temporário no Lovable...")
    payload = json.dumps({
        "client_name": "Lovable MCP Proxy Bridge",
        "redirect_uris": [REDIRECT_URI]
    }).encode("utf-8")

    req = urllib.request.Request(
        REGISTRATION_ENDPOINT,
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": "Lovable-MCP-Proxy"}
    )
    with urllib.request.urlopen(req) as response:
        client_data = json.load(response)

    client_id = client_data["client_id"]
    client_secret = client_data["client_secret"]

    with open(CLIENT_FILE, "w") as f:
        json.dump({
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": REDIRECT_URI
        }, f, indent=2)

    return client_id, client_secret


def generate_pkce():
    """Gera code_verifier e code_challenge (S256) conforme a especificação OAuth 2.1."""
    code_verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(code_verifier.encode("utf-8")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).decode("utf-8").replace("=", "")
    state = secrets.token_urlsafe(16)
    return code_verifier, code_challenge, state


def exchange_code_for_tokens(client_id, client_secret, code, code_verifier):
    """Troca o authorization_code pelo access_token e refresh_token."""
    data = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "code_verifier": code_verifier,
        "client_id": client_id,
        "client_secret": client_secret
    }).encode("utf-8")

    # Autenticação HTTP Basic com client_id e client_secret
    creds = f"{client_id}:{client_secret}".encode("utf-8")
    auth_header = "Basic " + base64.b64encode(creds).decode("utf-8")

    req = urllib.request.Request(
        TOKEN_ENDPOINT,
        data=data,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": auth_header,
            "User-Agent": "Lovable-MCP-Proxy"
        }
    )

    with urllib.request.urlopen(req) as resp:
        return json.load(resp)


def main():
    print("==================================================")
    print("🔑 Assistente de Autenticação OAuth 2.1 - Lovable")
    print("==================================================")

    client_id, client_secret = get_or_create_client()
    code_verifier, code_challenge, state = generate_pkce()

    # Prepara o servidor local de callback
    server = HTTPServer(("0.0.0.0", CALLBACK_PORT), OAuthCallbackHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "state": state
    }
    authorize_url = f"{AUTHORIZE_ENDPOINT}?{urllib.parse.urlencode(params)}"

    print("\n👉 Abrindo navegador para autorização do Lovable...")
    print(f"URL de Autorização:\n{authorize_url}\n")
    try:
        webbrowser.open(authorize_url)
    except Exception:
        pass

    print(f"Aguardando autorização no navegador (escutando em {REDIRECT_URI})...")
    print("(Pressione Ctrl+C para cancelar)\n")

    try:
        # Aguarda até o callback ser chamado
        while not server_done.is_set():
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nOperação cancelada pelo usuário.")
        server.shutdown()
        return

    server.shutdown()

    if auth_error:
        print(f"❌ Erro durante autorização: {auth_error}")
        return

    if not auth_code:
        print("❌ Nenhum código de autorização foi capturado.")
        return

    print("🔄 Código recebido! Solicitando access_token e refresh_token...")
    try:
        tokens = exchange_code_for_tokens(client_id, client_secret, auth_code, code_verifier)
    except Exception as e:
        print(f"❌ Erro ao trocar código por tokens: {e}")
        return

    access_token = tokens.get("access_token")
    refresh_token = tokens.get("refresh_token")
    expires_in = tokens.get("expires_in", 3600)

    if not access_token:
        print("❌ Resposta da API não contém access_token:", tokens)
        return

    tokens_payload = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": tokens.get("token_type", "Bearer"),
        "expires_at": int(time.time()) + expires_in,
        "created_at": int(time.time())
    }

    with open(TOKENS_FILE, "w", encoding="utf-8") as f:
        json.dump(tokens_payload, f, indent=2)

    print(f"✅ Tokens salvos com sucesso em: {TOKENS_FILE}")
    print(f"   • Access Token:  {access_token[:15]}...{access_token[-10:]}")
    if refresh_token:
        print(f"   • Refresh Token: {refresh_token[:15]}...{refresh_token[-10:]}")
    print(f"   • Expira em:     {expires_in} segundos (~{expires_in//3600} horas)")

    # Tenta acionar o reconnect do container local se estiver ativo
    try:
        req_reconnect = urllib.request.Request(
            "http://localhost:8000/reconnect",
            data=b"{}",
            headers={"Content-Type": "application/json", "x-api-key": os.getenv("INTERNAL_API_KEY", "minha_chave_secreta_123")}
        )
        with urllib.request.urlopen(req_reconnect, timeout=3) as r:
            print("🚀 Proxy Docker notificado e reconectado com sucesso!")
    except Exception:
        print("💡 Dica: Inicie o Docker com 'docker compose up -d' para ativar o proxy.")

    print("\n🎉 Tudo pronto! Seu proxy MCP Lovable agora está 100% autenticado.")


if __name__ == "__main__":
    main()
