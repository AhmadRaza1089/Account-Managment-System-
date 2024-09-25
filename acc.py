import mysql.connector
db = mysql.connector.connect(
    host="localhost",
    user="root",
    password="0300Ahmed$",
    database="account"
)
# to execute query 
my_cursor = db.cursor()

def create_table():
    my_cursor.execute("""
    CREATE TABLE IF NOT EXISTS Company(
        id INT AUTO_INCREMENT PRIMARY KEY,
        name VARCHAR(50),
        owner_name VARCHAR(50)
    )
    """)
    my_cursor.execute("""
    CREATE TABLE IF NOT EXISTS Account(
        id INT AUTO_INCREMENT PRIMARY KEY,
        company_id INT,
        income DECIMAL(10, 2),
        expense DECIMAL(10, 2),
        pending_expense DECIMAL(10, 2),
        FOREIGN KEY (company_id) REFERENCES Company(id)
    )
    """)
    db.commit()

class Admin:
    def __init__(self, adminname, role, approved=True):
        self.adminname = adminname
        self.role = role
        self.approved = approved

class Company:
    def __init__(self, name, owner_name):
        self.name = name
        self.owner_name = owner_name
        self.accounts = []

    def add_account(self, account):
        self.accounts.append(account)
        my_cursor.execute("INSERT INTO Company (name, owner_name) VALUES (%s, %s)", (self.name, self.owner_name))
        db.commit()  # Corrected indentation

    def view_details(self, checker_name):
        if checker_name == self.owner_name:  # Corrected attribute name
            return True
        else:
            return False
class Account:
    def __init__(self, company_id):
        self.company_id = company_id
        self.income = 0
        self.expense = 0
        self.pending_expense = 0

    def add_income(self, user, amount):
        if user.role == "admin":
            self.income += amount
            print(f"The amount: {amount} successfully added to the income account: {self.income}")
            my_cursor.execute("UPDATE Account SET income = %s WHERE company_id = %s", (self.income, self.company_id))  # Ensure company.id is correct
            db.commit()
        else:
            print("Invalid try again")

    def add_expense(self, user, amount):
        if user.role == "regular_user":
            if amount > self.income:
                print("You exceed the limits")
            else:
                print("Your request is sent for approval")
        elif user.role == "admin":
            if user.approved:
                self.expense += amount
                print(f"The expense successfully approved: {self.expense}")
                my_cursor.execute("UPDATE Account SET expense = %s WHERE company_id = %s", (self.expense, self.company_id))  
                db.commit()
            else:
                self.pending_expense += amount
                print("Pending")
        else:
            print("Invalid try again")

class ExpenseManager:
    @staticmethod
    def calculate_budget(user, income, expenses, pending_expense):
        total_expenses = sum(expenses)
        balance = income
        if user.role == "admin" or user.role == "owner":
            balance = income - total_expenses
        return total_expenses, balance, pending_expense

create_table()
company = Company("Company", owner_name="Ahmed")
admin = Admin("Raza", "admin")
regular_user = Admin("Raza", "regular_user")
company.add_account(None)
company_id = my_cursor.lastrowid
account = Account(company_id)
company_id = my_cursor.lastrowid
account.add_income(admin, 10000)
account.add_expense(regular_user, 500)
account.add_expense(admin, 500)
total_expense, balance, pending_expense = ExpenseManager.calculate_budget(admin, account.income, [account.expense], account.pending_expense)
print(f"The report of the {company.name}: income: {account.income} expense: {total_expense} pending: {account.pending_expense}")
my_cursor.close()
db.close()
