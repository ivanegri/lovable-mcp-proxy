"""
Serviço e Rotas de Ingestão de Webhooks da Meta Ads.
Implementa:
- Verificação de handshake com Meta: GET /webhooks/meta (hub.mode, hub.challenge, hub.verify_token)
- Validação de integridade criptográfica: SHA-256 via cabeçalho X-Hub-Signature-256
- Ingestão em background com hidratação automática via Graph API (Leads e Alertas)
- Disparo opcional de automação proativa (N8N webhook trigger ou Agent loop)
"""

import os
import hmac
import hashlib
import json
import asyncio
import logging
from typing import Optional, Dict, Any, List
from fastapi import APIRouter, Request, HTTPException, Query, Header, status, BackgroundTasks
from fastapi.responses import PlainTextResponse, JSONResponse
import httpx

from meta_database import MetaDatabase
from meta_client import MetaGraphClient
from storage import StorageManager

logger = logging.getLogger("mcp-proxy.meta_webhook")

webhook_router = APIRouter(prefix="/webhooks/meta", tags=["Meta Webhooks"])


def verify_meta_signature(app_secret: str, payload_bytes: bytes, signature_header: Optional[str]) -> bool:
    """Valida a assinatura HMAC SHA256 enviada pela Meta no cabeçalho X-Hub-Signature-256."""
    if not app_secret:
        # Se app_secret não foi configurado, loga aviso e permite em desenvolvimento
        logger.warning("META_APP_SECRET não definido; pulando validação estrita de X-Hub-Signature-256.")
        return True

    if not signature_header:
        logger.error("Assinatura X-Hub-Signature-256 ausente na requisição da Meta.")
        return False

    parts = signature_header.split("sha256=")
    if len(parts) != 2:
        return False
    expected_hash = parts[1].strip()

    computed_hash = hmac.new(
        key=app_secret.encode("utf-8"),
        msg=payload_bytes,
        digestmod=hashlib.sha256
    ).hexdigest()

    return hmac.compare_digest(computed_hash, expected_hash)


async def trigger_proactive_agent(tenant_id: str, event_type: str, data: Dict[str, Any]):
    """Dispara webhook proativo para N8N, Flowise ou agente autônomo se configurado."""
    agent_webhook_url = os.getenv("META_AGENT_TRIGGER_URL")
    if not agent_webhook_url:
        return

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(agent_webhook_url, json={
                "source": "meta_webhook",
                "tenant_id": tenant_id,
                "event_type": event_type,
                "payload": data
            })
            logger.info(f"Disparo proativo de webhook enviado para {agent_webhook_url}")
    except Exception as e:
        logger.warning(f"Erro ao disparar automação proativa para agente: {e}")


