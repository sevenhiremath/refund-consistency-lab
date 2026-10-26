from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from app.domain.model import Delivery, Observation, Payment, RefundIntent
from app.store import AdmissionResult, IngestResult, Store


# One payment, a handful of refunds. Bounded so a workspace cannot grow without limit.
MAX_REFUNDS_PER_WORKSPACE = 50


class IntegrationError(Exception):
    """A request the integration service cannot serve.

    Carries a status so a future HTTP layer can map it without inventing rules here.
    """

    def __init__(
        self, status: int, message: str, headers: dict[str, str] | None = None
    ) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.headers = headers or {}


@dataclass(frozen=True)
class Workspace:
    workspace_id: str
    provider: str
    payment_id: str
    captured_paise: int
    currency: str


@dataclass(frozen=True)
class DeliveryOutcome:
    outcome: str
    recorded: bool
    refund: dict[str, Any] | None


@dataclass(frozen=True)
class WorkspaceStatus:
    workspace_id: str
    provider: str
    payment_id: str
    captured_paise: int
    admitted_paise: int
    currency: str
    confirmed_paise: int
    review_paise: int
    refunds: tuple[dict[str, Any], ...]
    findings: tuple[dict[str, Any], ...]
    unmatched: tuple[dict[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "workspace_id": self.workspace_id,
            "provider": self.provider,
            "payment": {
                "payment_id": self.payment_id,
                "captured_paise": self.captured_paise,
                "admitted_paise": self.admitted_paise,
                "currency": self.currency,
            },
            "totals": {
                "confirmed_paise": self.confirmed_paise,
                "review_paise": self.review_paise,
            },
            "refunds": list(self.refunds),
            "findings": list(self.findings),
            "unmatched": list(self.unmatched),
        }


class IntegrationService:
    """Fixture free processing for one payment per workspace.

    Uses the same admission and ingest path as the demo. The only differences are
    that a workspace always exists with its payment, and the status view is the
    protected result only.
    """

    def __init__(self, store: Store) -> None:
        self.store = store

    # -- setup -----------------------------------------------------------

    def create_workspace(
        self, provider: str, payment_id: str, captured_paise: int
    ) -> Workspace:
        if not provider or not provider.strip():
            raise IntegrationError(422, "provider is required")

        # Payment and RefundIntent validate positive paise and INR.
        payment = Payment(payment_id, captured_paise)
        workspace_id = "ws_" + uuid.uuid4().hex[:12]
        self.store.create_workspace(workspace_id, provider, payment)
        return Workspace(
            workspace_id=workspace_id,
            provider=provider,
            payment_id=payment.payment_id,
            captured_paise=payment.captured_paise,
            currency=payment.currency,
        )

    def workspace(self, workspace_id: str) -> Workspace:
        return self._workspace(workspace_id)

    def register_refund(
        self, workspace_id: str, refund_id: str, amount_paise: int
    ) -> AdmissionResult:
        workspace = self._workspace(workspace_id)
        already = self.store.refund_exists(workspace_id, refund_id)
        if not already and self.store.refund_count(workspace_id) >= MAX_REFUNDS_PER_WORKSPACE:
            raise IntegrationError(
                409,
                "workspace refund limit reached (%d)" % MAX_REFUNDS_PER_WORKSPACE,
            )
        intent = RefundIntent(refund_id, workspace.payment_id, amount_paise)
        return self.store.admit(workspace_id, intent)

    # -- processing ------------------------------------------------------

    def submit_delivery(
        self,
        workspace_id: str,
        attempt_id: str,
        event_id: str,
        refund_id: str,
        status: str,
        amount_paise: int,
    ) -> DeliveryOutcome:
        """Record one normalized webhook delivery.

        This never registers a refund intent. A delivery for an unknown refund is
        stored as evidence and reported as unmatched, exactly like the demo.
        """
        workspace = self._workspace(workspace_id)
        delivery = Delivery(
            attempt_id,
            Observation(event_id, refund_id, status, amount_paise),
        )
        result: IngestResult = self.store.ingest(
            workspace_id, workspace.provider, delivery
        )
        report = self.store.report(workspace_id, workspace.payment_id)
        return DeliveryOutcome(
            outcome=result.outcome,
            recorded=result.recorded,
            refund=self._refund_view(report, refund_id),
        )

    # -- reads -----------------------------------------------------------

    def status(self, workspace_id: str) -> WorkspaceStatus:
        workspace = self._workspace(workspace_id)
        report = self.store.report(workspace_id, workspace.payment_id)

        refunds = [self._row_view(row) for row in report.rows]

        findings = tuple(
            {
                "key": row["finding_key"],
                "kind": row["kind"],
                "refund_id": row["refund_id"],
                "event_id": row["event_id"],
                "detail": row["detail"],
            }
            for row in self.store.findings(workspace_id)
        )
        unmatched = tuple(
            {
                "event_id": item.event_id,
                "refund_id": item.refund_id,
                "status": item.status,
                "amount_paise": item.amount_paise,
            }
            for item in report.unmatched
        )

        return WorkspaceStatus(
            workspace_id=workspace_id,
            provider=workspace.provider,
            payment_id=workspace.payment_id,
            captured_paise=report.captured_paise,
            admitted_paise=report.admitted_paise,
            currency=workspace.currency,
            confirmed_paise=report.protected_confirmed_paise,
            review_paise=report.protected_review_paise,
            refunds=tuple(refunds),
            findings=findings,
            unmatched=unmatched,
        )

    # -- internals -------------------------------------------------------

    def _workspace(self, workspace_id: str) -> Workspace:
        try:
            info = self.store.run_info(workspace_id)
        except ValueError as exc:
            raise IntegrationError(404, "unknown workspace") from exc
        if info["kind"] != "integration":
            raise IntegrationError(404, "unknown workspace")

        payments = self.store.run_payments(workspace_id)
        if len(payments) != 1:
            raise IntegrationError(
                409, "workspace does not have exactly one payment"
            )
        payment = payments[0]
        return Workspace(
            workspace_id=workspace_id,
            provider=info["provider"],
            payment_id=payment.payment_id,
            captured_paise=payment.captured_paise,
            currency=payment.currency,
        )

    def _row_view(self, row: Any) -> dict[str, Any]:
        """Protected fields only. Naive results stay out of the integration view."""
        return {
            "refund_id": row.refund_id,
            "amount_paise": row.amount_paise,
            "status": row.protected_status,
            "confirmed_paise": row.protected_confirmed_paise,
            "review_paise": row.protected_review_paise,
            "reasons": list(row.reasons),
        }

    def _refund_view(self, report: Any, refund_id: str) -> dict[str, Any] | None:
        for row in report.rows:
            if row.refund_id == refund_id:
                return self._row_view(row)
        return None