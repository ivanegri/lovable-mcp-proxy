#!/usr/bin/env python3
"""
Lovable OAuth Helper
--------------------
Script interativo para autenticar sua conta no Lovable, obter o access_token
e o refresh_token via OAuth 2.1 com PKCE e salvar automaticamente em data/tokens.json.
Suporta execução local ou em VPS remota (com detecção de porta livre e modo manual).
"""

import os
import sys
import json
import time
import socket
import base64
import hashlib
import secrets
import argparse
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
SCOPES = "offline projects:read projects:write workspaces:read workspaces:write"
# IMPORTANTE: o registro dinâmico do Lovable usa por padrão apenas ["authorization_code"].
# Sem "refresh_token" aqui, o servidor NUNCA emite refresh_token (mesmo com escopo 'offline').
GRANT_TYPES = ["authorization_code", "refresh_token"]

auth_code = None
auth_error = None
server_done = threading.Event()


class ReusableHTTPServer(HTTPServer):
    allow_reuse_address = True


class OAuthCallbackHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Suprime logs de acesso HTTP
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


def find_available_port(preferred_port=8080, max_attempts=50) -> int:
    """Encontra uma porta TCP disponível, testando a preferencial e alternativas."""
    ports_to_try = [preferred_port] + [p for p in range(preferred_port + 1, preferred_port + max_attempts) if p != preferred_port]
    for p in ports_to_try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("0.0.0.0", p))
            s.close()
            return p
        except OSError:
            s.close()
            continue
    raise RuntimeError(f"Não foi possível encontrar nenhuma porta TCP livre entre {preferred_port} e {preferred_port + max_attempts}")


def get_or_create_client(redirect_uri: str):
    """Gera ou recupera as credenciais de cliente OAuth dinâmico no Lovable para o redirect_uri especificado."""
    os.makedirs(DATA_DIR, exist_ok=True)
    if os.path.exists(CLIENT_FILE):
        try:
            with open(CLIENT_FILE, "r") as f:
                data = json.load(f)
                if (
                    data.get("client_id")
                    and data.get("client_secret")
                    and data.get("redirect_uri") == redirect_uri
                    and "refresh_token" in (data.get("grant_types") or [])
                ):
                    return data["client_id"], data["client_secret"]
                if data.get("client_id") and "refresh_token" not in (data.get("grant_types") or []):
                    print("ℹ️  Cliente OAuth salvo não permite refresh_token. Registrando um novo...")
        except Exception:
            pass

    print(f"🔧 Registrando cliente OAuth no Lovable para '{redirect_uri}'...")
    payload = json.dumps({
        "client_name": "Lovable MCP Proxy Bridge",
        "redirect_uris": [redirect_uri],
        "grant_types": GRANT_TYPES,
        "response_types": ["code"],
        "token_endpoint_auth_method": "client_secret_basic",
        "scope": SCOPES
    }).encode("utf-8")

    req = urllib.request.Request(
        REGISTRATION_ENDPOINT,
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": "Lovable-MCP-Proxy"}
    )
    with urllib.request.urlopen(req, timeout=15) as response:
        client_data = json.load(response)

    client_id = client_data["client_id"]
    client_secret = client_data["client_secret"]
    granted_grants = client_data.get("grant_types") or []
    if "refresh_token" not in granted_grants:
        print(f"⚠️  O Lovable registrou o cliente sem o grant 'refresh_token' (grant_types={granted_grants}).")

    with open(CLIENT_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_types": granted_grants
        }, f, indent=2)

    return client_id, client_secret


def generate_pkce():
    """Gera code_verifier e code_challenge (S256) conforme a especificação OAuth 2.1."""
    code_verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(code_verifier.encode("utf-8")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).decode("utf-8").replace("=", "")
    state = secrets.token_urlsafe(16)
    return code_verifier, code_challenge, state


def exchange_code_for_tokens(client_id, client_secret, code, code_verifier, redirect_uri):
    """Troca o authorization_code pelo access_token e refresh_token."""
    data = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
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

    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.load(resp)


def manual_input_listener():
    """Permite que o usuário cole a URL redirecionada ou o código diretamente no terminal."""
    global auth_code, auth_error
    try:
        while not server_done.is_set():
            prompt_text = "\n📋 Se estiver em VPS remota: após autorizar no navegador, cole a URL redirecionada (ou o code) aqui:\n> "
            sys.stdout.write(prompt_text)
            sys.stdout.flush()
            user_input = sys.stdin.readline()
            if not user_input:
                break
            val = user_input.strip()
            if val and not server_done.is_set():
                if "code=" in val:
                    # O usuário colou a URL inteira: http://localhost:8080/callback?code=XXXX&state=YYYY
                    parsed = urllib.parse.urlparse(val)
                    params = urllib.parse.parse_qs(parsed.query)
                    if "code" in params:
                        auth_code = params["code"][0]
                        server_done.set()
                        break
                    elif "error" in params:
                        auth_error = params.get("error_description", params["error"])[0]
                        server_done.set()
                        break
                elif len(val) >= 10:
                    # O usuário colou apenas o code
                    auth_code = val
                    server_done.set()
                    break
    except Exception:
        pass


