from datetime import datetime
from flask_sqlalchemy import SQLAlchemy

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
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

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
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    customer = db.relationship("Customer", backref="sales_orders", lazy=True)

    def to_dict(self):
        d = {c.name: getattr(self, c.name) for c in self.__table__.columns}
        d["customer"] = self.customer.to_dict() if self.customer else None
        return d


class SalesForecast(db.Model):
    __tablename__ = "sales_forecasts"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=True)
    payload = db.Column(db.JSON, nullable=True)  # store Plotly graph data/layout or raw forecast
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

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
    updated_at = db.Column(db.DateTime, default=datetime.utcnow)

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
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

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
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

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
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

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
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}


class AttendanceRecord(db.Model):
    __tablename__ = "attendance_records"
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=False)
    date = db.Column(db.Date, nullable=True)
    status = db.Column(db.String(50), nullable=True)  # Present / Absent / Leave
    note = db.Column(db.String(512), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

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
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

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
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

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
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

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
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

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
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

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
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}


class BillOfMaterials(db.Model):
    __tablename__ = "bill_of_materials"
    id = db.Column(db.Integer, primary_key=True)
    product = db.Column(db.String(255), nullable=True)
    component = db.Column(db.String(255), nullable=True)
    quantity_required = db.Column(db.Integer, nullable=True)
    unit = db.Column(db.String(50), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}


class WorkCenter(db.Model):
    __tablename__ = "work_centers"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    current_task = db.Column(db.String(255), nullable=True)
    status = db.Column(db.String(50), nullable=True)  # Running / Idle
    operator = db.Column(db.String(255), nullable=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow)

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
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

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
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
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