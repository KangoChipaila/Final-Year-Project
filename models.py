from datetime import datetime, timezone
from sqlalchemy import func
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy import Column, Integer, String, DateTime, Float, ForeignKey, JSON, Index

db = SQLAlchemy()


# --------------------
# Customers / Sales
# --------------------
class Customer(db.Model):
    __tablename__ = "customers"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=True)
    contact_person = db.Column(db.String(255), nullable=True)
    email = db.Column(db.String(255), nullable=True)
    phone = db.Column(db.String(50), nullable=True)
    status = db.Column(db.String(50), nullable=True)  # Active / Inactive
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now())

    def to_dict(self):
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}


class SalesOrder(db.Model):
    __tablename__ = "sales_orders"
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.String(100), unique=True, nullable=False)
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.id"), nullable=True)
    date = db.Column(db.Date, nullable=True)
    amount = db.Column(db.Numeric(12, 2), nullable=True)
    status = db.Column(db.String(50), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now())
    quantity = db.Column(db.Integer, nullable=True, default=0)
    inventory_id = db.Column(db.Integer, db.ForeignKey("inventory_items.id"), nullable=True)


    customer = db.relationship("Customer", backref="sales_orders", lazy=True)

    def to_dict(self):
        d = {c.name: getattr(self, c.name) for c in self.__table__.columns}
        d["customer"] = self.customer.to_dict() if self.customer else None
        return d


class SalesForecast(db.Model):
    __tablename__ = "sales_forecasts"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=True)
    payload = db.Column(db.JSON, nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now())

    def to_dict(self):
        return {"id": self.id, "name": self.name, "payload": self.payload, "created_at": self.created_at.isoformat()}


# --------------------
# Distribution / Inventory
# --------------------
class InventoryItem(db.Model):
    __tablename__ = "inventory_items"
    id = db.Column(db.Integer, primary_key=True)
    product = db.Column(db.String(255), nullable=False)
    sku = db.Column(db.String(100), nullable=True)
    warehouse = db.Column(db.String(255), nullable=True)
    quantity = db.Column(db.Integer, default=0)
    reorder_level = db.Column(db.Integer, default=0)
    status = db.Column(db.String(50), nullable=True)
    updated_at = db.Column(db.DateTime(timezone=True), server_default=func.now())
    unit_cost = db.Column(db.Float, nullable=True, default=0.0)
    
    def to_dict(self):
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}


class Shipment(db.Model):
    __tablename__ = "shipments"
    id = db.Column(db.Integer, primary_key=True)
    shipment_id = db.Column(db.String(100), unique=True, nullable=False)
    date = db.Column(db.Date, nullable=True)
    carrier = db.Column(db.String(255), nullable=True)
    destination = db.Column(db.String(255), nullable=True)
    status = db.Column(db.String(50), nullable=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.id"), nullable=True)
    customer = db.relationship("Customer", lazy=True)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now())

    def to_dict(self):
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}


# --------------------
# Finance
# --------------------
class Payment(db.Model):
    __tablename__ = "payments"
    id = db.Column(db.Integer, primary_key=True)
    payment_id = db.Column(db.String(100), unique=True, nullable=False)
    party = db.Column(db.String(255), nullable=True)
    due_date = db.Column(db.Date, nullable=True)
    amount = db.Column(db.Numeric(12, 2), nullable=True)
    status = db.Column(db.String(50), nullable=True)  # Paid / Pending
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now())

    def to_dict(self):
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}


class CashFlowRecord(db.Model):
    __tablename__ = "cashflow_records"
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=True)
    description = db.Column(db.String(512), nullable=True)
    inflow = db.Column(db.Numeric(12, 2), nullable=True)
    outflow = db.Column(db.Numeric(12, 2), nullable=True)
    balance = db.Column(db.Numeric(12, 2), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now())

    def to_dict(self):
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}


# --------------------
# HR / Employees
# --------------------
class Employee(db.Model):
    __tablename__ = "employees"
    id = db.Column(db.Integer, primary_key=True)
    external_id = db.Column(db.String(100), nullable=True)
    name = db.Column(db.String(255), nullable=True)
    department = db.Column(db.String(255), nullable=True)
    role = db.Column(db.String(255), nullable=True)
    email = db.Column(db.String(255), nullable=True)
    phone = db.Column(db.String(50), nullable=True)
    status = db.Column(db.String(50), nullable=True)  # Active / On Leave / Inactive
    hire_date = db.Column(db.Date, nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now())

    def to_dict(self):
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}


