#!/usr/bin/env python3
"""
MCP OAuth Login Helper (Multi-Service & Multi-Tenant)
-----------------------------------------------------
Script interativo para autenticar contas no Lovable ou Uscreen via OAuth 2.1 com PKCE
e salvar automaticamente em data/tokens/{service}_{tenant}.json.
"""

import os
import sys
import json
import time
import socket
import argparse
import threading
import webbrowser
import urllib.parse
import urllib.request
from http.server import HTTPServer, BaseHTTPRequestHandler

# Importa módulos internos
from providers import (
    PROVIDERS,
    get_provider,
    generate_pkce,
    get_or_register_client,
    build_authorization_url,
    exchange_code_for_tokens
)
from storage import StorageManager

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
storage = StorageManager(DATA_DIR)

auth_code = None
auth_error = None
server_done = threading.Event()


class ReusableHTTPServer(HTTPServer):
    allow_reuse_address = True


class OAuthCallbackHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
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
                <body style="font-family: sans-serif; display: flex; align-items: center; justify-content: center; height: 100vh; background: #0b0f19; color: #f8fafc;">
                    <div style="text-align: center; padding: 40px; background: #111827; border: 1px solid rgba(255,255,255,0.1); border-radius: 12px; box-shadow: 0 4px 20px rgba(0,0,0,0.6);">
                        <h1 style="color: #34d399;">✅ Autenticado com Sucesso!</h1>
                        <p style="font-size: 16px; color: #cbd5e1; margin-top: 10px;">Os tokens foram capturados. Você já pode fechar esta aba e retornar ao terminal.</p>
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
                self.wfile.write(f"<h1>Erro na autenticacao: {auth_error}</h1>".encode("utf-8"))

            server_done.set()
        else:
            self.send_response(404)
            self.end_headers()


def find_available_port(preferred_port=8080, max_attempts=50) -> int:
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
    raise RuntimeError(f"Nenhuma porta livre encontrada entre {preferred_port} e {preferred_port + max_attempts}")


def notify_proxy_reconnect(service: str, tenant: str):
    """Tenta notificar o container do proxy para conectar a sessão imediatamente."""
    proxy_url = os.getenv("PROXY_URL", "http://localhost:8000")
    api_key = os.getenv("INTERNAL_API_KEY", "minha_chave_secreta_123")
    try:
        req = urllib.request.Request(
            f"{proxy_url}/{service}/{tenant}/reconnect",
            data=b"{}",
            headers={"Content-Type": "application/json", "x-api-key": api_key}
        )
        with urllib.request.urlopen(req, timeout=4) as resp:
            if resp.status == 200:
                print(f"🔄 Proxy Docker notificado: sessão ({service}, {tenant}) reconectada com sucesso!")
    except Exception:
        pass


