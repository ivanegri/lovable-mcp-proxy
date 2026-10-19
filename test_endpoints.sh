#!/usr/bin/env bash
# Script utilitário para testar os endpoints do MCP Proxy Multi-Tenant (Lovable & Uscreen)

API_KEY=${INTERNAL_API_KEY:-"minha_chave_secreta_123"}
BASE_URL=${PROXY_URL:-"http://localhost:8000"}

echo "=========================================================="
echo " 🧪 Teste do MCP Multi-Tenant Bridge (Lovable & Uscreen)"
echo "=========================================================="

echo -e "\n1. Verificando Saúde Geral do Proxy (/health)"
echo "----------------------------------------------------------"
curl -s -X GET "${BASE_URL}/health" | (which jq >/dev/null && jq . || cat)

echo -e "\n\n2. Listando Tenants e Status via API (/api/tenants)"
echo "----------------------------------------------------------"
curl -s -X GET "${BASE_URL}/api/tenants" \
  -H "x-api-key: ${API_KEY}" | (which jq >/dev/null && jq . || cat)

echo -e "\n\n3. Consultando Ferramentas do Lovable (/lovable/default/tools)"
echo "----------------------------------------------------------"
curl -s -X GET "${BASE_URL}/lovable/default/tools" \
  -H "x-api-key: ${API_KEY}" | (which jq >/dev/null && jq . || cat)

echo -e "\n\n4. Consultando Ferramentas do Uscreen (/uscreen/default/tools)"
echo "----------------------------------------------------------"
curl -s -X GET "${BASE_URL}/uscreen/default/tools" \
  -H "x-api-key: ${API_KEY}" | (which jq >/dev/null && jq . || cat)

echo -e "\n\n5. Verificando Painel Visual Web (/)"
echo "----------------------------------------------------------"
STATUS_CODE=$(curl -s -o /dev/null -w "%{http_code}" "${BASE_URL}/")
echo "HTTP Status do Dashboard: ${STATUS_CODE} (Acesse ${BASE_URL} no navegador para autenticar)"

echo -e "\n=========================================================="
echo " Concluído!"
echo "=========================================================="
