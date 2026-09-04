"""Domain objects. No database access here — keeps business rules unit-testable
without a live MySQL instance. Persistence lives in repository.py.
"""

from dataclasses import dataclass, field
from decimal import Decimal


class Role:
    ADMIN = "admin"
    REGULAR_USER = "regular_user"
    OWNER = "owner"


@dataclass
class User:
    name: str
    role: str
    approved: bool = True


@dataclass
class Company:
    name: str
    owner_name: str
    id: int | None = None

    def is_owned_by(self, checker_name: str) -> bool:
        return checker_name == self.owner_name


@dataclass
class Account:
    company_id: int
    id: int | None = None
    income: Decimal = field(default_factory=lambda: Decimal("0"))
    expense: Decimal = field(default_factory=lambda: Decimal("0"))
    pending_expense: Decimal = field(default_factory=lambda: Decimal("0"))

    def add_income(self, user: User, amount: Decimal) -> None:
        if user.role != Role.ADMIN:
            raise PermissionError(f"{user.name} is not authorized to add income.")
        if amount <= 0:
            raise ValueError("Income amount must be positive.")
        self.income += amount

    def add_expense(self, user: User, amount: Decimal) -> str:
        """Returns one of: 'approved', 'pending_approval', 'rejected: <reason>'."""
        if amount <= 0:
            raise ValueError("Expense amount must be positive.")

        if user.role == Role.REGULAR_USER:
            if amount > self.income:
                return "rejected: exceeds available income"
            self.pending_expense += amount
            return "pending_approval"

        if user.role == Role.ADMIN:
            if user.approved:
                self.expense += amount
                return "approved"
            self.pending_expense += amount
            return "pending_approval"

        raise PermissionError(f"Unknown role '{user.role}'.")


class ExpenseManager:
    @staticmethod
    def calculate_budget(
        income: Decimal, expenses: list[Decimal]
    ) -> tuple[Decimal, Decimal]:
        total_expenses = sum(expenses, Decimal("0"))
        balance = income - total_expenses
        return total_expenses, balance
