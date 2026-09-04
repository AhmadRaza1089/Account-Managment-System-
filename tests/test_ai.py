"""Tests for the optional AI layer.

The anomaly detector is tested properly because it runs for everyone with
no configuration. The provider-backed features are tested through a fake
provider, so the suite never needs an API key or a network connection.
"""

from datetime import timedelta
from decimal import Decimal

import pytest

from account_manager import services
from account_manager.ai.anomalies import (
    detect_anomalies,
    find_duplicates,
    find_outliers,
    find_stale_approvals,
)
from account_manager.ai.base import AIError, AIUnavailable
from account_manager.ai.extract import parse_transaction_text
from account_manager.ai.providers import get_provider
from account_manager.config import Settings
from account_manager.models import Actor, Role, Transaction, TransactionType, utcnow


class FakeProvider:
    """Stands in for a real model."""

    name = "fake"
    model = "fake-1"

    def __init__(self, json_response=None, text_response=""):
        self.json_response = json_response or {}
        self.text_response = text_response
        self.calls: list[dict] = []

    def complete_json(self, *, system, prompt, schema):
        self.calls.append({"system": system, "prompt": prompt, "schema": schema})
        return self.json_response

    def complete_text(self, *, system, prompt, max_tokens=1024):
        self.calls.append({"system": system, "prompt": prompt})
        return self.text_response


def expense(amount, *, id=1, category="meals", description="Lunch", by="Raza", age_days=0):
    return Transaction(
        id=id,
        company_id=1,
        type=TransactionType.EXPENSE,
        status=services.TransactionStatus.APPROVED,
        amount=Decimal(amount),
        category=category,
        description=description,
        created_by=by,
        created_at=utcnow() - timedelta(days=age_days),
    )


# ------------------------------------------------------------- duplicates


def test_identical_charges_close_together_are_flagged():
    findings = find_duplicates(
        [expense("50", id=1, age_days=1), expense("50", id=2, age_days=0)]
    )
    assert len(findings) == 1
    assert findings[0].kind == "possible_duplicate"
    assert findings[0].transaction_ids == [1, 2]


def test_identical_charges_far_apart_are_not_flagged():
    findings = find_duplicates(
        [expense("50", id=1, age_days=90), expense("50", id=2, age_days=0)]
    )
    assert findings == []


def test_different_people_are_not_treated_as_duplicates():
    findings = find_duplicates(
        [expense("50", id=1, by="Raza"), expense("50", id=2, by="Sara")]
    )
    assert findings == []


def test_different_descriptions_are_not_duplicates():
    findings = find_duplicates(
        [expense("50", id=1, description="Lunch"), expense("50", id=2, description="Taxi")]
    )
    assert findings == []


def test_a_long_run_of_repeats_is_one_quiet_finding_not_many_loud_ones():
    """A daily coffee shouldn't produce a wall of duplicate alerts."""
    repeats = [expense("12", id=i, age_days=6 - i) for i in range(1, 7)]
    findings = find_duplicates(repeats)

    assert len(findings) == 1
    assert findings[0].kind == "repeated_charge"
    assert findings[0].severity == "low"
    assert len(findings[0].transaction_ids) == 6


def test_repeats_split_by_a_long_gap_are_reported_separately():
    findings = find_duplicates(
        [
            expense("50", id=1, age_days=100),
            expense("50", id=2, age_days=99),
            expense("50", id=3, age_days=1),
            expense("50", id=4, age_days=0),
        ]
    )
    assert len(findings) == 2
    assert [f.transaction_ids for f in findings] == [[1, 2], [3, 4]]


# --------------------------------------------------------------- outliers


def test_a_large_outlier_is_flagged():
    normal = [expense("10", id=i, category="meals") for i in range(1, 7)]
    huge = expense("5000", id=99, category="meals")
    findings = find_outliers([*normal, huge])
    assert [f.transaction_ids for f in findings] == [[99]]


def test_no_outliers_without_enough_history():
    """Two expenses aren't enough to call anything 'unusual'."""
    findings = find_outliers([expense("10", id=1), expense("5000", id=2)])
    assert findings == []