class AttendanceRecord(db.Model):
    __tablename__ = "attendance_records"
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=False)
    date = db.Column(db.Date, nullable=True)
    status = db.Column(db.String(50), nullable=True)  # Present / Absent / Leave
    note = db.Column(db.String(512), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now())

    employee = db.relationship("Employee", backref="attendance", lazy=True)

    def to_dict(self):
        d = {c.name: getattr(self, c.name) for c in self.__table__.columns}
        d["employee"] = self.employee.to_dict() if self.employee else None
        return d


class PayrollRecord(db.Model):
    __tablename__ = "payroll_records"
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=False)
    period_start = db.Column(db.Date, nullable=True)
    period_end = db.Column(db.Date, nullable=True)
    gross_pay = db.Column(db.Numeric(12, 2), nullable=True)
    net_pay = db.Column(db.Numeric(12, 2), nullable=True)
    paid = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now())

    employee = db.relationship("Employee", backref="payrolls", lazy=True)

    def to_dict(self):
        d = {c.name: getattr(self, c.name) for c in self.__table__.columns}
        d["employee"] = self.employee.to_dict() if self.employee else None
        return d


class LeaveRequest(db.Model):
    __tablename__ = "leave_requests"
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=False)
    leave_type = db.Column(db.String(100), nullable=True)
    start_date = db.Column(db.Date, nullable=True)
    end_date = db.Column(db.Date, nullable=True)
    days = db.Column(db.Integer, nullable=True)
    status = db.Column(db.String(50), nullable=True)  # Pending / Approved / Denied
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now())

    employee = db.relationship("Employee", backref="leave_requests", lazy=True)

    def to_dict(self):
        d = {c.name: getattr(self, c.name) for c in self.__table__.columns}
        d["employee"] = self.employee.to_dict() if self.employee else None
        return d


# --------------------
# Procurement
# --------------------
class PurchaseRequest(db.Model):
    __tablename__ = "purchase_requests"
    id = db.Column(db.Integer, primary_key=True)
    request_id = db.Column(db.String(100), unique=True, nullable=False)
    requested_by = db.Column(db.String(255), nullable=True)
    date = db.Column(db.Date, nullable=True)
    item = db.Column(db.String(255), nullable=True)
    quantity = db.Column(db.Integer, nullable=True)
    status = db.Column(db.String(50), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now())
    approved_by = db.Column(db.String(), nullable=True)

    def to_dict(self):
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}


class PurchaseOrder(db.Model):
    __tablename__ = "purchase_orders"
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.String(100), unique=True, nullable=False)
    vendor = db.Column(db.String(255), nullable=True)
    date = db.Column(db.Date, nullable=True)
    item = db.Column(db.String(255), nullable=True)
    quantity = db.Column(db.Integer, nullable=True)
    amount = db.Column(db.Numeric(12, 2), nullable=True)
    status = db.Column(db.String(50), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now())

    def to_dict(self):
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}


class Supplier(db.Model):
    __tablename__ = "suppliers"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    contact_person = db.Column(db.String(255), nullable=True)
    email = db.Column(db.String(255), nullable=True)
    phone = db.Column(db.String(50), nullable=True)
    status = db.Column(db.String(50), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now())

    def to_dict(self):
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}


# --------------------
# Production / BOM / Work Centers
# --------------------
class ProductionOrder(db.Model):
    __tablename__ = "production_orders"
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.String(100), unique=True, nullable=False)
    product = db.Column(db.String(255), nullable=True)
    quantity = db.Column(db.Integer, nullable=True)
    start_date = db.Column(db.Date, nullable=True)
    end_date = db.Column(db.Date, nullable=True)
    status = db.Column(db.String(50), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now())

    def to_dict(self):
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}


class BillOfMaterials(db.Model):
    __tablename__ = "bill_of_materials"
    id = db.Column(db.Integer, primary_key=True)
    product = db.Column(db.String(255), nullable=True)
    component = db.Column(db.String(255), nullable=True)
    quantity_required = db.Column(db.Integer, nullable=True)
    unit = db.Column(db.String(50), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now())

    def to_dict(self):
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}


