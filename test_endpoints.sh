#!/usr/bin/env bash
# Script utilitário para testar os endpoints do Lovable MCP Proxy

API_KEY=${INTERNAL_API_KEY:-"minha_chave_secreta_123"}
BASE_URL=${PROXY_URL:-"http://localhost:8000"}

echo "----------------------------------------"
echo "1. Verificando Saúde do Proxy (/health)"
echo "----------------------------------------"
curl -s -X GET "${BASE_URL}/health" | (which jq >/dev/null && jq . || cat)
echo -e "\n"

echo "----------------------------------------"
echo "2. Listando Ferramentas Disponíveis (/tools)"
echo "----------------------------------------"
curl -s -X GET "${BASE_URL}/tools" \
  -H "x-api-key: ${API_KEY}" | (which jq >/dev/null && jq . || cat)
echo -e "\n"

echo "----------------------------------------"
echo "3. Chamando list_workspaces (/tools/list_workspaces)"
echo "----------------------------------------"
curl -s -X POST "${BASE_URL}/tools/list_workspaces" \
  -H "Content-Type: application/json" \
  -H "x-api-key: ${API_KEY}" \
  -d '{}' | (which jq >/dev/null && jq . || cat)
echo -e "\n"
