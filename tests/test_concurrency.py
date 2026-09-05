"""Concurrency tests.

These only mean something on a database with real row locking. SQLite
serialises writers anyway and treats SELECT ... FOR UPDATE as a no-op, so
they are skipped there — which is exactly why CI also runs the suite
against MySQL and PostgreSQL.

A race that depends on thread timing is not a test: at these speeds one
thread almost always finishes before the other starts, so the test passes
whether or not the locking works. Each test below therefore widens the
window deliberately — it slows the step between taking the lock and
committing, so both threads are guaranteed to be inside the critical
section at once. With the lock, the second thread waits and then sees the
committed result; without it, both proceed and the test fails. That
property is verified by removing the lock and watching these fail.
"""

import time
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier

import pytest

from account_manager import db, services
from account_manager.errors import AccountManagerError, InsufficientFunds
from account_manager.models import Actor, Role
from tests.conftest import requires_real_database

ADMIN = Actor("Ahmed", Role.ADMIN)
STAFF = Actor("Raza", Role.REGULAR_USER)

# Long enough that both threads reliably overlap, short enough to keep the
# suite fast.
OVERLAP = 0.4


@pytest.fixture
def slow_balance_check(monkeypatch):
    """Hold the critical section of submit_expense open."""
    original = services.get_balances

    def delayed(*args, **kwargs):
        result = original(*args, **kwargs)
        time.sleep(OVERLAP)
        return result

    monkeypatch.setattr(services, "get_balances", delayed)


@pytest.fixture
def slow_claim(monkeypatch):
    """Hold the critical section of approve/reject open."""
    original = services._claim_pending_expense

    def delayed(*args, **kwargs):
        result = original(*args, **kwargs)
        time.sleep(OVERLAP)
        return result

    monkeypatch.setattr(services, "_claim_pending_expense", delayed)


def _company_with(balance: str) -> int:
    with db.session_scope() as session:
        company_id = services.create_company(session, "Acme", "Ahmed").id
        services.add_income(session, company_id, ADMIN, balance)
    return company_id


def _run_together(work, count: int) -> list[str]:
    """Run `work` in `count` threads that all start at the same moment."""
    start = Barrier(count)

    def wrapped():
        start.wait(timeout=10)
        return work()

    with ThreadPoolExecutor(max_workers=count) as pool:
        futures = [pool.submit(wrapped) for _ in range(count)]
        return sorted(future.result() for future in futures)


@requires_real_database
def test_two_racing_expenses_cannot_take_the_same_money(database, slow_balance_check):
    """The company holds 1000 and two people each try to spend 800 at the
    same instant. Exactly one must succeed, and the balance must not go
    negative."""
    company_id = _company_with("1000")

    def spend():
        try:
            with db.session_scope() as session:
                services.submit_expense(session, company_id, ADMIN, "800")
            return "ok"
        except InsufficientFunds:
            return "refused"

    assert _run_together(spend, 2) == ["ok", "refused"]

    with db.session_scope() as session:
        balances = services.get_balances(session, company_id)
    assert balances.expense == Decimal("800.00")
    assert balances.balance == Decimal("200.00")


@requires_real_database
def test_an_earlier_read_does_not_freeze_the_balance(database, slow_balance_check):
    """Spending after an unrelated read in the same transaction.

    MySQL's default REPEATABLE READ fixes the transaction's view of the data
    at its first plain SELECT. If that happens before the balance check —
    here, an innocuous list_companies() first — the balance stays frozen at
    its pre-lock value and both spenders see the full 1000. This is why the
    engine pins MySQL to READ COMMITTED; without that, this test overspends.
    """
    company_id = _company_with("1000")

    def spend():
        try:
            with db.session_scope() as session:
                services.list_companies(session)  # establishes the snapshot
                services.submit_expense(session, company_id, ADMIN, "800")
            return "ok"
        except InsufficientFunds:
            return "refused"

    assert _run_together(spend, 2) == ["ok", "refused"]

    with db.session_scope() as session:
        assert services.get_balances(session, company_id).balance == Decimal("200.00")


@requires_real_database
def test_racing_approvals_cannot_apply_the_same_expense_twice(database, slow_claim):
    """Two admins approving the same request at once must not spend it twice."""
    company_id = _company_with("1000")
    with db.session_scope() as session:
        txn_id = services.submit_expense(session, company_id, STAFF, "500").id

    def approve():
        try:
            with db.session_scope() as session:
                services.approve_expense(session, txn_id, ADMIN)
            return "ok"
        except AccountManagerError:
            return "refused"

    assert _run_together(approve, 2) == ["ok", "refused"]

    with db.session_scope() as session:
        assert services.get_balances(session, company_id).expense == Decimal("500.00")


@requires_real_database
def test_approve_and_reject_racing_cannot_both_apply(database, slow_claim):
    """One admin approves while another rejects. Whichever wins, the
    expense must end up in exactly one state."""
    company_id = _company_with("1000")
    with db.session_scope() as session:
        txn_id = services.submit_expense(session, company_id, STAFF, "500").id

    def decide(decision):
        def run():
            try:
                with db.session_scope() as session:
                    decision(session, txn_id, ADMIN)
                return "ok"
            except AccountManagerError:
                return "refused"

        return run

    start = Barrier(2)

    def guarded(fn):
        def run():
            start.wait(timeout=10)
            return fn()

        return run

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(guarded(decide(services.approve_expense))),
            pool.submit(guarded(decide(services.reject_expense))),
        ]
        results = sorted(future.result() for future in futures)

    assert results == ["ok", "refused"]

    with db.session_scope() as session:
        balances = services.get_balances(session, company_id)
    # Approved (expense 500) or rejected (expense 0) — never both, and
    # never left holding the funds as pending.
    assert balances.expense in (Decimal("0"), Decimal("500.00"))
    assert balances.pending_expense == Decimal("0")


@requires_real_database
def test_concurrent_income_is_never_lost(database):
    """Ten simultaneous deposits must all land.

    This one tests the ledger design rather than the locking: because every
    write is an insert, there is no total for one writer to overwrite. Under
    the old stored-total design most of these would have been lost.
    """
    company_id = _company_with("0.01")

    def deposit():
        with db.session_scope() as session:
            services.add_income(session, company_id, ADMIN, "100")
        return "ok"

    assert _run_together(deposit, 10) == ["ok"] * 10

    with db.session_scope() as session:
        assert services.get_balances(session, company_id).income == Decimal("1000.01")