class WorkCenter(db.Model):
    __tablename__ = "work_centers"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    current_task = db.Column(db.String(255), nullable=True)
    status = db.Column(db.String(50), nullable=True)  # Running / Idle
    operator = db.Column(db.String(255), nullable=True)
    updated_at = db.Column(db.DateTime(timezone=True), server_default=func.now())

    def to_dict(self):
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}


# --------------------
# Assets
# --------------------
class Asset(db.Model):
    __tablename__ = "assets"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    category = db.Column(db.String(255), nullable=True)
    purchase_date = db.Column(db.Date, nullable=True)
    value = db.Column(db.Numeric(12, 2), nullable=True)
    depreciation_rate = db.Column(db.Float, nullable=True)
    status = db.Column(db.String(50), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now())

    def to_dict(self):
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}

# --------------------
# User
# --------------------

class User(db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False, index=True)
    email = db.Column(db.String(255), unique=True, nullable=True, index=True)
    password_hash = db.Column(db.String(255), nullable=True)
    full_name = db.Column(db.String(255), nullable=True)
    group_id = db.Column(db.Integer, nullable=True)        # numeric group id for permissions / grouping
    group_name = db.Column(db.String(100), nullable=True)  # optional human-readable group name
    role = db.Column(db.String(50), nullable=True)
    is_active = db.Column(db.Boolean, default=True)
    last_login = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now())
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def set_password(self, password):
        from werkzeug.security import generate_password_hash
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        from werkzeug.security import check_password_hash
        return check_password_hash(self.password_hash or "", password)

    def to_dict(self):
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}

# --------------------
# Simple utilities
# --------------------
def register_extensions(app):
    """
    Call this from your application factory or app.py after creating the Flask app:
        from models import db, register_extensions
        register_extensions(app)
    """
    db.init_app(app)

class Account(db.Model):
    __tablename__ = "accounts"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    type = db.Column(db.String(80))
    balance = db.Column(db.Numeric(18, 2), default=0)
    currency = db.Column(db.String(10), default="USD")
    status = db.Column(db.String(50), default="active")
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now())
    updated_at = db.Column(db.DateTime, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<Account {self.name} ({self.id})>"

class Invoice(db.Model):
    __tablename__ = "invoices"
    id = db.Column(db.Integer, primary_key=True)
    invoice_id = db.Column(db.String(100), unique=True, index=True)
    customer_id = db.Column(db.Integer, nullable=True)
    date = db.Column(db.DateTime, default=datetime.utcnow)
    due_date = db.Column(db.DateTime, nullable=True)
    amount = db.Column(db.Numeric(18, 2), default=0)
    currency = db.Column(db.String(10), default="USD")
    status = db.Column(db.String(50), default="draft")
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now())

    def __repr__(self):
        return f"<Invoice {self.invoice_id or self.id}>"

class Expense(db.Model):
    __tablename__ = "expenses"
    id = db.Column(db.Integer, primary_key=True)
    account_id = db.Column(db.Integer, nullable=True)
    date = db.Column(db.DateTime, default=datetime.utcnow)
    description = db.Column(db.Text)
    amount = db.Column(db.Numeric(18, 2), default=0)
    category = db.Column(db.String(120))
    status = db.Column(db.String(50), default="recorded")
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now())

    def __repr__(self):
        return f"<Expense {self.id} {self.amount}>"

class JournalEntry(db.Model):
    __tablename__ = "journal_entries"
    id = db.Column(db.Integer, primary_key=True)
    entry_id = db.Column(db.String(100), unique=True, index=True)
    date = db.Column(db.DateTime, default=datetime.utcnow)
    description = db.Column(db.Text)
    debit_account_id = db.Column(db.Integer, nullable=True)
    credit_account_id = db.Column(db.Integer, nullable=True)
    amount = db.Column(db.Numeric(18, 2), default=0)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now())

    def __repr__(self):
        return f"<JournalEntry {self.entry_id or self.id}>"

class FinancialSummaryLine(db.Model):
    __tablename__ = "financial_summary_lines"
    id = db.Column(db.Integer, primary_key=True)
    label = db.Column(db.String(200), nullable=False)
    value_amount = db.Column(db.Numeric(18, 2), default=0)
    currency = db.Column(db.String(10), default="ZMW")
    period = db.Column(db.String(50))
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now())

    def __repr__(self):
        return f"<FinancialSummaryLine {self.label}: {self.value_amount}>"

