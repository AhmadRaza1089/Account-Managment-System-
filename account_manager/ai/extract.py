"""Turn a plain sentence into a structured transaction.

    "paid 45.50 for an uber to the airport"
        -> expense, 45.50, travel, "Uber to the airport"

Amounts are re-validated by the service layer afterwards, so a model that
hallucinates a number still can't put an invalid amount in the ledger.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ..errors import InvalidAmount
from ..services import parse_amount
from .base import AIError, AIProvider

CATEGORIES = (
    "travel",
    "meals",
    "office",
    "software",
    "equipment",
    "marketing",
    "payroll",
    "utilities",
    "professional_services",
    "other",
)

SYSTEM = (
    "You extract structured accounting data from short notes written by "
    "small business staff. Return only what the note actually says. If the "
    "note does not state an amount, set amount to null rather than guessing. "
    "Write the description as a short, clean noun phrase."
)

SCHEMA = {
    "type": "object",
    "properties": {
        "type": {"type": "string", "enum": ["income", "expense"]},
        "amount": {"type": ["string", "null"]},
        "category": {"type": "string", "enum": list(CATEGORIES)},
        "description": {"type": "string"},
    },
    "required": ["type", "amount", "category", "description"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class ParsedTransaction:
    type: str
    amount: Decimal
    category: str
    description: str


def parse_transaction_text(text: str, provider: AIProvider) -> ParsedTransaction:
    """Extract a transaction from free text.

    Raises AIError if the model can't find an amount, and InvalidAmount if
    the amount it found isn't usable.
    """
    text = (text or "").strip()
    if not text:
        raise AIError("Nothing to parse — the text is empty.")

    result = provider.complete_json(
        system=SYSTEM,
        prompt=f"Extract the transaction from this note:\n\n{text}",
        schema=SCHEMA,
    )

    amount = result.get("amount")
    if amount in (None, "", "null"):
        raise AIError(
            f"Could not find an amount in {text!r}. "
            "Include one, for example: 'lunch with client 45.00'."
        )

    try:
        value = parse_amount(str(amount))
    except InvalidAmount as exc:
        raise AIError(f"The extracted amount was not usable: {exc}") from exc

    category = result.get("category") or "other"
    if category not in CATEGORIES:
        category = "other"

    txn_type = result.get("type")
    if txn_type not in ("income", "expense"):
        txn_type = "expense"

    return ParsedTransaction(
        type=txn_type,
        amount=value,
        category=category,
        description=(result.get("description") or text).strip()[:500],
    )
