"""Plain-English summaries of a company's finances."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from sqlalchemy.orm import Session

from ..models import TransactionStatus, TransactionType
from ..services import get_balances, get_company, list_transactions
from .anomalies import detect_anomalies
from .base import AIProvider

SYSTEM = (
    "You are a careful bookkeeper writing a short summary for a small "
    "business owner. Use only the figures given to you — never invent or "
    "estimate numbers. Be direct and concrete: what came in, what went out, "
    "what stands out, and what needs their attention. Three short "
    "paragraphs at most, no headings, no bullet points."
)


def build_context(session: Session, company_id: int, *, limit: int = 200) -> str:
    """Assemble the facts the model is allowed to talk about."""
    company = get_company(session, company_id)
    balances = get_balances(session, company_id)
    transactions = list_transactions(session, company_id, limit=limit)

    # Approved only. Counting pending, rejected or reversed requests here
    # would tell the model a company spent money it did not spend, and the
    # summary would state that as fact.
    by_category: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for txn in transactions:
        if (
            txn.type is TransactionType.EXPENSE
            and txn.status is TransactionStatus.APPROVED
        ):
            by_category[txn.category or "uncategorised"] += txn.amount

    lines = [
        f"Company: {company.name} (owner {company.owner_name})",
        f"Income: {balances.income}",
        f"Approved expenses: {balances.expense}",
        f"Awaiting approval: {balances.pending_expense}",
        f"Balance: {balances.balance}",
        f"Available to spend: {balances.available}",
        "",
        "Spend by category (approved only):",
    ]
    if by_category:
        for category, total in sorted(
            by_category.items(), key=lambda item: item[1], reverse=True
        ):
            lines.append(f"  {category}: {total}")
    else:
        lines.append("  (none recorded)")

    findings = detect_anomalies(session, company_id)
    if findings:
        lines.append("")
        lines.append("Automated checks flagged:")
        lines.extend(f"  [{f.severity}] {f.message}" for f in findings)

    lines.append("")
    lines.append(f"Recent transactions (newest first, up to {limit}):")
    for txn in transactions[:50]:
        lines.append(
            f"  #{txn.id} {txn.created_at:%Y-%m-%d} {txn.type.value} {txn.amount} "
            f"{txn.status.value} {txn.category or '-'} "
            f"\"{txn.description or ''}\" by {txn.created_by}"
        )
    return "\n".join(lines)


def summarise(session: Session, company_id: int, provider: AIProvider) -> str:
    """Ask the configured model to describe how the company is doing."""
    context = build_context(session, company_id)
    return provider.complete_text(
        system=SYSTEM,
        prompt=f"Summarise this company's finances:\n\n{context}",
        max_tokens=800,
    )
