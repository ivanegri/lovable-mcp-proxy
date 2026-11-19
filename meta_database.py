"""
Módulo de Banco de Dados SQLite para Persistência e Cache de Eventos e Webhooks da Meta Ads.
Armazena webhooks recebidos (hub.challenge, leadgen, ad_account alerts) e dados enriquecidos da Graph API.
"""

import os
import json
import time
import sqlite3
import logging
from typing import Optional, Dict, Any, List

logger = logging.getLogger("mcp-proxy.meta_db")


class MetaDatabase:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=15)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        """Inicializa as tabelas de eventos, leads, alertas e cache de campanhas."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            # Tabela de eventos genéricos de Webhook da Meta
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS meta_webhook_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id TEXT NOT NULL,
                    object_type TEXT NOT NULL,       -- 'ad_account', 'page', etc.
                    entry_id TEXT,                  -- ad_account_id ou page_id
                    field TEXT,                     -- 'with_issues_ad_objects', 'leadgen', 'creative_fatigue', etc.
                    raw_payload TEXT NOT NULL,      -- JSON original do webhook
                    hydrated_data TEXT,             -- JSON enriquecido obtido via Graph API
                    received_at INTEGER NOT NULL,   -- timestamp unix
                    status TEXT DEFAULT 'pending'   -- 'pending', 'processed', 'error'
                )
            """)

            # Tabela de Leads recebidos via Webhook (leadgen)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS meta_leads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id TEXT NOT NULL,
                    leadgen_id TEXT UNIQUE NOT NULL,
                    page_id TEXT,
                    form_id TEXT,
                    ad_id TEXT,
                    ad_name TEXT,
                    campaign_id TEXT,
                    campaign_name TEXT,
                    created_time INTEGER,
                    field_data TEXT,                -- JSON com os campos preenchidos do lead (nome, email, telefone...)
                    raw_data TEXT,                  -- Resposta bruta da Graph API
                    received_at INTEGER NOT NULL
                )
            """)

            # Tabela de Alertas de Anúncios e Contas (with_issues, creative_fatigue, etc.)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS meta_ad_alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id TEXT NOT NULL,
                    ad_account_id TEXT,
                    alert_type TEXT NOT NULL,       -- 'creative_fatigue', 'with_issues', 'disapproved', 'billing_failure'
                    object_id TEXT,                 -- ad_id, adset_id ou campaign_id
                    object_name TEXT,
                    message TEXT,
                    details TEXT,                   -- JSON com detalhes
                    created_at INTEGER NOT NULL,
                    resolved INTEGER DEFAULT 0
                )
            """)

            # Tabela de Cache de Insights / Métricas para leitura rápida por Resources MCP
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS meta_insights_cache (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id TEXT NOT NULL,
                    ad_account_id TEXT NOT NULL,
                    level TEXT NOT NULL,            -- 'account', 'campaign', 'adset', 'ad'
                    date_preset TEXT NOT NULL,      -- 'today', 'yesterday', 'last_7d', etc.
                    data TEXT NOT NULL,             -- JSON com as métricas retornadas da Graph API
                    updated_at INTEGER NOT NULL,
                    UNIQUE(tenant_id, ad_account_id, level, date_preset)
                )
            """)

            # Índices para consultas rápidas
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_tenant_field ON meta_webhook_events(tenant_id, field, received_at)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_leads_tenant ON meta_leads(tenant_id, received_at)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_alerts_tenant ON meta_ad_alerts(tenant_id, alert_type, created_at)")
            conn.commit()

    def record_webhook_event(
        self,
        tenant_id: str,
        object_type: str,
        entry_id: Optional[str],
        field: Optional[str],
        raw_payload: Dict[str, Any],
        hydrated_data: Optional[Dict[str, Any]] = None,
        status: str = "received"
    ) -> int:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO meta_webhook_events (tenant_id, object_type, entry_id, field, raw_payload, hydrated_data, received_at, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                tenant_id,
                object_type,
                entry_id,
                field,
                json.dumps(raw_payload, ensure_ascii=False),
                json.dumps(hydrated_data, ensure_ascii=False) if hydrated_data else None,
                int(time.time()),
                status
            ))
            conn.commit()
            return cursor.lastrowid

    def save_lead(
        self,
        tenant_id: str,
        leadgen_id: str,
        page_id: Optional[str] = None,
        form_id: Optional[str] = None,
        ad_id: Optional[str] = None,
        ad_name: Optional[str] = None,
        campaign_id: Optional[str] = None,
        campaign_name: Optional[str] = None,
        created_time: Optional[int] = None,
        field_data: Optional[List[Dict[str, Any]]] = None,
        raw_data: Optional[Dict[str, Any]] = None
    ):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO meta_leads (
                    tenant_id, leadgen_id, page_id, form_id, ad_id, ad_name, campaign_id, campaign_name,
                    created_time, field_data, raw_data, received_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(leadgen_id) DO UPDATE SET
                    field_data=excluded.field_data,
                    raw_data=excluded.raw_data,
                    ad_name=COALESCE(excluded.ad_name, meta_leads.ad_name),
                    campaign_name=COALESCE(excluded.campaign_name, meta_leads.campaign_name)
            """, (
                tenant_id,
                leadgen_id,
                page_id,
                form_id,
                ad_id,
                ad_name,
                campaign_id,
                campaign_name,
                created_time or int(time.time()),
                json.dumps(field_data or [], ensure_ascii=False),
                json.dumps(raw_data or {}, ensure_ascii=False),
                int(time.time())
            ))
            conn.commit()

    def record_alert(
        self,
        tenant_id: str,
        ad_account_id: Optional[str],
        alert_type: str,
        object_id: Optional[str],
        object_name: Optional[str],
        message: str,
        details: Optional[Dict[str, Any]] = None
    ):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO meta_ad_alerts (tenant_id, ad_account_id, alert_type, object_id, object_name, message, details, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                tenant_id,
                ad_account_id,
                alert_type,
                object_id,
                object_name,
                message,
                json.dumps(details or {}, ensure_ascii=False),
                int(time.time())
            ))
            conn.commit()

    def get_recent_alerts(self, tenant_id: str, limit: int = 50, unresolved_only: bool = False) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            query = "SELECT * FROM meta_ad_alerts WHERE tenant_id = ?"
            params = [tenant_id]
            if unresolved_only:
                query += " AND resolved = 0"
            query += " ORDER BY created_at DESC LIMIT ?"
            params.append(limit)
            cursor.execute(query, params)
            rows = cursor.fetchall()
            results = []
            for r in rows:
                item = dict(r)
                if item.get("details"):
                    try:
                        item["details"] = json.loads(item["details"])
                    except Exception:
                        pass
                results.append(item)
            return results

    def get_recent_leads(self, tenant_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM meta_leads WHERE tenant_id = ? ORDER BY received_at DESC LIMIT ?
            """, (tenant_id, limit))
            rows = cursor.fetchall()
            results = []
            for r in rows:
                item = dict(r)
                if item.get("field_data"):
                    try:
                        item["field_data"] = json.loads(item["field_data"])
                    except Exception:
                        pass
                if item.get("raw_data"):
                    try:
                        item["raw_data"] = json.loads(item["raw_data"])
                    except Exception:
                        pass
                results.append(item)
            return results

    def get_webhook_events(self, tenant_id: str, event_type: Optional[str] = None, hours: int = 24, limit: int = 100) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            since = int(time.time()) - (hours * 3600)
            query = "SELECT * FROM meta_webhook_events WHERE tenant_id = ? AND received_at >= ?"
            params = [tenant_id, since]
            if event_type:
                query += " AND (field = ? OR object_type = ?)"
                params.extend([event_type, event_type])
            query += " ORDER BY received_at DESC LIMIT ?"
            params.append(limit)
            cursor.execute(query, params)
            rows = cursor.fetchall()
            results = []
            for r in rows:
                item = dict(r)
                try:
                    item["raw_payload"] = json.loads(item["raw_payload"])
                except Exception:
                    pass
                if item.get("hydrated_data"):
                    try:
                        item["hydrated_data"] = json.loads(item["hydrated_data"])
                    except Exception:
                        pass
                results.append(item)
            return results

    def cache_insights(self, tenant_id: str, ad_account_id: str, level: str, date_preset: str, data: Any):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO meta_insights_cache (tenant_id, ad_account_id, level, date_preset, data, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(tenant_id, ad_account_id, level, date_preset) DO UPDATE SET
                    data=excluded.data,
                    updated_at=excluded.updated_at
            """, (tenant_id, ad_account_id, level, date_preset, json.dumps(data, ensure_ascii=False), int(time.time())))
            conn.commit()

    def get_cached_insights(self, tenant_id: str, ad_account_id: str, level: str, date_preset: str, max_age_seconds: int = 1800) -> Optional[Any]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT data, updated_at FROM meta_insights_cache
                WHERE tenant_id = ? AND ad_account_id = ? AND level = ? AND date_preset = ?
            """, (tenant_id, ad_account_id, level, date_preset))
            row = cursor.fetchone()
            if not row:
                return None
            if int(time.time()) - row["updated_at"] > max_age_seconds:
                return None
            try:
                return json.loads(row["data"])
            except Exception:
                return None