class IncomeStatementLine(db.Model):
    __tablename__ = "income_statement_lines"
    id = db.Column(db.Integer, primary_key=True)
    period = db.Column(db.String(50))
    category = db.Column(db.String(200))
    amount = db.Column(db.Numeric(18, 2), default=0)
    line_type = db.Column(db.String(50))  # e.g. 'income' or 'expense'
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now())

    def __repr__(self):
        return f"<IncomeStatementLine {self.category} {self.amount}>"

class OutstandingPayment(db.Model):
    __tablename__ = "outstanding_payments"
    id = db.Column(db.Integer, primary_key=True)
    payment_id = db.Column(db.String(100), unique=True, index=True)
    party = db.Column(db.String(200))
    due_date = db.Column(db.DateTime, nullable=True)
    amount = db.Column(db.Numeric(18, 2), default=0)
    status = db.Column(db.String(50), default="open")
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now())

    def __repr__(self):
        return f"<OutstandingPayment {self.payment_id} {self.amount}>"

class FinanceChartData(db.Model):
    __tablename__ = "finance_chart_data"
    id = db.Column(db.Integer, primary_key=True)
    chart_id = db.Column(db.String(100), unique=True, index=True)
    name = db.Column(db.String(200))
    chart_json = db.Column(JSON, nullable=True)  # uses Postgres JSON if available
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now())

    def __repr__(self):
        return f"<FinanceChartData {self.chart_id}>"

class HRReport(db.Model):
    __tablename__ = "hr_reports"
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(250), nullable=False)
    date = db.Column(db.DateTime, default=datetime.utcnow)
    content = db.Column(db.Text)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now())

    def __repr__(self):
        return f"<HRReport {self.title}>"

# --------------------
# Logging / Audit tables
# --------------------
class AuthLog(db.Model):
    __tablename__ = "auth_logs"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, nullable=True, index=True)
    event_type = db.Column(db.String(100), nullable=False)  # login, logout, failed_login, mfa_success...
    success = db.Column(db.Boolean, nullable=True)
    ip_address = db.Column(db.String(100), nullable=True)
    user_agent = db.Column(db.String(1024), nullable=True)
    meta = db.Column(JSON, nullable=True)  # extra context (jsonb)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), index=True)

    def to_dict(self):
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}


class AuditLog(db.Model):
    __tablename__ = "audit_logs"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, nullable=True, index=True)
    action = db.Column(db.String(100), nullable=False)         # create/update/delete/approve/reject
    resource_type = db.Column(db.String(100), nullable=True)   # e.g. PurchaseRequest
    resource_id = db.Column(db.String(100), nullable=True)     # resource identifier (string to be flexible)
    before = db.Column(JSON, nullable=True)                    # snapshot before change
    after = db.Column(JSON, nullable=True)                     # snapshot after change
    reason = db.Column(db.String(512), nullable=True)
    meta = db.Column(JSON, nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), index=True)

    def to_dict(self):
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}


class ErrorLog(db.Model):
    __tablename__ = "error_logs"
    id = db.Column(db.Integer, primary_key=True)
    level = db.Column(db.String(50), nullable=False)           # ERROR, WARN, INFO, DEBUG
    message = db.Column(db.String(1024), nullable=False)
    stacktrace = db.Column(db.Text, nullable=True)
    context = db.Column(JSON, nullable=True)                   # request, user, extra info
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), index=True)

    def to_dict(self):
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}


class TaskEvent(db.Model):
    __tablename__ = "task_event"
    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(String(128), nullable=True)             
    task_type = Column(String(100), nullable=False)          
    event_type = Column(String(32), nullable=False)          
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False)
    duration_seconds = Column(Float, nullable=True)          
    work_center_id = Column(Integer, ForeignKey('work_centers.id'), nullable=True)
    production_order_id = Column(Integer, ForeignKey('production_orders.id'), nullable=True)
    employee_id = Column(Integer, ForeignKey('employees.id'), nullable=True)
    meta = Column(JSON, nullable=True)                       
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index('ix_taskevent_tasktype_timestamp', 'task_type', 'timestamp'),
        Index('ix_taskevent_wc_timestamp', 'work_center_id', 'timestamp'),
    )
