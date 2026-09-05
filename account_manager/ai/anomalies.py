"""Spot suspicious expenses.

Deliberately statistical rather than model-driven: it needs no API key, no
network, and no configuration, so every user gets it. It is also
repeatable — the same ledger always produces the same findings, which
matters when the output is an accusation about someone's expense claim.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from ..models import Transaction, TransactionStatus, TransactionType, utcnow
from ..services import list_transactions

# How far apart two identical charges can be and still look like a double entry.
DUPLICATE_WINDOW = timedelta(days=7)
# How many times the typical spend an expense must be to count as an outlier.
OUTLIER_MULTIPLIER = Decimal("4")
# Below this many comparable expenses, "typical" isn't meaningful yet.
MIN_SAMPLE = 5


@dataclass(frozen=True)
class Finding:
    kind: str
    severity: str  # "high" | "medium" | "low"
    message: str
    transaction_ids: list[int]

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "severity": self.severity,
            "message": self.message,
            "transaction_ids": self.transaction_ids,
        }


def _normalise(text: str | None) -> str:
    return " ".join((text or "").lower().split())


def find_duplicates(transactions: Sequence[Transaction]) -> list[Finding]:
    """Identical charges logged repeatedly in a short window.

    Repeats are reported as one finding per run rather than one per pair,
    so a daily coffee doesn't produce a wall of near-identical alerts. An
    exact pair is the suspicious case; a longer run usually means a
    recurring charge, so it is reported more quietly.
    """
    findings: list[Finding] = []
    by_key: dict[tuple, list[Transaction]] = defaultdict(list)

    for txn in transactions:
        if txn.type is not TransactionType.EXPENSE:
            continue
        by_key[(txn.amount, _normalise(txn.description), txn.created_by)].append(txn)

    for (amount, description, created_by), group in by_key.items():
        if len(group) < 2:
            continue
        ordered = sorted(group, key=lambda t: t.created_at)

        # Walk the run, splitting whenever the gap exceeds the window.
        cluster = [ordered[0]]
        for earlier, later in zip(ordered, ordered[1:], strict=False):
            if later.created_at - earlier.created_at <= DUPLICATE_WINDOW:
                cluster.append(later)
            else:
                findings.extend(_duplicate_finding(cluster, amount, description, created_by))
                cluster = [later]
        findings.extend(_duplicate_finding(cluster, amount, description, created_by))

    return findings


def _duplicate_finding(
    cluster: list[Transaction], amount: Decimal, description: str, created_by: str
) -> list[Finding]:
    if len(cluster) < 2:
        return []

    ids = [t.id for t in cluster]
    label = f" for '{description}'" if description else ""
    if len(cluster) == 2:
        return [
            Finding(
                kind="possible_duplicate",
                severity="high",
                message=(
                    f"{created_by} logged {amount}{label} twice within "
                    f"{DUPLICATE_WINDOW.days} days (#{ids[0]} and #{ids[1]})."
                ),
                transaction_ids=ids,
            )
        ]
    return [
        Finding(
            kind="repeated_charge",
            severity="low",
            message=(
                f"{created_by} logged {amount}{label} {len(cluster)} times within "
                f"{DUPLICATE_WINDOW.days} days — check whether this is a recurring "
                f"charge or a mistake."
            ),
            transaction_ids=ids,
        )
    ]


def find_outliers(transactions: Sequence[Transaction]) -> list[Finding]:
    """Expenses far above what this company normally spends in that category."""
    findings: list[Finding] = []
    by_category: dict[str, list[Transaction]] = defaultdict(list)

    for txn in transactions:
        if txn.type is TransactionType.EXPENSE:
            by_category[txn.category or "uncategorised"].append(txn)

    for category, group in by_category.items():
        if len(group) < MIN_SAMPLE:
            continue
        typical = Decimal(
            str(statistics.median(float(t.amount) for t in group))
        ).quantize(Decimal("0.01"))
        if typical <= 0:
            continue
        limit = typical * OUTLIER_MULTIPLIER
        for txn in group:
            if txn.amount > limit:
                findings.append(
                    Finding(
                        kind="unusual_amount",
                        severity="medium",
                        message=(
                            f"#{txn.id}: {txn.amount} for '{category}' is well above "
                            f"the usual {typical} for that category."
                        ),
                        transaction_ids=[txn.id],
                    )
                )
    return findings


def find_stale_approvals(transactions: Sequence[Transaction], *, days: int = 14) -> list[Finding]:
    """Requests nobody has decided on.

    Measured against the clock, not against the newest ledger entry: a
    company whose most recent activity *is* the forgotten request would
    otherwise never see it flagged, which is exactly the case that matters.
    """
    if not transactions:
        return []
    cutoff = utcnow() - timedelta(days=days)

    stale = [
        t
        for t in transactions
        if t.status is TransactionStatus.PENDING and t.created_at < cutoff
    ]
    if not stale:
        return []
    return [
        Finding(
            kind="stale_approval",
            severity="low",
            message=(
                f"{len(stale)} expense request(s) have been awaiting approval for "
                f"more than {days} days, holding {sum(t.amount for t in stale)}."
            ),
            transaction_ids=[t.id for t in stale],
        )
    ]


def detect_anomalies(session: Session, company_id: int, *, limit: int = 1000) -> list[Finding]:
    """Run every check over a company's ledger, worst first."""
    transactions = list_transactions(session, company_id, limit=limit)
    findings = [
        *find_duplicates(transactions),
        *find_outliers(transactions),
        *find_stale_approvals(transactions),
    ]
    order = {"high": 0, "medium": 1, "low": 2}
    return sorted(findings, key=lambda f: order.get(f.severity, 3))