def main():
    parser = argparse.ArgumentParser(description="Autenticador OAuth 2.1 Multi-Tenant para Lovable e Uscreen")
    parser.add_argument("--service", choices=["lovable", "uscreen"], help="Serviço para autenticar (lovable ou uscreen)")
    parser.add_argument("--tenant", default=None, help="Identificador do tenant (ex: default, empresa_a)")
    parser.add_argument("--port", type=int, default=8080, help="Porta local para escutar o callback")
    parser.add_argument("--manual", action="store_true", help="Modo manual para VPS sem redirecionamento automático")
    args = parser.parse_args()

    print("\n" + "="*60)
    print(" 🚀 MCP Multi-Tenant OAuth Helper (Lovable & Uscreen)")
    print("="*60 + "\n")

    # Escolha do serviço se não informado
    service = args.service
    if not service:
        print("Selecione o serviço que deseja conectar:")
        print("  1) Lovable")
        print("  2) Uscreen")
        choice = input("Digite 1 ou 2 [padrão: 1]: ").strip()
        service = "uscreen" if choice == "2" else "lovable"

    provider = get_provider(service)

    # Escolha do tenant
    tenant = args.tenant
    if not tenant:
        tenants = storage.get_all_tenants()
        print(f"\nTenants disponíveis: {list(tenants.keys())}")
        tenant_input = input("Informe o ID do tenant [padrão: default]: ").strip()
        tenant = tenant_input if tenant_input else "default"

    # Garante que o tenant existe
    if not storage.get_tenant(tenant):
        name_input = input(f"Nome amigável para o novo tenant '{tenant}': ").strip()
        storage.create_or_update_tenant(tenant, name_input or f"Tenant {tenant}")

    print(f"\n🎯 Conectando serviço: {provider.name.upper()} | Tenant: {tenant}")

    # Modo manual ou servidor local
    if args.manual:
        redirect_uri = "http://localhost:8080/callback"
        client_id, client_secret = get_or_register_client(provider, redirect_uri, storage.data_dir)
        code_verifier, code_challenge, state = generate_pkce()
        auth_url = build_authorization_url(provider, client_id, redirect_uri, code_challenge, state)

        print("\nAbra a seguinte URL no seu navegador para autorizar:")
        print("-" * 60)
        print(auth_url)
        print("-" * 60)
        code = input("\nApós autorizar, cole aqui o código (code=...) da barra de endereços: ").strip()
        if "code=" in code:
            code = urllib.parse.parse_qs(urllib.parse.urlparse(code).query).get("code", [code])[0]
    else:
        callback_port = find_available_port(args.port)
        redirect_uri = f"http://localhost:{callback_port}/callback"

        client_id, client_secret = get_or_register_client(provider, redirect_uri, storage.data_dir)
        code_verifier, code_challenge, state = generate_pkce()
        auth_url = build_authorization_url(provider, client_id, redirect_uri, code_challenge, state)

        httpd = ReusableHTTPServer(("0.0.0.0", callback_port), OAuthCallbackHandler)
        server_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        server_thread.start()

        print(f"\nServidor de callback escutando em: {redirect_uri}")
        print("Abrindo navegador para autorização...")
        try:
            webbrowser.open(auth_url)
        except Exception:
            pass

        print("\nSe o navegador não abriu automaticamente, acesse:")
        print(auth_url)
        print("\nAguardando autorização no navegador (Ctrl+C para cancelar)...")

        try:
            server_done.wait(timeout=300)
        except KeyboardInterrupt:
            print("\nCancelado pelo usuário.")
            httpd.shutdown()
            sys.exit(1)

        httpd.shutdown()

        if auth_error:
            print(f"\n❌ Erro durante a autorização: {auth_error}")
            sys.exit(1)

        if not auth_code:
            print("\n⚠️ Tempo esgotado aguardando autorização.")
            sys.exit(1)

        code = auth_code

    print("\n🔄 Trocando código de autorização por tokens...")
    try:
        tokens = exchange_code_for_tokens(
            provider=provider,
            client_id=client_id,
            client_secret=client_secret,
            code=code,
            code_verifier=code_verifier,
            redirect_uri=redirect_uri
        )
    except Exception as e:
        print(f"\n❌ Erro ao obter tokens: {e}")
        sys.exit(1)

    tokens["expires_at"] = int(time.time()) + int(tokens.get("expires_in", 3600))
    tokens["refreshed_at"] = int(time.time())

    token_path = storage.get_token_path(service, tenant)
    storage.save_tokens(service, tenant, tokens)

    print("\n" + "="*60)
    print(f" ✅ CONEXÃO ESTABELECIDA COM SUCESSO!")
    print(f"  Serviço: {provider.name}")
    print(f"  Tenant:  {tenant}")
    print(f"  Salvo em: {token_path}")
    print(f"  Auto-refresh: {'Ativo (Refresh Token obtido)' if tokens.get('refresh_token') else 'Sem refresh token'}")
    print("="*60 + "\n")

    notify_proxy_reconnect(service, tenant)


if __name__ == "__main__":
    main()
