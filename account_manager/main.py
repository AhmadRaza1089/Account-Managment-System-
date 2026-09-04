import logging
from decimal import Decimal

from . import db, repository
from .config import DatabaseSettings
from .models import Account, Company, ExpenseManager, Role, User

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)


def run_demo() -> None:
    settings = DatabaseSettings.from_env()
    db.init_pool(settings)
    db.init_schema()

    company = Company(name="Company", owner_name="Ahmed")
    company_id = repository.insert_company(company)

    account = Account(company_id=company_id)
    repository.insert_account(account)

    admin = User(name="Raza", role=Role.ADMIN)
    regular_user = User(name="Raza", role=Role.REGULAR_USER)

    account.add_income(admin, Decimal("10000"))
    repository.save_account(account)

    status = account.add_expense(regular_user, Decimal("500"))
    logger.info("Regular user expense request: %s", status)
    repository.save_account(account)

    status = account.add_expense(admin, Decimal("500"))
    logger.info("Admin expense request: %s", status)
    repository.save_account(account)

    total_expenses, balance = ExpenseManager.calculate_budget(
        account.income, [account.expense]
    )
    print(
        f"The report of {company.name}: income={account.income} "
        f"expense={total_expenses} pending={account.pending_expense} balance={balance}"
    )


if __name__ == "__main__":
    run_demo()