def main():
    parser = argparse.ArgumentParser(description="Lovable OAuth 2.1 CLI Helper")
    parser.add_argument("--port", "-p", type=int, default=None, help="Porta local para o callback (padrão: auto-detectar a partir de 8080)")
    parser.add_argument("--no-browser", action="store_true", help="Não tenta abrir navegador automaticamente")
    args = parser.parse_args()

    print("==================================================")
    print("🔑 Assistente de Autenticação OAuth 2.1 - Lovable")
    print("==================================================")

    # 1. Determina a porta disponível
    preferred = args.port or int(os.getenv("AUTH_CALLBACK_PORT", "8080"))
    try:
        port = find_available_port(preferred)
        if port != preferred:
            print(f"ℹ️  A porta {preferred} já está em uso por outro serviço na sua máquina.")
            print(f"👉 Usando a porta livre detectada: {port}")
        else:
            print(f"ℹ️  Usando a porta local: {port}")
    except Exception as e:
        print(f"❌ Erro ao obter porta: {e}")
        return

    redirect_uri = f"http://localhost:{port}/callback"

    # 2. Registra o cliente OAuth
    try:
        client_id, client_secret = get_or_create_client(redirect_uri)
    except Exception as e:
        print(f"❌ Falha ao registrar cliente OAuth dinâmico no Lovable: {e}")
        return

    code_verifier, code_challenge, state = generate_pkce()

    # 3. Inicia servidor HTTP local para capturar o callback automaticamente
    server = None
    try:
        server = ReusableHTTPServer(("0.0.0.0", port), OAuthCallbackHandler)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
    except Exception as e:
        print(f"⚠️ Não foi possível iniciar servidor HTTP local na porta {port}: {e}")
        print("Modo de colagem manual ativo.")

    # 4. Gera URL de autorização
    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": SCOPES,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "state": state
    }
    authorize_url = f"{AUTHORIZE_ENDPOINT}?{urllib.parse.urlencode(params)}"

    print("\n--------------------------------------------------")
    print("🌐 URL DE AUTORIZAÇÃO:")
    print("--------------------------------------------------")
    print(f"\n{authorize_url}\n")
    print("--------------------------------------------------")

    if not args.no_browser:
        try:
            webbrowser.open(authorize_url)
            print("👉 Tentando abrir o navegador automaticamente...")
        except Exception:
            pass

    print("\nInstruções:")
    print("1. Abra o link acima no seu navegador (se não abriu sozinho).")
    print("2. Faça login no Lovable e clique em Autorizar.")
    print("3. Se estiver na VPS: após autorizar, o navegador tentará abrir 'localhost'.")
    print("   Copie a URL da barra de endereços do navegador e cole abaixo!")
    print("(Pressione Ctrl+C a qualquer momento para sair)")

    # Inicia thread de escuta para colagem manual
    input_thread = threading.Thread(target=manual_input_listener, daemon=True)
    input_thread.start()

    try:
        while not server_done.is_set():
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n\nOperação cancelada pelo usuário.")
        if server:
            server.shutdown()
        return

    if server:
        server.shutdown()

    if auth_error:
        print(f"\n❌ Erro durante autorização: {auth_error}")
        return

    if not auth_code:
        print("\n❌ Nenhum código de autorização foi capturado.")
        return

    print("\n🔄 Código recebido com sucesso!")
    print("📡 Solicitando access_token e refresh_token ao Lovable...")
    try:
        tokens = exchange_code_for_tokens(client_id, client_secret, auth_code, code_verifier, redirect_uri)
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

    print(f"\n✅ Tokens salvos com sucesso em: {TOKENS_FILE}")
    print(f"   • Access Token:  {access_token[:15]}...{access_token[-10:]}")
    if refresh_token:
        print(f"   • Refresh Token: {refresh_token[:15]}...{refresh_token[-10:]}")
    else:
        print("   ⚠️ NENHUM refresh_token recebido! A renovação automática NÃO funcionará.")
        print(f"      Escopo concedido: {tokens.get('scope')!r} (precisa conter 'offline').")
        print(f"      Apague {CLIENT_FILE} e rode o script novamente.")
    print(f"   • Expira em:     {expires_in} segundos (~{expires_in//3600} horas)")

    # Notifica o proxy local se estiver ativo
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