def test_similar_amounts_produce_no_outliers():
    findings = find_outliers([expense("100", id=i) for i in range(1, 8)])
    assert findings == []


def test_categories_are_compared_separately():
    meals = [expense("10", id=i, category="meals") for i in range(1, 7)]
    payroll = [expense("5000", id=100 + i, category="payroll") for i in range(6)]
    # A big payroll run is normal for payroll, so nothing should be flagged.
    assert find_outliers([*meals, *payroll]) == []


# -------------------------------------------------------- stale approvals


def test_long_waiting_requests_are_flagged(session):
    company = services.create_company(session, "Acme", "Ahmed")
    admin = Actor("Ahmed", Role.ADMIN)
    services.add_income(session, company.id, admin, "1000")
    txn = services.submit_expense(
        session, company.id, Actor("Raza", Role.REGULAR_USER), "100"
    )
    txn.created_at = utcnow() - timedelta(days=40)
    session.flush()

    findings = find_stale_approvals(services.list_transactions(session, company.id))
    assert len(findings) == 1
    assert findings[0].kind == "stale_approval"


def test_no_findings_for_an_empty_ledger():
    assert find_stale_approvals([]) == []


def test_detect_anomalies_orders_by_severity(session):
    company = services.create_company(session, "Acme", "Ahmed")
    admin = Actor("Ahmed", Role.ADMIN)
    services.add_income(session, company.id, admin, "100000")
    for _ in range(6):
        services.submit_expense(
            session, company.id, admin, "10", description="Coffee", category="meals"
        )
    services.submit_expense(
        session, company.id, admin, "9000", description="Coffee", category="meals"
    )

    findings = detect_anomalies(session, company.id)
    severities = [f.severity for f in findings]
    assert severities == sorted(severities, key=lambda s: {"high": 0, "medium": 1, "low": 2}[s])
    # Six identical charges read as a recurring charge, not six mistakes.
    assert any(f.kind == "repeated_charge" for f in findings)
    assert any(f.kind == "unusual_amount" for f in findings)


# -------------------------------------------------------------- extraction


def test_parses_a_sentence_into_a_transaction():
    provider = FakeProvider(
        {
            "type": "expense",
            "amount": "45.50",
            "category": "travel",
            "description": "Uber to the airport",
        }
    )
    parsed = parse_transaction_text("paid 45.50 for an uber to the airport", provider)

    assert parsed.type == "expense"
    assert parsed.amount == Decimal("45.50")
    assert parsed.category == "travel"
    assert parsed.description == "Uber to the airport"


def test_missing_amount_is_reported_clearly():
    provider = FakeProvider(
        {"type": "expense", "amount": None, "category": "meals", "description": "Lunch"}
    )
    with pytest.raises(AIError, match="Could not find an amount"):
        parse_transaction_text("had lunch with a client", provider)


def test_a_hallucinated_amount_is_still_validated():
    """The model's output goes through the same validation as typed input."""
    provider = FakeProvider(
        {"type": "expense", "amount": "-99", "category": "meals", "description": "x"}
    )
    with pytest.raises(AIError, match="not usable"):
        parse_transaction_text("refund of 99", provider)


def test_an_unknown_category_falls_back_to_other():
    provider = FakeProvider(
        {
            "type": "expense",
            "amount": "10",
            "category": "interdimensional_travel",
            "description": "x",
        }
    )
    assert parse_transaction_text("spent 10", provider).category == "other"


def test_empty_text_is_refused():
    with pytest.raises(AIError):
        parse_transaction_text("   ", FakeProvider())


# --------------------------------------------------------------- providers


def test_no_provider_configured_gives_a_helpful_error():
    with pytest.raises(AIUnavailable, match="No AI provider configured"):
        get_provider(Settings(database_url="sqlite://", ai_provider=""))


def test_unknown_provider_lists_the_valid_ones():
    with pytest.raises(AIUnavailable, match="claude, openai, ollama"):
        get_provider(Settings(database_url="sqlite://", ai_provider="hal9000"))


def test_ollama_needs_no_package():
    provider = get_provider(
        Settings(database_url="sqlite://", ai_provider="ollama", ai_model="llama3.1")
    )
    assert provider.name == "ollama"
    assert provider.model == "llama3.1"
