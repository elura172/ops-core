from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.api.abebooks_service import AbeBooksService
from app.api.biblio_service import BiblioService
from app.api.operator import OperatorService
from app.config import Settings
from app.db.want_list import WantListStore
from app.observability.logging import get_logger
from app.vault import decrypt_credential

logger = get_logger("reconciliation_service")


class ReconciliationService:
    """Orchestrates scheduled multi-tenant channel polling, order ingestion,

    collision arbitration with 48h courtesy holds, and autonomous Corvus delisting cascades.
    """

    def __init__(
        self,
        operator: OperatorService,
        user_store: Any = None,
        settings: Settings | None = None,
        want_list_store: WantListStore | None = None,
        inventory_store: Any | None = None,
    ):
        self.operator = operator
        self.user_store = user_store
        self.settings = settings or Settings.from_env_permissive()
        self.want_list_store = want_list_store
        self.inventory_store = inventory_store

    def _get_tenant_credentials(self, tenant_id: int) -> dict[str, str]:
        """Resolves and decrypts third-party marketplace credentials for a given tenant."""
        creds: dict[str, str] = {}
        if self.user_store:
            try:
                enc = self.user_store.get_tenant_integrations(tenant_id)
                for provider, enc_val in enc.items():
                    try:
                        creds[provider] = decrypt_credential(enc_val)
                    except Exception as exc:
                        logger.warning(
                            "reconciliation.decrypt_credential_failed",
                            tenant_id=tenant_id,
                            provider=provider,
                            error=str(exc),
                        )
            except Exception as exc:
                logger.warning(
                    "reconciliation.fetch_tenant_integrations_failed",
                    tenant_id=tenant_id,
                    error=str(exc),
                )

        # Fallbacks to settings/env for single-tenant or default tenant 1
        if "biblio" not in creds and self.settings.biblio_api_key:
            creds["biblio"] = self.settings.biblio_api_key
        if "biblio_dealer_id" not in creds and self.settings.biblio_dealer_id:
            creds["biblio_dealer_id"] = self.settings.biblio_dealer_id
        if "abebooks" not in creds and self.settings.abebooks_api_key:
            creds["abebooks"] = self.settings.abebooks_api_key
        if "abebooks_client_id" not in creds and getattr(self.settings, "abebooks_client_id", ""):
            creds["abebooks_client_id"] = self.settings.abebooks_client_id

        return creds

    def run_reconciliation_cycle(
        self,
        demo_sample: bool = False,
        force_tenant_id: int | None = None,
    ) -> dict[str, Any]:
        """Runs a complete reconciliation pulse across all active tenants."""
        started_at = datetime.now(timezone.utc).isoformat()
        logger.info(
            "reconciliation.cycle.started",
            demo_sample=demo_sample,
            force_tenant_id=force_tenant_id,
        )

        tenants: list[int] = [1]
        if self.user_store:
            try:
                tenants = self.user_store.list_tenants() or [1]
            except Exception as exc:
                logger.warning("reconciliation.list_tenants_failed", error=str(exc))
                tenants = [1]

        if force_tenant_id is not None:
            tenants = [force_tenant_id]

        total_orders_polled = {"biblio": 0, "abebooks": 0}
        total_orders_processed = 0
        total_collisions_detected = 0
        tenants_checked = 0
        tenant_summaries: list[dict[str, Any]] = []
        corvus_summary = {"approved": 0, "ignored": 0, "failed": 0}

        for tenant_id in tenants:
            if self.user_store and force_tenant_id is None:
                try:
                    billing = self.user_store.get_tenant_billing_status(tenant_id)
                    # Allow tenant 1 (demo/flagship) even if status is default
                    if tenant_id != 1 and billing.get("subscription_status") != "active":
                        logger.debug("reconciliation.skip_inactive_tenant", tenant_id=tenant_id)
                        continue
                except Exception as exc:
                    logger.warning(
                        "reconciliation.check_billing_failed",
                        tenant_id=tenant_id,
                        error=str(exc),
                    )

            tenants_checked += 1
            creds = self._get_tenant_credentials(tenant_id)
            tenant_orders_processed = 0
            tenant_collisions = 0

            # 1. Biblio Polling & Ingestion
            biblio_key = creds.get("biblio", "")
            biblio_dealer = creds.get("biblio_dealer_id", "")
            biblio_orders = BiblioService.fetch_orders(
                api_key=biblio_key,
                dealer_id=biblio_dealer,
                dry_run=self.settings.dry_run,
                demo_sample=demo_sample,
            )
            total_orders_polled["biblio"] += len(biblio_orders)

            for order in biblio_orders:
                res = BiblioService.process_order(
                    order=order,
                    operator=self.operator,
                    tenant_id=tenant_id,
                    want_list_store=self.want_list_store,
                )
                total_orders_processed += 1
                tenant_orders_processed += 1
                if res.get("collisions"):
                    collision_count = len(res["collisions"])
                    total_collisions_detected += collision_count
                    tenant_collisions += collision_count
                if res.get("corvus_triage"):
                    for k in ("approved", "ignored", "failed"):
                        corvus_summary[k] += res["corvus_triage"].get(k, 0)

            # 2. AbeBooks Polling & Ingestion
            abe_key = creds.get("abebooks", "")
            abe_client = creds.get("abebooks_client_id", "ops_core_client")
            abe_orders = AbeBooksService.fetch_orders(
                api_key=abe_key,
                client_id=abe_client,
                dry_run=self.settings.dry_run,
            )
            total_orders_polled["abebooks"] += len(abe_orders)

            for order in abe_orders:
                res = AbeBooksService.process_order(
                    order=order,
                    operator=self.operator,
                    tenant_id=tenant_id,
                    want_list_store=self.want_list_store,
                )
                total_orders_processed += 1
                tenant_orders_processed += 1
                if res.get("collisions"):
                    collision_count = len(res["collisions"])
                    total_collisions_detected += collision_count
                    tenant_collisions += collision_count
                if res.get("corvus_triage"):
                    for k in ("approved", "ignored", "failed"):
                        corvus_summary[k] += res["corvus_triage"].get(k, 0)

            tenant_summaries.append({
                "tenant_id": tenant_id,
                "orders_processed": tenant_orders_processed,
                "collisions_detected": tenant_collisions,
            })

        # 3. Wake Corvus to triage any remaining pending reviews
        try:
            from app.agents.corvus import CorvusWarden
            warden = CorvusWarden(operator=self.operator, user_store=self.user_store)
            final_triage = warden.wake_and_triage()
            for k in ("approved", "ignored", "failed"):
                corvus_summary[k] += final_triage.get(k, 0)
            logger.info("reconciliation.corvus_triage.complete", **corvus_summary)
        except Exception as exc:
            logger.error("reconciliation.corvus_triage.failed", error=str(exc))

        result = {
            "status": "success",
            "started_at": started_at,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "tenants_checked": tenants_checked,
            "orders_polled": total_orders_polled,
            "orders_processed": total_orders_processed,
            "collisions_detected": total_collisions_detected,
            "corvus_actions": corvus_summary,
            "tenant_summaries": tenant_summaries,
        }
        logger.info("reconciliation.cycle.finished", **result)
        return result