async def process_and_hydrate_event(
    meta_db: MetaDatabase,
    storage: StorageManager,
    tenant_id: str,
    raw_payload: Dict[str, Any]
):
    """Processa o evento em background, busca dados faltantes na Graph API e salva no SQLite."""
    try:
        object_type = raw_payload.get("object", "unknown")
        entries = raw_payload.get("entry", [])

        # Tenta obter token para hidratação
        tokens = storage.load_tokens("meta", tenant_id)
        access_token = tokens.get("access_token") if tokens else None
        graph_client = MetaGraphClient(access_token) if access_token else None

        for entry in entries:
            entry_id = entry.get("id")
            changes = entry.get("changes", [])

            for change in changes:
                field = change.get("field")
                value = change.get("value", {})

                hydrated = None

                # 1. Leadgen (Leads de anúncios do Facebook/Instagram)
                if field == "leadgen" or "leadgen_id" in value:
                    leadgen_id = str(value.get("leadgen_id"))
                    page_id = str(value.get("page_id", entry_id))
                    form_id = str(value.get("form_id"))
                    ad_id = str(value.get("ad_id"))
                    created_time = value.get("created_time")

                    if graph_client and leadgen_id:
                        try:
                            lead_data = await graph_client.hydrate_lead(leadgen_id)
                            hydrated = lead_data
                            meta_db.save_lead(
                                tenant_id=tenant_id,
                                leadgen_id=leadgen_id,
                                page_id=page_id,
                                form_id=form_id or lead_data.get("form_id"),
                                ad_id=ad_id or lead_data.get("ad_id"),
                                ad_name=lead_data.get("ad_name"),
                                campaign_id=lead_data.get("campaign_id"),
                                campaign_name=lead_data.get("campaign_name"),
                                created_time=lead_data.get("created_time") or created_time,
                                field_data=lead_data.get("field_data"),
                                raw_data=lead_data
                            )
                            logger.info(f"✅ Lead {leadgen_id} hidratado e persistido com sucesso para tenant '{tenant_id}'!")
                        except Exception as he:
                            logger.warning(f"Erro ao hidratar lead {leadgen_id}: {he}")
                            meta_db.save_lead(tenant_id, leadgen_id, page_id=page_id, form_id=form_id, ad_id=ad_id)
                    else:
                        meta_db.save_lead(tenant_id, leadgen_id, page_id=page_id, form_id=form_id, ad_id=ad_id)

                    asyncio.create_task(trigger_proactive_agent(tenant_id, "new_lead", hydrated or value))

                # 2. Alertas de Ad Account: Anúncios reprovados ou com problemas (with_issues_ad_objects)
                elif field in ("with_issues_ad_objects", "in_process_ad_objects"):
                    ad_id = str(value.get("ad_id") or value.get("object_id") or entry_id)
                    message = f"Anúncio reportou alteração de status/problema ({field})"
                    if graph_client and ad_id:
                        try:
                            ad_info = await graph_client.hydrate_ad_issues(ad_id)
                            hydrated = ad_info
                            meta_db.record_alert(
                                tenant_id=tenant_id,
                                ad_account_id=entry_id,
                                alert_type="with_issues",
                                object_id=ad_id,
                                object_name=ad_info.get("name"),
                                message=f"Anúncio '{ad_info.get('name', ad_id)}' com issues: {ad_info.get('effective_status')}",
                                details=ad_info
                            )
                        except Exception as ae:
                            logger.warning(f"Erro ao hidratar status do anúncio {ad_id}: {ae}")
                            meta_db.record_alert(tenant_id, entry_id, "with_issues", ad_id, None, message, value)
                    else:
                        meta_db.record_alert(tenant_id, entry_id, "with_issues", ad_id, None, message, value)

                    asyncio.create_task(trigger_proactive_agent(tenant_id, "ad_issue", hydrated or value))

                # 3. Fadiga de Criativo (creative_fatigue)
                elif field == "creative_fatigue":
                    object_id = str(value.get("ad_id") or value.get("object_id") or entry_id)
                    message = "Fadiga de criativo identificada pela Meta."
                    meta_db.record_alert(
                        tenant_id=tenant_id,
                        ad_account_id=entry_id,
                        alert_type="creative_fatigue",
                        object_id=object_id,
                        object_name=value.get("ad_name"),
                        message=message,
                        details=value
                    )
                    asyncio.create_task(trigger_proactive_agent(tenant_id, "creative_fatigue", value))

                # Salva o evento genérico no log
                meta_db.record_webhook_event(
                    tenant_id=tenant_id,
                    object_type=object_type,
                    entry_id=entry_id,
                    field=field,
                    raw_payload=raw_payload,
                    hydrated_data=hydrated,
                    status="processed"
                )

        if graph_client:
            await graph_client.close()

    except Exception as e:
        logger.error(f"Erro ao processar webhook em background: {e}", exc_info=True)


def register_meta_webhook_routes(app, meta_db: MetaDatabase, storage: StorageManager):
    """Registra endpoints de Webhook para a Meta no FastAPI."""

    @app.get("/webhooks/meta")
    @app.get("/webhooks/meta/")
    @app.get("/{tenant}/webhooks/meta")
    async def meta_webhook_verification(
        request: Request,
        hub_mode: Optional[str] = Query(None, alias="hub.mode"),
        hub_challenge: Optional[str] = Query(None, alias="hub.challenge"),
        hub_verify_token: Optional[str] = Query(None, alias="hub.verify_token"),
        tenant: Optional[str] = None
    ):
        """Verificação de Handshake da Meta (hub.challenge)."""
        expected_token = os.getenv("META_VERIFY_TOKEN", "meta_mcp_hub_token")
        if hub_mode == "subscribe" and hub_verify_token == expected_token:
            logger.info("✅ Handshake de Webhook da Meta verificado com sucesso!")
            return PlainTextResponse(content=hub_challenge or "")

        logger.warning(f"❌ Falha na verificação de webhook Meta: token recebido '{hub_verify_token}' != esperado.")
        raise HTTPException(status_code=403, detail="Verification token mismatch")

    @app.post("/webhooks/meta")
    @app.post("/webhooks/meta/")
    @app.post("/{tenant}/webhooks/meta")
    async def meta_webhook_ingest(
        request: Request,
        background_tasks: BackgroundTasks,
        x_hub_signature_256: Optional[str] = Header(None, alias="X-Hub-Signature-256"),
        tenant: Optional[str] = None
    ):
        """Recebe notificações HTTP POST da Meta, valida a assinatura e despacha processamento em background."""
        payload_bytes = await request.body()
        app_secret = os.getenv("META_APP_SECRET", "")

        if not verify_meta_signature(app_secret, payload_bytes, x_hub_signature_256):
            raise HTTPException(status_code=401, detail="Invalid X-Hub-Signature-256")

        try:
            payload_json = json.loads(payload_bytes.decode("utf-8"))
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON body")

        target_tenant = tenant or "default"
        # Executa hidratação e gravação em background para responder à Meta em < 500ms
        background_tasks.add_task(process_and_hydrate_event, meta_db, storage, target_tenant, payload_json)

        return JSONResponse({"status": "received", "tenant": target_tenant})
