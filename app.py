from flask import Flask, render_template, jsonify, Response, send_file, request, redirect, url_for, json, flash, make_response, abort
from pyspark.sql import SparkSession
from pyspark.sql.functions import col
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
import analytics
import asset_upload_module
import asset_functions
import barcode
from barcode.writer import ImageWriter
import os
import subprocess
from flask_login import LoginManager, UserMixin, login_user, logout_user, current_user, login_required
from datetime import datetime, timezone
import pdfkit
from sqlalchemy import text, func
from sqlalchemy.exc import OperationalError, DataError
from flask_migrate import Migrate
from routes.assets_upload import bp as assets_upload_bp
from plotly.utils import PlotlyJSONEncoder
import csv
import json
import traceback
from types import SimpleNamespace
from flask_wtf import CSRFProtect

from models import (
    db, register_extensions,
    Customer, Employee,
    InventoryItem, Shipment,
    PurchaseRequest, PurchaseOrder, Supplier,
    ProductionOrder, BillOfMaterials, WorkCenter,
    Asset, SalesOrder, SalesForecast, Payment, User, LeaveRequest,
    CashFlowRecord, AttendanceRecord, PayrollRecord, HRReport, Account,
    OutstandingPayment, FinanceChartData, FinancialSummaryLine, JournalEntry,
    Expense, Invoice, IncomeStatementLine,
    AuthLog, AuditLog, ErrorLog
)

CashFlowEntry = CashFlowRecord
Attendance = AttendanceRecord
Payroll = PayrollRecord


app = Flask(__name__)
app.secret_key = "supersecretkey"  # Replace with a real secret key

#csrf = CSRFProtect(app)

app.register_blueprint(assets_upload_bp)

# configure SQLAlchemy (use env var or fallback)
username = 'postgres'
password = 'kango'
directory = 'Final-Year-Project'

app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get(
    'DATABASE_URL',
    f'postgresql://{username}:{password}@localhost:5432/{directory}'
)
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# initialize DB extensions
register_extensions(app)
migrate = Migrate(app, db)


config = pdfkit.configuration(wkhtmltopdf=r'C:\Progra~1\wkhtmltopdf\bin\wkhtmltopdf.exe')

# Global error handler to persist uncaught exceptions to DB
@app.errorhandler(Exception)
def handle_uncaught_exception(e):
    try:
        stack = traceback.format_exc()
        ctx = {
            "path": request.path if request else None,
            "method": request.method if request else None,
            "user_id": _current_user_id()
        }
        log_error(level="ERROR", message=str(e), stacktrace=stack, context=ctx)
    except Exception:
        app.logger.exception("Failed to persist ErrorLog")

    # re-raise default behavior for debug mode; return a 500 page in production
    return render_template('500.html'), 500

# ------------------- Login Manager Setup -------------------
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"

# lightweight wrapper so Flask-Login works with models.User (DB model)
class AuthUser(UserMixin):
    def __init__(self, model):
        self._model = model
        # Flask-Login expects get_id() to return a string
        self.id = str(getattr(model, "id", ""))
        self.username = getattr(model, "username", None)

    @property
    def model(self):
        return self._model

    @property
    def is_active(self):
        # return model's is_active flag (default True) without setting an attribute
        return bool(getattr(self._model, "is_active", True))

# simple fallback in-memory user wrapper (for development)
class FallbackUser:
    def __init__(self, username):
        self.id = username
        self.username = username
        self.is_active = True

@login_manager.user_loader
def load_user(user_id):
    try:
        # try treating user_id as integer primary key first
        try:
            uid = int(user_id)
            user_row = db.session.get(User, uid)
            if user_row:
                return AuthUser(user_row)
        except (ValueError, TypeError):
            # not an int — do username lookup
            user_row = None

        if user_row is None:
            try:
                user_row = db.session.query(User).filter_by(username=user_id).first()
                if user_row:
                    return AuthUser(user_row)
            except (OperationalError, DataError):
                # DB trouble during username lookup — fall through to fallback
                pass

    except (OperationalError, DataError):
        # DB not ready — fall back to in-memory users
        pass
"""
    # fallback to in-memory USERS
    if user_id in USERS:
        return AuthUser(FallbackUser(user_id))
    return None"""


# --- Logging helpers (persist to DB tables: auth_logs, audit_logs, error_logs) ---
def _current_user_id():
    try:
        m = getattr(current_user, "model", None)
        if m is not None:
            return getattr(m, "id", None)
        # fallback if current_user is a plain object
        return getattr(current_user, "id", None)
    except Exception:
        return None

def log_auth(event_type, success=True, user_id=None, ip=None, user_agent=None, meta=None):
    """Append to auth_logs (best-effort)."""
    try:
        uid = user_id if user_id is not None else _current_user_id()
        al = AuthLog(
            user_id=uid,
            event_type=event_type,
            success=bool(success),
            ip_address=ip or request.remote_addr if request else None,
            user_agent=user_agent or (request.headers.get("User-Agent") if request else None),
            meta=meta or {}
        )
        db.session.add(al)
        db.session.commit()
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass
        app.logger.exception("Failed to write AuthLog")

def log_audit(action, resource_type=None, resource_id=None, before=None, after=None, reason=None, user_id=None, meta=None):
    """Append to audit_logs (best-effort)."""
    try:
        uid = user_id if user_id is not None else _current_user_id()
        al = AuditLog(
            user_id=uid,
            action=action,
            resource_type=resource_type,
            resource_id=str(resource_id) if resource_id is not None else None,
            before=(before if isinstance(before, (dict, list)) else (before.to_dict() if hasattr(before, "to_dict") else None)),
            after=(after if isinstance(after, (dict, list)) else (after.to_dict() if hasattr(after, "to_dict") else None)),
            reason=reason,
            meta=meta or {}
        )
        db.session.add(al)
        db.session.commit()
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass
        app.logger.exception("Failed to write AuditLog")

def log_error(level, message, stacktrace=None, context=None):
    """Append to error_logs (best-effort)."""
    try:
        el = ErrorLog(
            level=(level or "ERROR"),
            message=str(message)[:1024],
            stacktrace=(stacktrace or "")[:65536],
            context=context or {}
        )
        db.session.add(el)
        db.session.commit()
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass
        app.logger.exception("Failed to write ErrorLog")

# ------------------- Admin: Create user -------------------
@app.route("/admin/users/create", methods=["POST"])
@login_required
def admin_create_user():
    # only allow administrators (role == 'admin' or group_id == 0)
    model = getattr(current_user, "model", None)
    is_admin = bool(model and (getattr(model, "role", None) == "admin" or getattr(model, "group_id", None) == 0))
    if not is_admin:
        return render_template("403.html"), 403

    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    email = request.form.get("email", "")
    full_name = request.form.get("full_name", "")
    role = request.form.get("role", "pending_user")
    group_id_raw = request.form.get("group_id", "1")
    is_active = request.form.get("is_active") == "on"

    if not username or not password:
        flash("Username and password are required.", "error")
        return redirect(url_for("admin_users"))

    try:
        # ensure username unique
        existing = db.session.query(User).filter_by(username=username).first()
        if existing:
            flash("Username already exists.", "error")
            return redirect(url_for("admin_users"))

        new_user = User(username=username)

        # optional fields if model supports them
        if hasattr(new_user, "email"):
            setattr(new_user, "email", email)
        if hasattr(new_user, "full_name"):
            setattr(new_user, "full_name", full_name)

        # group_id
        try:
            gid = int(group_id_raw)
            setattr(new_user, "group_id", gid)
        except Exception:
            setattr(new_user, "group_id", group_id_raw)

        # role
        if hasattr(new_user, "role"):
            setattr(new_user, "role", role)

        # password (prefer model helper)
        if hasattr(new_user, "set_password"):
            new_user.set_password(password)
        else:
            if hasattr(new_user, "password_hash"):
                setattr(new_user, "password_hash", generate_password_hash(password))
            elif hasattr(new_user, "password"):
                setattr(new_user, "password", generate_password_hash(password))
            else:
                setattr(new_user, "password_hash", generate_password_hash(password))

        # is_active
        if hasattr(new_user, "is_active"):
            setattr(new_user, "is_active", bool(is_active))
        else:
            setattr(new_user, "is_active", bool(is_active))

        db.session.add(new_user)
        db.session.commit()
        flash(f"User '{username}' created.", "success")
        # audit
        try:
            log_audit(action="create_user", resource_type="User", resource_id=getattr(new_user, "id", None), after=new_user)
        except Exception:
            app.logger.debug("Audit log failed for create_user", exc_info=True)
    except Exception as e:
        db.session.rollback()
        app.logger.exception("Admin create user failed")
        flash("Failed to create user.", "error")
        flash(str(e))
    return redirect(url_for("admin_users"))

# ------------------- Login Route (use DB model when available) -------------------
@app.route("/login", methods=["GET", "POST"])
def login():
    """
    Authenticate against models.User when available. Fall back to in-memory USERS.
    """
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        # Try DB authentication
        try:
            user = db.session.query(User).filter_by(username=username).first()
            if user:
                # prefer model's password checker if provided
                if hasattr(user, "check_password"):
                    ok = user.check_password(password)
            elif getattr(user, "password_hash", None) is not None:
                ok = check_password_hash(getattr(user, "password_hash"), password)
            elif getattr(user, "password", None) is not None:
                ok = check_password_hash(getattr(user, "password"), password)
            else:
                ok = False

            if ok:
                login_user(AuthUser(user))
                # record successful login
                log_auth(event_type="login", success=True, user_id=getattr(user, "id", None))
                return redirect(url_for("index"))
            else:
                # record failed login attempt (no user id)
                log_auth(event_type="login_failed", success=False, user_id=None, meta={"username": username})
                return render_template("login.html", error="Invalid username or password")
        except (OperationalError, DataError):
            # DB not available — try in-memory
            pass

        return render_template("login.html", error="Invalid username or password")

    return render_template("login.html")

# ------------------- Context Processor (for base.html) -------------------
@app.context_processor
def inject_globals():
    return {
        "current_user": current_user,
        "current_year": datetime.now().year,
        "system_name": "Data-Driven ERP System",
        "version": "1.0.0"
    }

# ------------------- Logout Route -------------------
@app.route("/logout", methods=["POST"])
@login_required
def logout():
    try:
        log_auth(event_type="logout", success=True)
    except Exception:
        app.logger.debug("Auth log for logout failed", exc_info=True)
    logout_user()
    return redirect(url_for("login"))

UPLOAD_FOLDER = 'uploads'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16 MB limit
hadoop_bin_path = r'C:\hadoop\hadoop-3.3.5\bin\hadoop.cmd'
os.environ['HADOOP_USER_NAME'] = 'Kango Chipaila'

sales_trend_graph = analytics.generate_sales_trend()
#goods_performance_pie_chart = analytics.generate_goods_performance_pchart()
customer_expenditure_pie_chart = analytics.generate_customer_expenditure_distribution_pchart()

#spark = SparkSession.builder.appName("CSVUpload").getOrCreate()

# In-memory asset list (for demo; later replaced by DB)
assets_data = [
    {"id": 1, "name": "Office Computer", "category": "IT Equipment", "purchase_date": "2023-03-12", "value": 1500, "depreciation_rate": 20, "status": "Active"},
    {"id": 2, "name": "Company Car", "category": "Vehicles", "purchase_date": "2021-07-22", "value": 25000, "depreciation_rate": 15, "status": "Active"},
    {"id": 3, "name": "Office Printer", "category": "Office Equipment", "purchase_date": "2020-02-10", "value": 600, "depreciation_rate": 30, "status": "Retired"}
]

DASHBOARD_DATA_FILE = 'static/js/test_dashboard_data.json'

def load_dashboard_data():
    with open(DASHBOARD_DATA_FILE, 'r') as f:
        return json.load(f)

@app.route('/')
@login_required
def index():
    def safe_get(obj, attr, default=None):
        try:
            val = getattr(obj, attr)
            return val if val is not None else default
        except Exception:
            return default

    try:
        # KPIs
        total_orders = db.session.query(func.count()).select_from(SalesOrder).scalar() or 0
        total_customers = db.session.query(func.count()).select_from(Customer).scalar() or 0

        revenue_col = None
        if hasattr(SalesOrder, "total"):
            revenue_col = getattr(SalesOrder, "total")
        elif hasattr(SalesOrder, "amount"):
            revenue_col = getattr(SalesOrder, "amount")
        if revenue_col is not None:
            total_revenue = float(db.session.query(func.coalesce(func.sum(revenue_col), 0)).scalar() or 0.0)
        else:
            total_revenue = 0.0

        recent_q = db.session.query(SalesOrder).order_by(getattr(SalesOrder, "id", SalesOrder)).limit(5)

        recent_orders_rows = recent_q.all()

        recent_orders = []
        for o in recent_orders_rows:
            recent_orders.append({
                "id": safe_get(o, "id", ""),
                "customer": (safe_get(o, "customer_name") or safe_get(o, "customer") or ""),
                "date": (safe_get(o, "order_date") or safe_get(o, "created_at") or safe_get(o, "date") or ""),
                "total": float(safe_get(o, "total") or safe_get(o, "amount") or 0.0)
            })
        recent_orders = []

        try:
            if 'Customer' in globals() and 'SalesOrder' in globals() and hasattr(SalesOrder, "customer_id") and hasattr(Customer, "id"):
                rows = (
                    db.session.query(SalesOrder, Customer)
                    .join(Customer, getattr(SalesOrder, "customer_id") == getattr(Customer, "id"))
                    .order_by(getattr(SalesOrder, "id", SalesOrder).desc())
                    .limit(5)
                    .all()
                )

                for so, cust in rows:
                    date_val = getattr(so, "order_date", None) or getattr(so, "created_at", None) or getattr(so, "date", None)
                    if hasattr(date_val, "isoformat"):
                        date_val = date_val.isoformat()
                    total_val = getattr(so, "total", None) or getattr(so, "amount", None) or 0.0
                    try:
                        total_val = float(total_val)
                    except Exception:
                        total_val = 0.0
                    recent_orders.append({
                        "order_id": getattr(so, "id", None),
                        "customer": getattr(cust, "name", "") or getattr(cust, "customer_name", "") or "",
                        "date": date_val or "",
                        "total": total_val,
                        "status": getattr(so, "status", "") or ""
                    })
            else:
                recent_q = db.session.query(SalesOrder).order_by(getattr(SalesOrder, "id", SalesOrder).desc()).limit(5)
                recent_orders_rows = recent_q.all()
                for o in recent_orders_rows:
                    date_val = getattr(o, "order_date", None) or getattr(o, "created_at", None) or getattr(o, "date", None)
                    if hasattr(date_val, "isoformat"):
                        date_val = date_val.isoformat()
                    total_val = getattr(o, "total", None) or getattr(o, "amount", None) or 0.0
                    try:
                        total_val = float(total_val)
                    except Exception:
                        total_val = 0.0
                    # try relationship or stored customer name
                    cust_name = getattr(o, "customer_name", None) or getattr(o, "customer", None)
                    if hasattr(cust_name, "name"):
                        cust_name = getattr(cust_name, "name")
                    recent_orders.append({
                        "id": getattr(o, "id", None),
                        "customer": cust_name or "",
                        "date": date_val or "",
                        "total": total_val
                    })
        except Exception:
            app.logger.exception("Failed to build recent_orders")
            recent_orders = []

        top_customers = []

        try:
            if hasattr(SalesOrder, "customer_id"):
                value_col = getattr(SalesOrder, "total", None) or getattr(SalesOrder, "amount", None)
                if value_col is not None:
                    rows = (
                        db.session.query(
                            Customer.id.label("customer_id"),
                            Customer.name.label("customer_name"),
                            func.coalesce(func.sum(value_col), 0).label("total")
                        )
                        .join(SalesOrder, SalesOrder.customer_id == Customer.id)
                        .group_by(Customer.id, Customer.name)
                        .order_by(func.sum(value_col).desc())
                        .limit(5)
                        .all()
                    )
                    top_customers = [
                        {"name": r.customer_name or "Unknown", "total": float(r.total or 0)}
                        for r in rows
                    ]
                else:
                    # no numeric order value column; fall back to listing customers
                    cust_rows = db.session.query(Customer).limit(5).all()
                    top_customers = [{"name": getattr(c, "name", ""), "total": 0.0} for c in cust_rows]
            else:
                cust_rows = db.session.query(Customer).limit(5).all()
                top_customers = [{"name": getattr(c, "name", ""), "total": 0.0} for c in cust_rows]
        except Exception:
            app.logger.exception("Failed to compute top_customers")
            top_customers = []
        
        #Total inventory value
        try:
            if 'InventoryItem' in globals():
                total_inventory_value = float(
                    db.session.query(
                        func.coalesce(func.sum(InventoryItem.quantity * InventoryItem.unit_cost), 0)
                    ).scalar() or 0.0
                )
            else:
                rows = db.session.query(InventoryItem).all()
                s = 0.0
                for r in rows:
                    try:
                        qty = int(getattr(r, "quantity", 0) or 0)
                        cost = float(getattr(r, "unit_cost", 0.0) or 0.0)
                        s += qty * cost
                    except Exception:
                        continue
                total_inventory_value = float(s)
        except Exception:
            print(Exception)
            app.logger.exception("Failed to compute total_inventory_value")
            total_inventory_value = 0.0

        kpi = {
            "total_orders": int(total_orders),
            "total_customers": int(total_customers),
            "total_revenue": f"{float(total_revenue):,.2f}",
            "inventory_value": f"{total_inventory_value:,.2f}"
        }

        try:
            sales_trend_graph_local = {"data": [], "layout": {"title": "Sales Trend"}}
            if 'SalesOrder' in globals() and hasattr(SalesOrder, "id"):
                date_col = getattr(SalesOrder, "order_date", getattr(SalesOrder, "date", None))
                value_col = getattr(SalesOrder, "total", getattr(SalesOrder, "amount", None))
                if date_col is not None and value_col is not None:
                    rows = (
                        db.session.query(func.date_trunc('month', date_col).label('month'),
                                         func.coalesce(func.sum(value_col), 0).label('total'))
                        .group_by('month')
                        .order_by('month')
                        .all()
                    )
                    months = [r[0].strftime("%Y-%m") for r in rows if r[0] is not None]
                    totals = [float(r[1]) for r in rows]
                    sales_trend_graph_local = {
                        "data": [{"x": months, "y": totals, "type": "scatter", "mode": "lines+markers", "name": "Monthly Sales"}],
                        "layout": {"title": "Sales Trend", "xaxis": {"title": "Month"}, "yaxis": {"title": "Total"}}
                    }

            # Top customers by revenue (pie)
            customers = {"data": [], "layout": {"title": "Top Customers"}}
            if 'Customer' in globals() and 'SalesOrder' in globals() and hasattr(SalesOrder, "customer_id"):
                value_col = getattr(SalesOrder, "total", getattr(SalesOrder, "amount", None))
                if value_col is not None:
                    rows = (
                        db.session.query(Customer.name, func.coalesce(func.sum(value_col), 0).label('total'))
                        .join(SalesOrder, getattr(SalesOrder, "customer_id") == getattr(Customer, "id"))
                        .group_by(Customer.name)
                        .order_by(func.sum(value_col).desc())
                        .limit(10)
                        .all()
                    )
                    labels = [r[0] or "Unknown" for r in rows]
                    vals = [float(r[1]) for r in rows]
                    if labels and vals:
                        customers = {"data": [{"labels": labels, "values": vals, "type": "pie", "name": "Customers"}],
                                     "layout": {"title": "Top Customers by Expenditure"}}

            
            product_performance_graph = {"data": [], "layout": {"title": "Product Performance"}}
            prod_field = None
            if 'SalesOrder' in globals():
                for cand in ("product", "product_name", "item", "sku"):
                    if hasattr(SalesOrder, cand):
                        prod_field = getattr(SalesOrder, cand)
                        break
            if prod_field is not None:
                rows = (
                    db.session.query(prod_field, func.count().label("cnt"))
                    .group_by(prod_field)
                    .order_by(func.count().desc())
                    .limit(10)
                    .all()
                )
                labels = [str(r[0]) if r[0] is not None else "Unknown" for r in rows]
                vals = [int(r[1]) for r in rows]
                if labels and vals:
                    product_performance_graph = {"data": [{"labels": labels, "values": vals, "type": "pie", "name": "Products"}],
                                                 "layout": {"title": "Top Products (by count)"}}
            elif 'InventoryItem' in globals() and hasattr(InventoryItem, "category"):
                rows = (
                    db.session.query(InventoryItem.category, func.count().label("cnt"))
                    .group_by(InventoryItem.category)
                    .order_by(func.count().desc())
                    .all()
                )
                labels = [r[0] or "Unknown" for r in rows]
                vals = [int(r[1]) for r in rows]
                if labels and vals:
                    product_performance_graph = {"data": [{"labels": labels, "values": vals, "type": "pie", "name": "Inventory categories"}],
                                                 "layout": {"title": "Inventory by Category"}}
            product_performance_graph = {"data": [], "layout": {"title": "Product Performance"}}

            try:
                if 'SalesOrder' in globals() and 'InventoryItem' in globals() and hasattr(SalesOrder, "inventory_id") and hasattr(InventoryItem, "id"):
                    prod_col = getattr(InventoryItem, "product", None) or getattr(InventoryItem, "name", None) or getattr(InventoryItem, "item", None)
                    if prod_col is not None:
                        rows = (
                            db.session.query(prod_col.label("product"), func.count().label("cnt"))
                            .join(SalesOrder, getattr(SalesOrder, "inventory_id") == getattr(InventoryItem, "id"))
                            .group_by(prod_col)
                            .order_by(func.count().desc())
                            .limit(10)
                            .all()
                        )
                        labels = [str(r.product) if getattr(r, "product", None) is not None else "Unknown" for r in rows]
                        vals = [int(r.cnt) for r in rows]
                        if labels and vals:
                            product_performance_graph = {"data": [{"labels": labels, "values": vals, "type": "pie", "name": "Products"}],
                                                         "layout": {"title": "Top Products (by order count)"}}
                if not product_performance_graph["data"]:
                    prod_field = None
                    if 'SalesOrder' in globals():
                        for cand in ("product", "product_name", "item", "sku"):
                            if hasattr(SalesOrder, cand):
                                prod_field = getattr(SalesOrder, cand)
                                break
                    if prod_field is not None:
                        rows = (
                            db.session.query(prod_field, func.count().label("cnt"))
                            .group_by(prod_field)
                            .order_by(func.count().desc())
                            .limit(10)
                            .all()
                        )
                        labels = [str(r[0]) if r[0] is not None else "Unknown" for r in rows]
                        vals = [int(r[1]) for r in rows]
                        if labels and vals:
                            product_performance_graph = {"data": [{"labels": labels, "values": vals, "type": "pie", "name": "Products"}],
                                                         "layout": {"title": "Top Products (by count)"}}
                if not product_performance_graph["data"] and 'InventoryItem' in globals() and hasattr(InventoryItem, "category"):
                    rows = (
                        db.session.query(InventoryItem.category, func.count().label("cnt"))
                        .group_by(InventoryItem.category)
                        .order_by(func.count().desc())
                        .all()
                    )
                    labels = [r[0] or "Unknown" for r in rows]
                    vals = [int(r[1]) for r in rows]
                    if labels and vals:
                        product_performance_graph = {"data": [{"labels": labels, "values": vals, "type": "pie", "name": "Inventory categories"}],
                                                     "layout": {"title": "Inventory by Category"}}
            except Exception:
                app.logger.exception("Failed to build product_performance_graph using inventory join/fallbacks")
 

            # Inventory status counts
            inventory_status_graph = {"data": [], "layout": {"title": "Inventory Status"}}
            if 'InventoryItem' in globals() and hasattr(InventoryItem, "status"):
                rows = db.session.query(InventoryItem.status, func.count().label("cnt")).group_by(InventoryItem.status).all()
                labels = [r[0] or "Unknown" for r in rows]
                vals = [int(r[1]) for r in rows]
                if labels and vals:
                    inventory_status_graph = {"data": [{"labels": labels, "values": vals, "type": "pie", "name": "Inventory Status"}],
                                              "layout": {"title": "Inventory Status"}}

            # Order status counts
            order_status_graph = {"data": [], "layout": {"title": "Order Status"}}
            if 'SalesOrder' in globals() and hasattr(SalesOrder, "status"):
                rows = db.session.query(SalesOrder.status, func.count().label("cnt")).group_by(SalesOrder.status).all()
                labels = [r[0] or "Unknown" for r in rows]
                vals = [int(r[1]) for r in rows]
                if labels and vals:
                    order_status_graph = {"data": [{"labels": labels, "values": vals, "type": "pie", "name": "Order Status"}],
                                          "layout": {"title": "Order Status"}}

            # Revenue vs Expenses (monthly)
            revenue_expenses_graph = {"data": [], "layout": {"title": "Revenue vs Expenses"}}
            try:
                # build monthly maps
                revenue_map = {}
                expense_map = {}
                # revenue from SalesOrder
                if 'SalesOrder' in globals() and date_col is not None and value_col is not None:
                    rev_rows = (
                        db.session.query(func.date_trunc('month', date_col).label('month'),
                                         func.coalesce(func.sum(value_col), 0).label('total'))
                        .group_by('month')
                        .order_by('month')
                        .all()
                    )
                    for r in rev_rows:
                        if r[0] is not None:
                            k = r[0].strftime("%Y-%m")
                            revenue_map[k] = float(r[1])
                # expenses from Expense.amount / date
                if 'Expense' in globals() and hasattr(Expense, "date") and hasattr(Expense, "amount"):
                    exp_rows = (
                        db.session.query(func.date_trunc('month', Expense.date).label('month'),
                                         func.coalesce(func.sum(Expense.amount), 0).label('total'))
                        .group_by('month')
                        .order_by('month')
                        .all()
                    )
                    for r in exp_rows:
                        if r[0] is not None:
                            k = r[0].strftime("%Y-%m")
                            expense_map[k] = float(r[1])
                # unify timeline
                months = sorted(set(list(revenue_map.keys()) + list(expense_map.keys())))
                if months:
                    rev_vals = [revenue_map.get(m, 0.0) for m in months]
                    exp_vals = [expense_map.get(m, 0.0) for m in months]
                    revenue_expenses_graph = {
                        "data": [
                            {"x": months, "y": rev_vals, "type": "scatter", "mode": "lines+markers", "name": "Revenue"},
                            {"x": months, "y": exp_vals, "type": "scatter", "mode": "lines+markers", "name": "Expenses"}
                        ],
                        "layout": {"title": "Revenue vs Expenses", "xaxis": {"title": "Month"}, "yaxis": {"title": "Amount"}}
                    }
            except Exception:
                # leave fallback empty
                app.logger.debug("Revenue/expenses graph build failed", exc_info=True)

            # Employee status
            employee_status_graph = {"data": [], "layout": {"title": "Employee Status"}}
            if 'Employee' in globals() and hasattr(Employee, "status"):
                rows = db.session.query(Employee.status, func.count().label("cnt")).group_by(Employee.status).all()
                labels = [r[0] or "Unknown" for r in rows]
                vals = [int(r[1]) for r in rows]
                if labels and vals:
                    employee_status_graph = {"data": [{"labels": labels, "values": vals, "type": "pie", "name": "Employees"}],
                                             "layout": {"title": "Employee Status"}}

        except Exception:
            # fall back to simple empty placeholders if any DB error occurs
            app.logger.exception("Failed to build DB-backed graphs; using placeholders")
            sales_trend_graph_local = sales_trend_graph or {"data": [], "layout": {}}
            customers = goods_performance_pie_chart or {"data": [], "layout": {}}
            product_performance_graph = goods_performance_pie_chart or {"data": [], "layout": {}}
            inventory_status_graph = {"data": [], "layout": {}}
            order_status_graph = {"data": [], "layout": {}}
            revenue_expenses_graph = {"data": [], "layout": {}}
            employee_status_graph = {"data": [], "layout": {}}


        # recent activity: synthesize from recent orders/payments if possible
        recent_activity = []
        try:
            payments = db.session.query(Payment).order_by(getattr(Payment, "id", Payment)).limit(5).all()
            for p in payments:
                recent_activity.append(f"Payment {safe_get(p,'id','')} amount {safe_get(p,'amount','')}")
        except Exception:
            recent_activity = []

        return render_template(
            'index.html',
            kpi=kpi,
            alerts=[],
            recent_activity=recent_activity,
            top_customers=top_customers,
            recent_orders=recent_orders,
            sales_trend_graph=sales_trend_graph_local,
            customers=customers,
            product_performance_graph=product_performance_graph,
            inventory_status_graph=inventory_status_graph,
            order_status_graph=order_status_graph,
            revenue_expenses_graph=revenue_expenses_graph,
            employee_status_graph=employee_status_graph
        )

    except Exception as e:
        # If anything fails (DB unavailable / schema mismatch), fall back to the existing JSON file
        app.logger.exception("Failed to load dashboard data from DB, falling back to static data")
        data = load_dashboard_data()
        kpi = data.get('kpi', {})
        alerts = data.get('alerts', [])
        recent_activity = data.get('recent_activity', [])
        top_customers = data.get('top_customers', [])
        recent_orders = data.get('recent_orders', [])
        sales_trend_graph = data.get('sales_trend_graph', {'data': [], 'layout': {}})
        customers = data.get('customers_graph', {'data': [], 'layout': {}})
        product_performance_graph = data.get('product_performance_graph', {'data': [], 'layout': {}})
        inventory_status_graph = data.get('inventory_status_graph', {'data': [], 'layout': {}})
        order_status_graph = data.get('order_status_graph', {'data': [], 'layout': {}})
        revenue_expenses_graph = data.get('revenue_expenses_graph', {'data': [], 'layout': {}})
        employee_status_graph = data.get('employee_status_graph', {'data': [], 'layout': {}})
        return render_template(
            'index.html',
            kpi=kpi,
            alerts=alerts,
            recent_activity=recent_activity,
            top_customers=top_customers,
            recent_orders=recent_orders,
            sales_trend_graph=sales_trend_graph,
            customers=customers,
            product_performance_graph=product_performance_graph,
            inventory_status_graph=inventory_status_graph,
            order_status_graph=order_status_graph,
            revenue_expenses_graph=revenue_expenses_graph,
            employee_status_graph=employee_status_graph
        )



@app.route('/add_payment', methods=['GET', 'POST'])
@login_required
def add_payment():
    """
    Create a payment record.
    If status is 'Paid', save to Payment table (history) with generated payment_id.
    Otherwise save to OutstandingPayment table (liability) with generated payment_id.
    """
    if request.method == 'POST':
        party = request.form.get('party', '').strip()
        due_date = request.form.get('due_date', '').strip()
        amount = request.form.get('amount', '').strip()
        status = request.form.get('status', '').strip()

        try:
            # Paid -> Payment history
            if status.lower() == 'paid' and 'Payment' in globals():
                p = Payment()

                # generate payment id based on count + 1
                try:
                    cnt = db.session.query(func.count()).select_from(Payment).scalar() or 0
                    next_num = int(cnt) + 1
                except Exception:
                    try:
                        # fallback to max id + 1
                        max_id = db.session.query(func.coalesce(func.max(getattr(Payment, "id", Payment)), 0)).scalar() or 0
                        next_num = int(max_id) + 1
                    except Exception:
                        next_num = int(datetime.utcnow().timestamp()) % 100000

                payment_id = f"PAY-{next_num:04d}"
                _set_attr_if_exists(p, "payment_id", payment_id)

                _set_attr_if_exists(p, "party", party)
                _set_attr_if_exists(p, "date", due_date, date_try=True)  # map form due_date -> Payment.date
                _set_attr_if_exists(p, "amount", amount, cast_float=True)
                _set_attr_if_exists(p, "status", status)

                # fallback description if model lacks party
                if not hasattr(p, 'party') and hasattr(p, 'description'):
                    _set_attr_if_exists(p, "description", f"Payment to {party}")

                db.session.add(p)
                db.session.commit()
                flash('Payment recorded in history.', 'success')
                return redirect(url_for('accounting_overview'))

            # Otherwise -> OutstandingPayment
            if 'OutstandingPayment' in globals():
                op = OutstandingPayment()

                # generate outstanding payment id based on count + 1
                try:
                    cnt = db.session.query(func.count()).select_from(OutstandingPayment).scalar() or 0
                    next_num = int(cnt) + 1
                except Exception:
                    try:
                        max_id = db.session.query(func.coalesce(func.max(getattr(OutstandingPayment, "id", OutstandingPayment)), 0)).scalar() or 0
                        next_num = int(max_id) + 1
                    except Exception:
                        next_num = int(datetime.utcnow().timestamp()) % 100000

                op_id = f"OP-{next_num:04d}"
                _set_attr_if_exists(op, "payment_id", op_id)

                _set_attr_if_exists(op, "party", party)
                _set_attr_if_exists(op, "due_date", due_date, date_try=True)
                _set_attr_if_exists(op, "amount", amount, cast_float=True)
                _set_attr_if_exists(op, "status", status)

                db.session.add(op)
                db.session.commit()
                flash('Outstanding payment recorded.', 'success')
                return redirect(url_for('accounting_overview'))

        except Exception:
            db.session.rollback()
            app.logger.exception("Failed to insert payment into DB; falling back to file")

        try:
            data = load_finance()
            payments = data.get('outstanding_payments', [])
            new_id = 1
            if payments:
                try:
                    numeric_ids = [int(''.join(filter(str.isdigit, (p.get('payment_id') or '')))) for p in payments if p.get('payment_id')]
                    if numeric_ids:
                        new_id = max(numeric_ids) + 1
                except Exception:
                    new_id = len(payments) + 1
            new_payment = {
                'payment_id': f"OP-{new_id:04d}",
                'party': party,
                'due_date': due_date,
                'amount': float(amount or 0),
                'status': status
            }
            payments.append(new_payment)
            data['outstanding_payments'] = payments
            save_finance(data)
            flash('Payment saved to file fallback.', 'success')
        except Exception:
            app.logger.exception("Failed to append payment to finance JSON")
            flash('Failed to add payment.', 'error')

        return redirect(url_for('accounting_overview'))

    return render_template('add_payment.html')

@app.route('/add_expense', methods=['GET', 'POST'])
@login_required
def add_expense():
    if request.method == 'POST':
        date_val = request.form.get('date', '').strip()
        description = request.form.get('description', '').strip()
        category = request.form.get('category', '').strip()
        amount = request.form.get('amount', '').strip()
        account_id = request.form.get('account_id', '').strip()

        try:
            if 'Expense' in globals():
                exp = Expense()
                _set_attr_if_exists(exp, "date", date_val, date_try=True)
                _set_attr_if_exists(exp, "description", description)
                _set_attr_if_exists(exp, "category", category)
                _set_attr_if_exists(exp, "amount", amount, cast_float=True)
                if account_id and account_id.isdigit():
                    _set_attr_if_exists(exp, "account_id", int(account_id))
                
                db.session.add(exp)
                db.session.commit()
                flash('Expense recorded successfully!', 'success')
                return redirect(url_for('accounting_overview'))
        except Exception:
            db.session.rollback()
            app.logger.exception("Failed to add expense")
            flash('Failed to add expense.', 'error')

    # Load accounts for the dropdown if available
    accounts = []
    if 'Account' in globals():
        try:
            accounts = db.session.query(Account).all()
        except:
            pass

    return render_template('add_expense.html', accounts=accounts)

@app.route('/add_journal_entry', methods=['GET', 'POST'])
@login_required
def add_journal_entry():
    """
    Record a manual journal entry (General Ledger).
    Redirects to accounting_overview.
    """
    if request.method == 'POST':
        date_val = request.form.get('date', '').strip()
        description = request.form.get('description', '').strip()
        debit_account = request.form.get('debit_account_id', '').strip()
        credit_account = request.form.get('credit_account_id', '').strip()
        amount = request.form.get('amount', '').strip()

        try:
            if 'JournalEntry' in globals():
                je = JournalEntry()
                _set_attr_if_exists(je, "date", date_val, date_try=True)
                _set_attr_if_exists(je, "description", description)
                _set_attr_if_exists(je, "amount", amount, cast_float=True)
                
                if debit_account.isdigit():
                    _set_attr_if_exists(je, "debit_account_id", int(debit_account))
                if credit_account.isdigit():
                    _set_attr_if_exists(je, "credit_account_id", int(credit_account))

                db.session.add(je)
                db.session.commit()
                flash('Journal entry recorded.', 'success')
                return redirect(url_for('accounting_overview'))
        except Exception:
            db.session.rollback()
            app.logger.exception("Failed to add journal entry")
            flash('Failed to add journal entry.', 'error')

    # Load accounts for dropdowns
    accounts = []
    if 'Account' in globals():
        try:
            accounts = db.session.query(Account).all()
        except:
            pass

    return render_template('add_journal_entry.html', accounts=accounts)

@app.route('/add_invoice', methods=['GET', 'POST'])
@login_required
def add_invoice():
    """
    Manually create an invoice.
    Redirects to accounting_overview.
    """
    if request.method == 'POST':
        customer_id = request.form.get('customer_id', '').strip()
        date_val = request.form.get('date', '').strip()
        due_date = request.form.get('due_date', '').strip()
        amount = request.form.get('amount', '').strip()
        status = request.form.get('status', 'Unpaid').strip()

        try:
            if 'Invoice' in globals():
                inv = Invoice()
                if customer_id.isdigit():
                    _set_attr_if_exists(inv, "customer_id", int(customer_id))
                _set_attr_if_exists(inv, "date", date_val, date_try=True)
                _set_attr_if_exists(inv, "due_date", due_date, date_try=True)
                _set_attr_if_exists(inv, "amount", amount, cast_float=True)
                _set_attr_if_exists(inv, "total", amount, cast_float=True)
                _set_attr_if_exists(inv, "status", status)
                
                db.session.add(inv)
                db.session.commit()
                flash('Invoice created successfully!', 'success')
                return redirect(url_for('accounting_overview'))
        except Exception:
            db.session.rollback()
            app.logger.exception("Failed to add invoice")
            flash('Failed to add invoice.', 'error')

    # Load customers
    customers = []
    if 'Customer' in globals():
        try:
            customers = db.session.query(Customer).all()
        except:
            pass

    return render_template('add_invoice.html', customers=customers)

@app.route("/accounting-overview")
@login_required
def accounting_overview():
    """
    Load accounting overview from PostgreSQL tables when available.
    Falls back to the existing file-based finance loader on error.
    """
    def safe_get(row, *candidates, default=None):
        for c in candidates:
            if hasattr(row, c):
                try:
                    val = getattr(row, c)
                    if hasattr(val, "isoformat"):
                        return val.isoformat()
                    return val
                except Exception:
                    continue
        return default

    try:
        accounts = []
        invoices = []
        payments = []
        expenses = []
        journal_entries = []
        outstanding_payments = []

        # Accounts
        if 'Account' in globals():
            acc_rows = db.session.query(Account).order_by(getattr(Account, "id", Account)).all()
            for a in acc_rows:
                accounts.append({
                    "id": getattr(a, "id", None),
                    "name": getattr(a, "name", "") or "",
                    "type": getattr(a, "type", "") or "",
                    "balance": float(getattr(a, "balance", 0) or 0),
                    "currency": getattr(a, "currency", "") or "",
                    "status": getattr(a, "status", "") or ""
                })

        # Invoices
        if 'Invoice' in globals():
            inv_rows = db.session.query(Invoice).order_by(getattr(Invoice, "invoice_id", Invoice)).all()
            for inv in inv_rows:
                amount = safe_get(inv, "amount", "total", default=0) or 0
                invoices.append({
                    "id": safe_get(inv, "invoice_id", "id"),
                    "customer_id": safe_get(inv, "customer_id", "customer"),
                    "date": safe_get(inv, "date", "created_at"),
                    "due_date": safe_get(inv, "due_date"),
                    "amount": float(amount),
                    "currency": safe_get(inv, "currency", "ccy", default=""),
                    "status": safe_get(inv, "status", "state", default="")
                })

        # Payments (Payment model is imported at top if available)
        if 'Payment' in globals():
            pay_rows = db.session.query(Payment).order_by(getattr(Payment, "id", Payment)).all()
            for p in pay_rows:
                payments.append({
                    "id": getattr(p, "id", None),
                    "party": getattr(p, "party", None),
                    "invoice_id": getattr(p, "invoice_id", None) or getattr(p, "invoice", None),
                    "date": getattr(p, "due_date", None) or getattr(p, "created_at", None),
                    "amount": float(getattr(p, "amount", 0) or 0),
                    "method": getattr(p, "method", "") or "",
                    "status": getattr(p, "status", "") or ""
                })

        # Outstanding Payments (Accounts Payable) - Added for Accounting Module
        if 'OutstandingPayment' in globals():
            op_rows = db.session.query(OutstandingPayment).order_by(getattr(OutstandingPayment, "due_date", OutstandingPayment)).all()
            for r in op_rows:
                outstanding_payments.append({
                    "payment_id": safe_get(r, "payment_id", "id"),
                    "party": safe_get(r, "party", "payee", ""),
                    "due_date": safe_get(r, "due_date", "date"),
                    "amount": float(safe_get(r, "amount", default=0) or 0),
                    "status": safe_get(r, "status", "")
                })

        # Expenses
        if 'Expense' in globals():
            exp_rows = db.session.query(Expense).order_by(getattr(Expense, "id", Expense)).all()
            for e in exp_rows:
                expenses.append({
                    "id": getattr(e, "id", None),
                    "account_id": getattr(e, "account_id", None),
                    "date": getattr(e, "date", None),
                    "description": getattr(e, "description", "") or "",
                    "amount": float(getattr(e, "amount", 0) or 0),
                    "category": getattr(e, "category", "") or "",
                    "status": getattr(e, "status", "") or ""
                })

        # Journal entries
        if 'JournalEntry' in globals():
            je_rows = db.session.query(JournalEntry).order_by(getattr(JournalEntry, "entry_id", JournalEntry)).all()
            for j in je_rows:
                journal_entries.append({
                    "id": safe_get(j, "entry_id", "id"),
                    "date": safe_get(j, "date"),
                    "description": safe_get(j, "description"),
                    "debit_account_id": safe_get(j, "debit_account_id", "debit_account"),
                    "credit_account_id": safe_get(j, "credit_account_id", "credit_account"),
                    "amount": float(safe_get(j, "amount", default=0) or 0)
                })

        # If no DB data found, fall through to file fallback
        any_db_data = any([accounts, invoices, payments, expenses, journal_entries, outstanding_payments])
        if not any_db_data:
            raise RuntimeError("No accounting models present in globals() or no rows returned")

        # Compute simple summary metrics
        total_revenue = sum(inv.get("amount", 0) for inv in invoices)
        total_expenses = sum(exp.get("amount", 0) for exp in expenses)
        outstanding_invoices_amount = sum(inv.get("amount", 0) for inv in invoices if (inv.get("status", "").lower() != "paid"))
        outstanding_invoices_count = sum(1 for inv in invoices if (inv.get("status", "").lower() != "paid"))

        # Build chart placeholders: cashflow from payments by month
        cashflow_by_month = {}
        for p in payments:
            d = p.get("date") or ""
            month = d[:7] if isinstance(d, str) and len(d) >= 7 else "unknown"
            cashflow_by_month[month] = cashflow_by_month.get(month, 0) + float(p.get("amount", 0))

        cashflow_data = {
            "data": [
                {"x": list(cashflow_by_month.keys()), "y": list(cashflow_by_month.values()), "type": "bar", "name": "Cash Flow"}
            ],
            "layout": {"title": "Cash Flow by Period"}
        }

        # Expense breakdown by category
        expense_by_cat = {}
        for e in expenses:
            cat = e.get("category") or "Uncategorized"
            expense_by_cat[cat] = expense_by_cat.get(cat, 0) + float(e.get("amount", 0))

        expense_breakdown_data = {
            "data": [{"labels": list(expense_by_cat.keys()), "values": list(expense_by_cat.values()), "type": "pie", "name": "Expenses"}],
            "layout": {"title": "Expense Breakdown"}
        }

        summary = [
            {"label": "Total Revenue", "value": f"{total_revenue:.2f}"},
            {"label": "Total Expenses", "value": f"{total_expenses:.2f}"},
            {"label": "Outstanding Invoices", "value": f"{outstanding_invoices_amount:.2f}"},
            {"label": "Outstanding Count", "value": outstanding_invoices_count}
        ]

        recent_activity = [f"Payment {p.get('id')} {p.get('status')} {p.get('amount')}" for p in payments[:5]]
        top_vendors = []  # would require supplier/payee model joins; leave empty if not available
        recent_transactions = payments[:5] if payments else []

        return render_template(
            "accounting-overview.html",
            summary=summary,
            alerts=[],
            recent_activity=recent_activity,
            top_vendors=top_vendors,
            recent_transactions=recent_transactions,
            cashflow_data=cashflow_data,
            expense_breakdown_data=expense_breakdown_data,
            revenue_sources_data={"data": [], "layout": {}},
            outstanding_invoices_data={"data": [], "layout": {}},
            outstanding_payments=outstanding_payments,
            payments=payments,
            invoices=invoices
        )

    except Exception:
        app.logger.exception("DB unavailable or accounting models missing; falling back to file-based finance data")


@app.route("/assets/edit/<int:asset_id>", methods=["GET", "POST"])
@login_required
def edit_asset(asset_id):
    # Support both GET (render form) and POST (apply DB-backed update)
    row = db.session.get(Asset, asset_id)

    if request.method == "POST":
        # read form values
        name = request.form.get("name")
        category = request.form.get("category")
        purchase_date = request.form.get("purchase_date")
        value = request.form.get("value")
        depreciation_rate = request.form.get("depreciation_rate")
        status = request.form.get("status")

        if row is None:
            flash("Asset not found.", "error")
            return redirect(url_for("asset_overview"))

        if not name or not category or not purchase_date or not value:
            flash("Please fill in all required fields.", "error")
            return redirect(url_for("edit_asset", asset_id=asset_id))

        try:
            before = row.to_dict() if hasattr(row, "to_dict") else None
            _set_attr_if_exists(row, "name", name)
            _set_attr_if_exists(row, "category", category)
            _set_attr_if_exists(row, "purchase_date", purchase_date, date_try=True)
            _set_attr_if_exists(row, "value", value, cast_float=True)
            _set_attr_if_exists(row, "depreciation_rate", depreciation_rate, cast_float=True)
            _set_attr_if_exists(row, "status", status)

            db.session.commit()

            try:
                log_audit(action="update", resource_type="Asset", resource_id=asset_id, before=before, after=row)
            except Exception:
                app.logger.debug("Audit log failed for edit_asset", exc_info=True)

            flash(f"Asset '{getattr(row, 'name', asset_id)}' updated successfully!", "success")
        except Exception:
            db.session.rollback()
            app.logger.exception("Failed to update asset in DB")
            flash("Failed to update asset (database error).", "error")

        return redirect(url_for("asset_overview"))

    # GET: render template using DB row if available, otherwise fall back to in-memory dict
    if row is not None:
        asset_dict = {
            "id": getattr(row, "id", None),
            "name": getattr(row, "name", "") or "",
            "category": getattr(row, "category", "") or "",
            "purchase_date": (getattr(row, "purchase_date", "") or "") if not hasattr(getattr(row, "purchase_date", None), "isoformat") else getattr(row, "purchase_date").isoformat(),
            "value": float(getattr(row, "value", 0) or 0),
            "depreciation_rate": float(getattr(row, "depreciation_rate", 0) or 0),
            "status": getattr(row, "status", "") or ""
        }
    else:
        asset_dict = next((a for a in assets_data if a["id"] == asset_id), None)
        if not asset_dict:
            flash("Asset not found.", "error")
            return redirect(url_for("asset_overview"))

    return render_template("edit-asset.html", asset=asset_dict)

@app.route("/assets/delete/<int:asset_id>", methods=["POST"])
@login_required
def delete_asset(asset_id):
   
    try:
        row = db.session.get(Asset, asset_id)
        if row is None:
            flash("Asset not found.", "error")
            return redirect(url_for("asset_overview"))
        before = row.to_dict() if hasattr(row, "to_dict") else None
        db.session.delete(row)
        db.session.commit()

        try:
            log_audit(action="delete", resource_type="Asset", resource_id=asset_id, before=before, after=None)
        except Exception:
            app.logger.debug("Audit log failed for delete_asset", exc_info=True)
        flash(f"Asset '{getattr(row, 'name', asset_id)}' deleted successfully!", "success")
    
    except Exception:
        db.session.rollback()
        app.logger.exception("Failed to delete asset from DB")
        flash("Failed to delete asset (database error).", "error")
    
    return redirect(url_for("asset_overview"))

@app.route("/assets/add", methods=["GET", "POST"])
@login_required
def add_asset():
   
    if request.method == "POST":
        name = request.form.get("name")
        category = request.form.get("category")
        purchase_date = request.form.get("purchase_date")
        value = request.form.get("value")
        depreciation_rate = request.form.get("depreciation_rate")
        status = request.form.get("status")

        # Basic validation
        if not name or not category or not purchase_date or not value:
            flash("Please fill in all required fields.", "error")
            return redirect(url_for("add_asset"))

        a = Asset()
        _set_attr_if_exists(a, "name", name)
        _set_attr_if_exists(a, "category", category)
        _set_attr_if_exists(a, "purchase_date", purchase_date, date_try=True)
        _set_attr_if_exists(a, "value", value, cast_float=True)
        _set_attr_if_exists(a, "depreciation_rate", depreciation_rate, cast_float=True)
        _set_attr_if_exists(a, "status", status)

        db.session.add(a)
        try:
            db.session.commit()
            log_audit(action="create", resource_type="Asset", resource_id=getattr(a, "id", None), after=a)
            flash(f"Asset '{name}' added successfully!", "success")
            return redirect(url_for("asset_overview"))
        except Exception:
            db.session.rollback()
            app.logger.exception("Failed to add Asset to DB")
            flash("Failed to save asset to database.", "error")
            return redirect(url_for("add_asset"))

    # If GET: render the form
    return render_template("add_asset.html")

@app.route("/assets-overview", methods=["GET"])
@login_required
def asset_overview():
    # Get search and filter parameters from URL
    query = request.args.get("query", "").strip()
    category = request.args.get("category", "")
    status = request.args.get("status", "")

    try:
        # Query Postgres via SQLAlchemy Asset model
        q = db.session.query(Asset)

        if query:
            q = q.filter(Asset.name.ilike(f"%{query}%"))

        if category:
            q = q.filter(Asset.category == category)

        if status:
            q = q.filter(Asset.status == status)

        assets_rows = q.order_by(Asset.id).all()

        # Convert model objects to dicts to match existing template expectations
        def asset_to_dict(a):
            purchase_date = getattr(a, "purchase_date", None)
            if hasattr(purchase_date, "isoformat"):
                purchase_date = purchase_date.isoformat()
            return {
                "id": getattr(a, "id", None),
                "name": getattr(a, "name", "") or "",
                "category": getattr(a, "category", "") or "",
                "purchase_date": purchase_date or "",
                "value": float(getattr(a, "value", 0) or 0),
                "depreciation_rate": float(getattr(a, "depreciation_rate", 0) or 0),
                "status": getattr(a, "status", "") or ""
            }

       
        assets = [asset_to_dict(a) for a in assets_rows]
        categories = sorted({a["category"] for a in assets if a["category"]})

        # audit: record that current user viewed the assets list (include filters)
        try:
            log_audit(
                action="view",
                resource_type="Asset",
                resource_id=None,
                after={"query": query, "category": category, "status": status},
                meta={"count": len(assets)}
            )
        except Exception:
            app.logger.debug("Asset list audit logging failed", exc_info=True)

    except Exception as exc:
        app.logger.exception("Failed to load assets from DB, falling back to in-memory list")
        flash("Could not load assets from database. Showing in-memory data.", "warning")
        # fallback to previous in-memory list (assets_data)
        assets = assets_data
        categories = sorted(list(set([a["category"] for a in assets])))

        # build Plotly pie chart JSON for asset categories
    try:
        # category chart
        cat_counts = {}
        status_counts = {}
        for a in assets:
            cat = a.get("category") or "Uncategorized"
            cat_counts[cat] = cat_counts.get(cat, 0) + 1
            st = a.get("status") or "Unknown"
            status_counts[st] = status_counts.get(st, 0) + 1

        labels_cat = list(cat_counts.keys())
        vals_cat = [cat_counts[k] for k in labels_cat]

        labels_status = list(status_counts.keys())
        vals_status = [status_counts[k] for k in labels_status]

        asset_chart = {
            "data": [
                {"labels": labels_cat, "values": vals_cat, "type": "pie", "name": "Asset Categories"}
            ],
            "layout": {"title": "Asset Category Distribution", "height": 380}
        }
        asset_status_chart = {
            "data": [
                {"labels": labels_status, "values": vals_status, "type": "pie", "name": "Asset Statuses"}
            ],
            "layout": {"title": "Asset Status Distribution", "height": 380}
        }

        asset_chart_data = json.dumps(asset_chart, cls=PlotlyJSONEncoder)
        asset_status_chart_data = json.dumps(asset_status_chart, cls=PlotlyJSONEncoder)
    except Exception:
        app.logger.exception("Failed to build asset charts")
        asset_chart_data = json.dumps({"data": [], "layout": {}})
        asset_status_chart_data = json.dumps({"data": [], "layout": {}})

    return render_template(
        "assets-overview.html",
        assets=assets,
        categories=categories,
        query=query,
        selected_category=category,
        selected_status=status,
        asset_chart_data=asset_chart_data,
        asset_status_chart_data=asset_status_chart_data
    )

@app.route('/detailed-assets-analysis')
def detailed_sales_analysis():
    return render_template('detailed-assets-analysis.html')

@app.route('/assets/add-barcode', methods = ['GET'])
def add_barcode():
    return Response (asset_upload_module.barcode_scanner(), mimetype = "multipart/x-mixed-replace; boundary=frame")

BARCODE_DIR = os.path.join('static', 'barcodes')
os.makedirs(BARCODE_DIR, exist_ok=True)

@app.route('/generate-barcode', methods=['POST'])
def generate_barcode():
    asset_id = request.json.get('asset_id')
    if not asset_id:
        return jsonify({'error': 'No asset_id provided'}), 400

    barcode_path = os.path.join(BARCODE_DIR, f"{asset_id}.png")
    code128 = barcode.get('code128', asset_id, writer=ImageWriter())
    code128.save(barcode_path[:-4])

    return jsonify({'barcode_url': f"/static/barcodes/{asset_id}.png"})

@app.route('/add_customer', methods=['GET', 'POST'])
def add_customer():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        contact_person = request.form.get('contact_person', '').strip()
        email = request.form.get('email', '').strip()
        phone = request.form.get('phone', '').strip()
        status = request.form.get('status', 'Active').strip()

        # Try to insert into DB; fall back to file storage if DB unavailable
        try:
            cust = Customer()
            if hasattr(cust, "name"):
                setattr(cust, "name", name)
            if hasattr(cust, "contact_person"):
                setattr(cust, "contact_person", contact_person)
            if hasattr(cust, "email"):
                setattr(cust, "email", email)
            if hasattr(cust, "phone"):
                setattr(cust, "phone", phone)
            if hasattr(cust, "status"):
                setattr(cust, "status", status)

            # set upload timestamp on first matching possible field
            for ts_field in ("uploaded_at", "created_at", "timestamp", "created_on", "uploaded_on", "created"):
                if hasattr(cust, ts_field):
                    setattr(cust, ts_field, datetime.utcnow())
                    break

            db.session.add(cust)
            db.session.commit()
            flash('Customer added successfully!', 'success')
            return redirect(url_for('customer_overview'))
        except Exception:
            # DB failed - fallback to JSON file as before
            db.session.rollback()
            app.logger.exception("DB add_customer failed")

            flash('Customer not added.', 'danger')
            return redirect(url_for('customer_overview'))

    return render_template('add_customer.html')


@app.route('/edit_customer/<int:customer_id>', methods=['GET', 'POST'])
def edit_customer(customer_id):
    """
    Edit a customer. Prefer updating the PostgreSQL Customer table; fall back to file-based storage.
    """
    # Try DB first
    try:
        cust = db.session.get(Customer, customer_id)
    except Exception:
        app.logger.exception("DB lookup failed for edit_customer")
        cust = None

    # If we found a DB row, operate on it
    if cust:
        if request.method == 'POST':
            # Read form values
            name = request.form.get('name', '').strip()
            contact_person = request.form.get('contact_person', '').strip()
            email = request.form.get('email', '').strip()
            phone = request.form.get('phone', '').strip()
            status = request.form.get('status', '').strip()

            try:
                if hasattr(cust, "name"):
                    setattr(cust, "name", name)
                if hasattr(cust, "contact_person"):
                    setattr(cust, "contact_person", contact_person)
                if hasattr(cust, "email"):
                    setattr(cust, "email", email)
                if hasattr(cust, "phone"):
                    setattr(cust, "phone", phone)
                if hasattr(cust, "status"):
                    setattr(cust, "status", status)

                # update "uploaded_at"/timestamp field if present (do not overwrite if empty)
                for ts_field in ("uploaded_at", "created_at", "timestamp", "created_on", "uploaded_on", "created"):
                    if hasattr(cust, ts_field):
                        try:
                            setattr(cust, ts_field, datetime.utcnow())
                        except Exception:
                            # ignore timestamp set errors
                            app.logger.debug("Could not set timestamp field %s on Customer", ts_field)
                        break

                db.session.add(cust)
                db.session.commit()
                flash('Customer updated successfully!', 'success')
                return redirect(url_for('customer_overview'))
            except Exception:
                db.session.rollback()
                app.logger.exception("Failed to update customer in DB")
                flash('Failed to update customer (database error).', 'danger')
                return redirect(url_for('customer_overview'))

        # GET: prepare a simple dict for the template (templates expect dict)
        customer_dict = {
            "id": getattr(cust, "id", None),
            "name": getattr(cust, "name", "") or "",
            "contact_person": getattr(cust, "contact_person", "") or "",
            "email": getattr(cust, "email", "") or "",
            "phone": getattr(cust, "phone", "") or "",
            "status": getattr(cust, "status", "") or ""
        }
        return render_template('edit_customer.html', customer=customer_dict)

@app.route('/delete_customer/<int:customer_id>', methods=['POST'])
@login_required
def delete_customer(customer_id):
    """
    Delete a customer. Prefer deleting from PostgreSQL Customer table; fall back to file-based storage.
    """
    # Try DB first
    try:
        cust = db.session.get(Customer, customer_id)
    except Exception:
        app.logger.exception("DB lookup failed for delete_customer")
        cust = None

    if cust:
        try:
            db.session.delete(cust)
            db.session.commit()
            flash('Customer deleted successfully!', 'success')
            return redirect(url_for('customer_overview'))
        except Exception:
            db.session.rollback()
            app.logger.exception("Failed to delete customer from DB")
            flash('Failed to delete customer (database error).', 'danger')
            return redirect(url_for('customer_overview'))

    return redirect(url_for('customer_overview'))

#CHURN ANALYSIS

from datetime import timedelta

# Churn analysis UI route
@app.route('/churn-analysis', methods=['GET'])
@login_required
def churn_analysis():
    """Render churn analysis page."""
    return render_template('churn-analysis.html')

# API endpoint used by churn-analysis.html to run analysis
@app.route('/api/churn', methods=['POST'])
@login_required
def api_churn():
    """
    Lightweight churn analysis using Customer + SalesOrder/Invoice tables.
    Accepts form fields: date_from, date_to, min_orders, churn_definition (days), method.
    Returns JSON: churn_rate, retention_rate, cohort, feature_importance, high_risk_customers.
    """
    try:
        # parse inputs
        date_from_raw = request.form.get('date_from', '').strip()
        date_to_raw = request.form.get('date_to', '').strip()
        min_orders = int(request.form.get('min_orders', 1) or 1)
        churn_days = int(request.form.get('churn_definition', 30) or 30)
        method = request.form.get('method', 'cohort')

        try:
            date_to = datetime.fromisoformat(date_to_raw).date() if date_to_raw else datetime.utcnow().date()
        except Exception:
            date_to = datetime.utcnow().date()
        try:
            date_from = datetime.fromisoformat(date_from_raw).date() if date_from_raw else (date_to - timedelta(days=365))
        except Exception:
            date_from = date_to - timedelta(days=365)

        # load customers
        try:
            cust_rows = db.session.query(Customer).all()
        except Exception:
            app.logger.exception("Failed to load customers for churn")
            return jsonify(ok=False, error="Failed to load customers"), 500

        customers = [{"id": getattr(c, "id", None), "name": getattr(c, "name", "") or str(getattr(c, "id", ""))} for c in cust_rows]
        total_customers = len(customers)
        if total_customers == 0:
            return jsonify(ok=True, churn_rate=0.0, retention_rate=1.0, cohort={"x": [], "y": []}, feature_importance=[], high_risk_customers=[])

        # pick order/invoice model
        order_model = None
        order_customer_field = None
        order_date_field = None
        order_amount_field = None
        if 'SalesOrder' in globals():
            order_model = SalesOrder
            order_customer_field = getattr(SalesOrder, "customer_id", None) or getattr(SalesOrder, "customer", None)
            order_date_field = getattr(SalesOrder, "order_date", None) or getattr(SalesOrder, "date", None) or getattr(SalesOrder, "created_at", None)
            order_amount_field = getattr(SalesOrder, "total", None) or getattr(SalesOrder, "amount", None)
        elif 'Invoice' in globals():
            order_model = Invoice
            order_customer_field = getattr(Invoice, "customer_id", None) or getattr(Invoice, "customer", None)
            order_date_field = getattr(Invoice, "date", None) or getattr(Invoice, "created_at", None)
            order_amount_field = getattr(Invoice, "amount", None) or getattr(Invoice, "total", None)

        # prepare activity dict
        activity = {c["id"]: {"customer_id": c["id"], "customer_name": c["name"], "total_orders": 0, "total_amount": 0.0, "first_order": None, "last_order": None, "orders_in_range": 0, "orders_recent_window": 0} for c in customers}

        # query orders/invoices if available
        if order_model and order_customer_field is not None and order_date_field is not None:
            try:
                rows = (
                    db.session.query(order_customer_field.label("customer_id"),
                                     order_date_field.label("date"),
                                     (order_amount_field if order_amount_field is not None else text("0")).label("amount"))
                    .filter(order_date_field <= date_to)
                    .filter(order_date_field >= (date_from - timedelta(days=365)))
                    .all()
                )
                for r in rows:
                    cid = getattr(r, "customer_id", None)
                    if cid not in activity:
                        continue
                    d = getattr(r, "date", None)
                    if hasattr(d, "date"):
                        d = d.date()
                    amt = getattr(r, "amount", 0) or 0
                    rec = activity[cid]
                    rec["total_orders"] += 1
                    try:
                        rec["total_amount"] += float(amt)
                    except Exception:
                        pass
                    if rec["first_order"] is None or (d and d < rec["first_order"]):
                        rec["first_order"] = d
                    if rec["last_order"] is None or (d and d > rec["last_order"]):
                        rec["last_order"] = d
                    if d and (date_from <= d <= date_to):
                        rec["orders_in_range"] = rec.get("orders_in_range", 0) + 1
                    if d and ((date_to - d).days <= 90):
                        rec["orders_recent_window"] = rec.get("orders_recent_window", 0) + 1
            except Exception:
                app.logger.exception("Failed to query orders for churn")

        # label churned and compute risk
        churn_threshold_date = date_to - timedelta(days=churn_days)
        churned_count = 0
        high_risk = []
        cohort_rows = []
        for cid, rec in activity.items():
            last = rec.get("last_order")
            if last is None:
                rec["recency_days"] = None
                rec["churned"] = True
            else:
                recency = (date_to - last).days
                rec["recency_days"] = recency
                rec["churned"] = (last < churn_threshold_date)
            if rec["churned"]:
                churned_count += 1
            recency_days = rec.get("recency_days") if rec.get("recency_days") is not None else 3650
            freq_recent = rec.get("orders_recent_window", 0)
            risk_score = (recency_days + 1) / (1 + freq_recent)
            high_risk.append({"customer_id": cid, "customer_name": rec.get("customer_name"), "risk_score": float(risk_score), "last_activity": rec.get("last_order").isoformat() if rec.get("last_order") else None})
            if rec.get("first_order"):
                cohort_rows.append({"customer_id": cid, "cohort_month": rec["first_order"].strftime("%Y-%m"), "last_month": (rec["last_order"].strftime("%Y-%m") if rec.get("last_order") else None)})

        churn_rate = float(churned_count) / float(total_customers) if total_customers else 0.0
        retention_rate = 1.0 - churn_rate
        high_risk_sorted = sorted(high_risk, key=lambda x: x["risk_score"], reverse=True)[:50]

        # cohort matrix (best-effort with pandas)
        cohort_result = {"x": [], "y": []}
        try:
            import pandas as pd
            if cohort_rows:
                df_cohort = pd.DataFrame(cohort_rows)
                months = pd.period_range(start=min(df_cohort["cohort_month"]), end=date_to.strftime("%Y-%m"), freq='M').astype(str).tolist()
                matrix = []
                cohort_groups = df_cohort.groupby("cohort_month")["customer_id"].nunique().to_dict()
                for cm in sorted(df_cohort["cohort_month"].unique()):
                    row_counts = []
                    for m in months:
                        count_retained = df_cohort[(df_cohort["cohort_month"] == cm) & (df_cohort["last_month"].notnull()) & (df_cohort["last_month"] >= m)]["customer_id"].nunique()
                        total_cohort = cohort_groups.get(cm, 1)
                        frac = float(count_retained) / float(total_cohort) if total_cohort else 0.0
                        row_counts.append(round(frac, 4))
                    matrix.append(row_counts)
                cohort_result = {"x": months, "y": matrix}
        except Exception:
            cohort_result = {"x": [], "y": []}

        # feature importance (best-effort)
        feature_importance = []
        try:
            from sklearn.ensemble import RandomForestClassifier
            from sklearn.model_selection import train_test_split
            import numpy as np
            rows = []
            for cid, rec in activity.items():
                rows.append({"customer_id": cid, "total_orders": rec.get("total_orders", 0), "total_amount": rec.get("total_amount", 0.0), "orders_recent_90": rec.get("orders_recent_window", 0), "recency_days": rec.get("recency_days") if rec.get("recency_days") is not None else 3650, "churned": 1 if rec.get("churned") else 0})
            df = pd.DataFrame(rows)
            if len(df) >= 20 and df['churned'].nunique() > 1:
                X = df[["total_orders", "total_amount", "orders_recent_90", "recency_days"]].fillna(0)
                y = df["churned"]
                X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42, stratify=y)
                clf = RandomForestClassifier(n_estimators=100, random_state=42)
                clf.fit(X_train, y_train)
                importances = clf.feature_importances_
                feat_names = X.columns.tolist()
                feature_importance = [{"name": n, "score": float(s)} for n, s in sorted(zip(feat_names, importances), key=lambda x: x[1], reverse=True)]
        except Exception:
            feature_importance = []

        return jsonify(ok=True, churn_rate=churn_rate, retention_rate=retention_rate, cohort=cohort_result, feature_importance=feature_importance, high_risk_customers=high_risk_sorted)

    except Exception:
        app.logger.exception("Churn analysis failed")
        return jsonify(ok=False, error="Server error during churn analysis"), 500


@app.route('/customer-overview')
@login_required
def customer_overview():
    """
    Load customers from the database (Customer model). Falls back to file-based loader on error.
    Provides customer_summary and customer_chart_data for the template.
    """
    query = request.args.get('query', '').strip()
    selected_status = request.args.get('status', '')

    try:
        q = db.session.query(Customer)

        if query:
            q = q.filter(Customer.name.ilike(f"%{query}%"))

        if selected_status:
            q = q.filter(Customer.status == selected_status)

        rows = q.order_by(getattr(Customer, "id", Customer)).all()

        customers = []
        for c in rows:
            customers.append({
                "id": getattr(c, "id", None),
                "name": getattr(c, "name", "") or "",
                "contact_person": getattr(c, "contact_person", "") or "",
                "email": getattr(c, "email", "") or "",
                "phone": getattr(c, "phone", "") or "",
                "status": getattr(c, "status", "") or ""
            })

    except Exception:
        app.logger.exception("Failed to load customers from DB")

    # Summary metrics
    total = len(customers)
    active_count = sum(1 for c in customers if (c.get("status") or c.get("status", "")) == "Active")
    inactive_count = sum(1 for c in customers if (c.get("status") or c.get("status", "")) == "Inactive")

    customer_summary = [
        {"label": "Total Customers", "value": total},
        {"label": "Active Customers", "value": active_count},
        {"label": "Inactive Customers", "value": inactive_count}
    ]

    customer_chart_data = {
        "data": [
            {
                "labels": ["Active", "Inactive"],
                "values": [active_count, inactive_count],
                "type": "pie",
                "name": "Customer Status"
            }
        ],
        "layout": {"title": ""}
    }

    return render_template(
        'customer-overview.html',
        customers=customers,
        query=query,
        selected_status=selected_status,
        customer_summary=customer_summary,
        customer_chart_data=customer_chart_data
    )

@app.route('/customers/upload', methods=['POST'])
@login_required
def upload_customers():
    """
    Accept a CSV file with headers: Id,name,contact_person,email,phone,status
    Insert rows into the Customer table. Skip rows that fail with a warning.
    """
    file = request.files.get('csv_file')
    if not file:
        flash("No file uploaded.", "error")
        return redirect(url_for('customer_overview'))

    try:
        content = file.read().decode('utf-8-sig')
        reader = csv.DictReader(content.splitlines())
    except Exception:
        flash("Failed to read CSV file.", "error")
        app.logger.exception("CSV read error")
        return redirect(url_for('customer_overview'))

    inserted = 0
    skipped = 0
    for row in reader:
        try:
            # Map CSV columns safely
            name = (row.get('name') or row.get('Name') or '').strip()
            contact_person = (row.get('contact_person') or row.get('contactPerson') or row.get('Contact Person') or '').strip()
            email = (row.get('email') or row.get('Email') or '').strip()
            phone = (row.get('phone') or row.get('Phone') or '').strip()
            status = (row.get('status') or row.get('Status') or 'Active').strip()

            if not name:
                skipped += 1
                continue

            cust = Customer()
            if hasattr(cust, "name"):
                setattr(cust, "name", name)
            if hasattr(cust, "contact_person"):
                setattr(cust, "contact_person", contact_person)
            if hasattr(cust, "email"):
                setattr(cust, "email", email)
            if hasattr(cust, "phone"):
                setattr(cust, "phone", phone)
            if hasattr(cust, "status"):
                setattr(cust, "status", status)

            # set upload timestamp (UTC) on the first matching timestamp field
            for ts_field in ("uploaded_at", "created_at", "timestamp", "created_on", "uploaded_on", "created"):
                if hasattr(cust, ts_field):
                    setattr(cust, ts_field, datetime.utcnow())
                    break

            db.session.add(cust)
            db.session.flush()  # allow catching DB errors early
            inserted += 1
        except Exception:
            db.session.rollback()
            skipped += 1
            app.logger.exception("Failed to insert customer row, skipping")

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        flash("Failed to save uploaded customers to database.", "error")
        app.logger.exception("Commit failed for uploaded customers")
        return redirect(url_for('customer_overview'))

    flash(f"Customers uploaded: {inserted}. Skipped: {skipped}.", "success")
    return redirect(url_for('customer_overview'))

@app.route('/customers/download-sample')
@login_required
def download_customers_sample():
    """
    Return a small CSV sample for customers upload
    """
    sample = (
        "Id,name,contact_person,email,phone,status\r\n"
        "1,Acme Trading,Chileshe Mwansa,chileshe.mwansa@acmetrading.co.zm,+260971234567,Active\r\n"
        "2,Zambia Supplies,Mwila Banda,mwila.banda@zambiasupplies.co.zm,+260967890123,Active\r\n"
    )
    return Response(sample, mimetype='text/csv',
                    headers={"Content-Disposition": "attachment;filename=customers_sample.csv"})

def _set_attr_if_exists(obj, field, value, date_try=False, cast_float=False):
    """Helper: set attribute on obj if present. Try to convert date or numeric if requested."""
    if not hasattr(obj, field):
        return
    val = value
    if date_try and value:
        try:
            # expect 'YYYY-MM-DD' from forms
            val = datetime.fromisoformat(value)
        except Exception:
            try:
                val = datetime.strptime(value, "%Y-%m-%d")
            except Exception:
                pass
    if cast_float and value not in (None, ""):
        try:
            val = float(value)
        except Exception:
            pass
    try:
        setattr(obj, field, val)
    except Exception:
        # ignore individual set errors
        app.logger.debug("Could not set %s on %s", field, type(obj).__name__)

@app.route('/distribution-overview')
def distribution_overview():
    try:
        inv_rows = db.session.query(InventoryItem).order_by(getattr(InventoryItem, "id", InventoryItem)).all()
        shipments_rows = db.session.query(Shipment).order_by(getattr(Shipment, "id", Shipment)).all()
        orders_rows = db.session.query(SalesOrder).order_by(getattr(SalesOrder, "id", SalesOrder)).all()

        def row_to_dict(row, fields):
            out = {}
            for f in fields:
                try:
                    val = getattr(row, f)
                    # serialize datetimes
                    if hasattr(val, "isoformat"):
                        val = val.isoformat()
                    out[f] = val
                except Exception:
                    out[f] = None
            return out

        inventory = []
        for r in inv_rows:
            inventory.append(row_to_dict(r, [
                getattr(InventoryItem, "id").key if hasattr(InventoryItem, "id") else "id",
                "product" if hasattr(InventoryItem, "product") else ("name" if hasattr(InventoryItem, "name") else "item"),
                "sku" if hasattr(InventoryItem, "sku") else "code",
                "warehouse" if hasattr(InventoryItem, "warehouse") else "location",
                "quantity" if hasattr(InventoryItem, "quantity") else ("qty" if hasattr(InventoryItem, "qty") else "stock"),
                "reorder_level" if hasattr(InventoryItem, "reorder_level") else "reorder",
                "status" if hasattr(InventoryItem, "status") else "state"
            ]))

        shipments = []
        # If Customer model and a customer_id FK exist on Shipment, join and attach customer name
        if 'Customer' in globals() and hasattr(Shipment, "customer_id"):
            rows = (
                db.session.query(Shipment, Customer)
                .outerjoin(Customer, getattr(Shipment, "customer_id") == getattr(Customer, "id"))
                .order_by(getattr(Shipment, "id", Shipment))
                .all()
            )
            for sh, cust in rows:
                sh_dict = row_to_dict(sh, [
                    "id" if hasattr(Shipment, "id") else "",
                    "shipment_id" if hasattr(Shipment, "shipment_id") else ("id" if hasattr(Shipment, "id") else "shipment"),
                    "date" if hasattr(Shipment, "date") else ("shipped_at" if hasattr(Shipment, "shipped_at") else "created_at"),
                    "carrier" if hasattr(Shipment, "carrier") else "carrier_name",
                    "destination" if hasattr(Shipment, "destination") else "dest",
                    "status" if hasattr(Shipment, "status") else "state",
                ])
            
                if cust is not None:
                    sh_dict["customer"] = getattr(cust, "name", None) or getattr(cust, "customer_name", None) or getattr(cust, "full_name", None) or ""
                else:
                    sh_dict["customer"] = getattr(sh, "customer_name", "") or (getattr(sh, "customer", None) and getattr(sh.customer, "name", "")) or ""
                shipments.append(sh_dict)
        else:
            for s in shipments_rows:
                shipments.append(row_to_dict(s, [
                    "id" if hasattr(Shipment, "id") else "",
                    "shipment_id" if hasattr(Shipment, "shipment_id") else ("id" if hasattr(Shipment, "id") else "shipment"),
                    "date" if hasattr(Shipment, "date") else ("shipped_at" if hasattr(Shipment, "shipped_at") else "created_at"),
                    "carrier" if hasattr(Shipment, "carrier") else "carrier_name",
                    "destination" if hasattr(Shipment, "destination") else "dest",
                    "status" if hasattr(Shipment, "status") else "state",
                    "customer" if hasattr(Shipment, "customer") else ""
                ]))
            
        orders = []
        try:
            if 'Customer' in globals() and hasattr(SalesOrder, "customer_id"):
                rows = (
                    db.session.query(SalesOrder, Customer)
                    .outerjoin(Customer, getattr(SalesOrder, "customer_id") == getattr(Customer, "id"))
                    .order_by(getattr(SalesOrder, "id", SalesOrder))
                    .all()
                )
                for so, cust in rows:
                    od = row_to_dict(so, [
                        "order_id" if hasattr(SalesOrder, "order_id") else ("id" if hasattr(SalesOrder, "id") else "order"),
                        "customer_id",
                        "date" if hasattr(SalesOrder, "date") else ("order_date" if hasattr(SalesOrder, "order_date") else "created_at"),
                        "amount" if hasattr(SalesOrder, "amount") else ("total" if hasattr(SalesOrder, "total") else "value"),
                        "status" if hasattr(SalesOrder, "status") else "state"
                    ])
                    
                    if cust is not None:
                        od["customer_name"] = getattr(cust, "name", None) or getattr(cust, "customer_name", None) or getattr(cust, "full_name", None) or ""
                    else:
                        od["customer_name"] = ""
                    orders.append(od)
            else:
                # Fallback: no Customer model or no customer_id field — read orders only and try relationship if present
                orders_rows = db.session.query(SalesOrder).order_by(getattr(SalesOrder, "id", SalesOrder)).all()
                for o in orders_rows:
                    od = row_to_dict(o, [
                        "order_id" if hasattr(SalesOrder, "order_id") else ("id" if hasattr(SalesOrder, "id") else "order"),
                        "customer_id" if hasattr(SalesOrder, "customer_id") else ("customer" if hasattr(SalesOrder, "customer") else "customer_name"),
                        "date" if hasattr(SalesOrder, "date") else ("order_date" if hasattr(SalesOrder, "order_date") else "created_at"),
                        "amount" if hasattr(SalesOrder, "amount") else ("total" if hasattr(SalesOrder, "total") else "value"),
                        "status" if hasattr(SalesOrder, "status") else "state"
                    ])
                    # if SalesOrder has a relationship attribute 'customer', use it
                    cust_obj = getattr(o, "customer", None)
                    if cust_obj is not None:
                        od["customer_name"] = getattr(cust_obj, "name", None) or getattr(cust_obj, "customer_name", None) or ""
                    else:
                        od["customer_name"] = ""
                    orders.append(od)
        except Exception:
            app.logger.exception("Failed to load orders with customer join; falling back to simple orders list")
            orders = []
            orders_rows = db.session.query(SalesOrder).order_by(getattr(SalesOrder, "id", SalesOrder)).all()
            for o in orders_rows:
                od = row_to_dict(o, [
                    "order_id" if hasattr(SalesOrder, "order_id") else ("id" if hasattr(SalesOrder, "id") else "order"),
                    "customer_id" if hasattr(SalesOrder, "customer_id") else ("customer" if hasattr(SalesOrder, "customer") else "customer_name"),
                    "date" if hasattr(SalesOrder, "date") else ("order_date" if hasattr(SalesOrder, "order_date") else "created_at"),
                    "amount" if hasattr(SalesOrder, "amount") else ("total" if hasattr(SalesOrder, "total") else "value"),
                    "status" if hasattr(SalesOrder, "status") else "state"
                ])
                od["customer_name"] = getattr(o, "customer_name", "") or getattr(o, "customer", "") or ""
                orders.append(od)
        alerts = []
        
        # 1. Low Stock Alerts
        for item in inventory:
            try:
                qty = item['quantity']
                reorder = item['reorder_level']
                if qty < reorder:
                    alerts.append(f"Low Stock Alert: {item['product']} is below reorder level ({int(qty)} < {int(reorder)}).")
            except (ValueError, TypeError):
                continue

        # 2. Pending Shipments Notification
        pending_shipments_count = sum(1 for s in shipments if s.get('status') == 'Pending')
        if pending_shipments_count > 0:
            alerts.append(f"Action Required: {pending_shipments_count} shipment(s) are pending dispatch.")

        # 3. Outstanding Orders Notification
        pending_orders_count = sum(1 for o in orders if o.get('status') == 'Pending')
        if pending_orders_count > 0:
            alerts.append(f"Notification: {pending_orders_count} sales order(s) are pending processing.")
        
        # --- Build pie charts: shipment status distribution and order status distribution ---
        try:
            # Shipments status counts
            ship_status_counts = {}
            for s in shipments:
                st = (s.get("status") or s.get("state") or "Unknown")
                ship_status_counts[st] = ship_status_counts.get(st, 0) + 1
            ship_labels = list(ship_status_counts.keys())
            ship_vals = list(ship_status_counts.values())
            ship_chart = {
                "data": [{"labels": ship_labels, "values": ship_vals, "type": "pie", "name": "Shipment Status"}],
                "layout": {"title": "Shipment Status Distribution", "height": 380}
            }
            shipments_status_chart_data = json.dumps(ship_chart, cls=PlotlyJSONEncoder)

            # Orders status counts
            order_status_counts = {}
            for o in orders:
                st = (o.get("status") or o.get("state") or "Unknown")
                order_status_counts[st] = order_status_counts.get(st, 0) + 1
            order_labels = list(order_status_counts.keys())
            order_vals = list(order_status_counts.values())
            order_chart = {
                "data": [{"labels": order_labels, "values": order_vals, "type": "pie", "name": "Order Status"}],
                "layout": {"title": "Order Status Distribution", "height": 380}
            }
            orders_status_chart_data = json.dumps(order_chart, cls=PlotlyJSONEncoder)
        except Exception:
            app.logger.exception("Failed to build distribution pie charts")
            shipments_status_chart_data = json.dumps({"data": [], "layout": {}}, cls=PlotlyJSONEncoder)
            orders_status_chart_data = json.dumps({"data": [], "layout": {}}, cls=PlotlyJSONEncoder)

        return render_template(
            'distribution-overview.html',
            inventory=inventory,
            shipments=shipments,
            orders=orders,
            alerts=alerts,
            shipments_status_chart_data=shipments_status_chart_data,
            orders_status_chart_data=orders_status_chart_data
        )
    except Exception:
        app.logger.exception("DB unavailable for distribution overview")
        
@app.route('/edit_shipment/<int:id>', methods=['GET', 'POST'])
@login_required
def edit_shipment(id):
    if 'Shipment' not in globals():
        abort(404)
    sh = db.session.get(Shipment, id)
    if not sh:
        abort(404)

    if request.method == 'POST':
        try:
            form = request.form
            # date: try parse YYYY-MM-DD -> date object; otherwise set raw value
            date_val = form.get('date')
            if date_val:
                try:
                    parsed = datetime.strptime(date_val, '%Y-%m-%d').date()
                    _set_attr_if_exists(sh, 'date', parsed, date_try=True)
                except Exception:
                    _set_attr_if_exists(sh, 'date', date_val)

            _set_attr_if_exists(sh, 'carrier', form.get('carrier'))
            _set_attr_if_exists(sh, 'destination', form.get('destination'))
            _set_attr_if_exists(sh, 'status', form.get('status'))

            db.session.add(sh)
            db.session.commit()
            # redirect back to distribution overview
            return redirect(url_for('distribution_overview'))
        except Exception:
            try:
                db.session.rollback()
            except Exception:
                pass
            app.logger.exception("Failed to save shipment edits")
            # show edit page again with 500 status
            return render_template('edit_shipment.html', shipment=sh), 500

    # GET
    return render_template('edit_shipment.html', shipment=sh)


@app.route('/add_shipment', methods=['GET', 'POST'])
def add_shipment():
    if request.method == 'POST':
        date_val = request.form.get('date', '')
        carrier = request.form.get('carrier', '')
        destination = request.form.get('destination', '')
        status = request.form.get('status', '')

        try:
            sh = Shipment()
            # flexible attribute setting
            _set_attr_if_exists(sh, "shipment_id", request.form.get('shipment_id', ''), date_try=False)
            _set_attr_if_exists(sh, "date", date_val, date_try=True)
            _set_attr_if_exists(sh, "carrier", carrier)
            _set_attr_if_exists(sh, "carrier_name", carrier)
            _set_attr_if_exists(sh, "destination", destination)
            _set_attr_if_exists(sh, "dest", destination)
            _set_attr_if_exists(sh, "status", status)
            db.session.add(sh)
            db.session.commit()
            flash('Shipment added!', 'success')
        except Exception:
            db.session.rollback()
            app.logger.exception("Failed to add shipment to DB; falling back to file")
            # fallback to file-based storage for compatibility

        return redirect(url_for('distribution_overview'))
    return render_template('add_shipment.html')

@app.route('/add_order', methods=['GET', 'POST'])
def add_order():
    if request.method == 'POST':
        order_id = request.form.get('order_id', '')
        customer = request.form.get('customer', '')
        date_val = request.form.get('date', '')
        total = request.form.get('total', '')
        status = request.form.get('status', '')

        try:
            o = SalesOrder()
            # try multiple common fields
            _set_attr_if_exists(o, "order_id", order_id)
            if customer.isdigit():
                _set_attr_if_exists(o, "customer_id", int(customer))
            else:
                _set_attr_if_exists(o, "customer", customer)
                _set_attr_if_exists(o, "customer_name", customer)
            _set_attr_if_exists(o, "date", date_val, date_try=True)
            _set_attr_if_exists(o, "order_date", date_val, date_try=True)
            _set_attr_if_exists(o, "total", total, cast_float=True)
            _set_attr_if_exists(o, "amount", total, cast_float=True)
            _set_attr_if_exists(o, "status", status)
            db.session.add(o)
            db.session.commit()
            flash('Order added!', 'success')
        except Exception:
            db.session.rollback()
            app.logger.exception("Failed to add order to DB")

        return redirect(url_for('distribution_overview'))
    return render_template('add_order.html')

@app.route('/receive_inventory', methods=['GET', 'POST'])
def receive_inventory():
    if request.method == 'POST':
        product = request.form.get('product', '')
        sku = request.form.get('sku', '')
        warehouse = request.form.get('warehouse', '')
        quantity = request.form.get('quantity', '')
        reorder_level = request.form.get('reorder_level', '')
        status = request.form.get('status', '')
        unit_cost = request.form.get('unit_cost', '')

        try:
            item = InventoryItem()
            _set_attr_if_exists(item, "product", product)
            _set_attr_if_exists(item, "name", product)
            _set_attr_if_exists(item, "sku", sku)
            _set_attr_if_exists(item, "code", sku)
            _set_attr_if_exists(item, "warehouse", warehouse)
            _set_attr_if_exists(item, "location", warehouse)
            _set_attr_if_exists(item, "quantity", quantity, cast_float=True)
            _set_attr_if_exists(item, "qty", quantity, cast_float=True)
            _set_attr_if_exists(item, "reorder_level", reorder_level, cast_float=True)
            _set_attr_if_exists(item, "reorder", reorder_level, cast_float=True)
            _set_attr_if_exists(item, "status", status)
            _set_attr_if_exists(item, "unit_cost", unit_cost, cast_float=True)
            db.session.add(item)
            db.session.commit()
            flash('Inventory received!', 'success')
        except Exception:
            db.session.rollback()
            app.logger.exception("Failed to add inventory to DB")
            
        return redirect(url_for('distribution_overview'))
    return render_template('receive_inventory.html')

FINANCE_DATA_FILE = 'static/js/test_finance_data.json'

def load_finance():
    with open(FINANCE_DATA_FILE, 'r') as f:
        return json.load(f)

def save_finance(data):
    with open(FINANCE_DATA_FILE, 'w') as f:
        json.dump(data, f, indent=2)

@app.route('/finance/approve_request/<int:id>', methods=['POST'])
@login_required
def approve_purchase_request(id):
    if 'PurchaseRequest' not in globals():
        flash("PurchaseRequest model not available.", "error")
        return redirect(url_for('finance_overview'))
    try:
        pr = db.session.get(PurchaseRequest, id)
        if not pr:
            flash("Purchase request not found.", "error")
            return redirect(url_for('finance_overview'))
        # set common status fields
        if hasattr(pr, "status"):
            setattr(pr, "status", "Approved")
        elif hasattr(pr, "state"):
            setattr(pr, "state", "Approved")
        db.session.add(pr)
        db.session.commit()
        flash(f"Purchase request {getattr(pr, 'request_id', id)} approved.", "success")
        try:
            log_audit(action="approve", resource_type="PurchaseRequest", resource_id=getattr(pr, "id", None), after=pr)
        except Exception:
            app.logger.debug("Audit log failed for approve_purchase_request", exc_info=True)
    except Exception:
        db.session.rollback()
        app.logger.exception("Failed to approve purchase request")
        flash("Failed to approve purchase request.", "error")
    return redirect(url_for('finance_overview'))


@app.route('/finance/reject_request/<int:id>', methods=['POST'])
@login_required
def reject_purchase_request(id):
    if 'PurchaseRequest' not in globals():
        flash("PurchaseRequest model not available.", "error")
        return redirect(url_for('finance_overview'))
    try:
        pr = db.session.get(PurchaseRequest, id)
        if not pr:
            flash("Purchase request not found.", "error")
            return redirect(url_for('finance_overview'))
        if hasattr(pr, "status"):
            setattr(pr, "status", "Denied")
        elif hasattr(pr, "state"):
            setattr(pr, "state", "Denied")
        db.session.add(pr)
        db.session.commit()
        flash(f"Purchase request {getattr(pr, 'request_id', id)} rejected.", "success")
        try:
            log_audit(action="reject", resource_type="PurchaseRequest", resource_id=getattr(pr, "id", None), after=pr)
        except Exception:
            app.logger.debug("Audit log failed for reject_purchase_request", exc_info=True)
    except Exception:
        db.session.rollback()
        app.logger.exception("Failed to reject purchase request")
        flash("Failed to reject purchase request.", "error")
    return redirect(url_for('finance_overview'))

@app.route('/finance-overview')
@login_required
def finance_overview():
    def safe_get(row, *candidates, default=None):
        for c in candidates:
            if hasattr(row, c):
                try:
                    val = getattr(row, c)
                    if hasattr(val, "isoformat"):
                        return val.isoformat()
                    return val
                except Exception:
                    continue
        return default

    try:
        any_db = False

        # Financial summary lines
        financial_summary = {}
        if 'FinancialSummaryLine' in globals():
            any_db = True
            rows = db.session.query(FinancialSummaryLine).order_by(getattr(FinancialSummaryLine, "id", FinancialSummaryLine)).all()
            for r in rows:
                label = safe_get(r, "label", "name") or ""
                amount = float(safe_get(r, "value_amount", "amount", default=0) or 0)
                period = safe_get(r, "period", default="")
                
                # store the numeric amount for templates that expect a scalar
                financial_summary[label] = amount

        # Income statement lines
        income_statement = []
        if 'IncomeStatementLine' in globals():
            any_db = True
            rows = db.session.query(IncomeStatementLine).order_by(getattr(IncomeStatementLine, "id", IncomeStatementLine)).all()
            for r in rows:
                income_statement.append({
                    "period": safe_get(r, "period", ""),
                    "category": safe_get(r, "category", "label", ""),
                    "amount": float(safe_get(r, "amount", default=0) or 0),
                    "line_type": safe_get(r, "line_type", "type", "")
                })

        # Cash flow entries
        cash_flow = []
        if 'CashFlowEntry' in globals():
            any_db = True
            rows = db.session.query(CashFlowEntry).order_by(getattr(CashFlowEntry, "date", CashFlowEntry)).all()
            for r in rows:
                cash_flow.append({
                    "entry_id": safe_get(r, "entry_id", "id"),
                    "date": safe_get(r, "date"),
                    "description": safe_get(r, "description", ""),
                    "inflow": float(safe_get(r, "inflow", default=0) or 0),
                    "outflow": float(safe_get(r, "outflow", default=0) or 0),
                    "balance": float(safe_get(r, "balance", default=0) or 0)
                })

        # Outstanding payments
        outstanding_payments = []
        # Prefer a dedicated OutstandingPayment model, otherwise try Payment as fallback
        if 'OutstandingPayment' in globals():
            any_db = True
            rows = db.session.query(OutstandingPayment).order_by(getattr(OutstandingPayment, "due_date", OutstandingPayment)).all()
            for r in rows:
                outstanding_payments.append({
                    "payment_id": safe_get(r, "payment_id", "id"),
                    "party": safe_get(r, "party", "payee", ""),
                    "due_date": safe_get(r, "due_date", "date"),
                    "amount": float(safe_get(r, "amount", default=0) or 0),
                    "status": safe_get(r, "status", "")
                })
        elif 'Payment' in globals():
            # Payment model already used elsewhere but may contain payments we can surface
            any_db = True
            rows = db.session.query(Payment).order_by(getattr(Payment, "date", Payment)).all()
            for r in rows:
                outstanding_payments.append({
                    "payment_id": safe_get(r, "payment_id", "id"),
                    "party": safe_get(r, "party", getattr(r, "invoice_id", None) or ""),  # best-effort
                    "due_date": safe_get(r, "date"),
                    "amount": float(safe_get(r, "amount", default=0) or 0),
                    "status": safe_get(r, "status", "")
                })

        # Finance chart data (JSON stored)
        finance_chart_data = {}
        if 'FinanceChartData' in globals():
            any_db = True
            rows = db.session.query(FinanceChartData).order_by(getattr(FinanceChartData, "chart_id", FinanceChartData)).all()
            for r in rows:
                name = safe_get(r, "name", "chart_id")
                chart_json = safe_get(r, "chart_json", "data") or safe_get(r, "json", None)
                # attempt to parse if stored as text
                try:
                    parsed = chart_json if isinstance(chart_json, (dict, list)) else json.loads(chart_json)
                except Exception:
                    parsed = {"data": [], "layout": {}}
                finance_chart_data[name] = parsed

        # --- Pending Purchase Requests for finance approval ---
        pending_purchase_requests = []
        if 'PurchaseRequest' in globals():
            try:
                any_db = True
                status_col = getattr(PurchaseRequest, "status", None)
                q = db.session.query(PurchaseRequest)
                if status_col is not None:
                    q = q.filter(func.lower(status_col) == 'pending')
                pr_rows = q.order_by(getattr(PurchaseRequest, "id", PurchaseRequest)).all()
                for r in pr_rows:
                    pending_purchase_requests.append({
                        "id": getattr(r, "id", None),
                        "request_id": safe_get(r, "request_id", "id") or getattr(r, "id", None),
                        "requested_by": safe_get(r, "requested_by", "requested_by_name", default=""),
                        "date": safe_get(r, "date", "requested_on", "created_at", default=""),
                        "item": safe_get(r, "item", "description", default=""),
                        "estimated_cost": float(safe_get(r, "estimated_cost", "amount", "cost", default=0) or 0),
                        "status": safe_get(r, "status", default="Pending")
                    })
            except Exception:
                app.logger.exception("Failed to load pending purchase requests")

        if not any_db:
            raise RuntimeError("No finance DB models available")

        # Prepare summary list (template expects list of label/value)
        summary_list = []
        if financial_summary:
            for k, v in financial_summary.items():
                # support both dict (legacy) and scalar values
                if isinstance(v, dict):
                    val = v.get('value', 0)
                else:
                    val = v or 0
                try:
                    valf = float(val)
                except Exception:
                    valf = 0.0
                summary_list.append({"label": k, "value": f"{valf:.2f}"})
        else:
            # fallback aggregate from income_statement / cash_flow if needed
            total_revenue = sum(i.get("amount", 0) for i in income_statement if i.get("line_type") == "income")
            total_expenses = -sum(i.get("amount", 0) for i in income_statement if i.get("line_type") == "expense")
            summary_list = [
                {"label": "Total Revenue", "value": f"{total_revenue:.2f}"},
                {"label": "Total Expenses", "value": f"{total_expenses:.2f}"}
            ]

        # --- NEW DYNAMIC ALERTS FOR FINANCE ---
        alerts = []

        # Derive outstanding_invoices_count from outstanding_payments if available
        outstanding_invoices_count = 0
        try:
            outstanding_invoices_count = sum(
                1 for p in (outstanding_payments or []) if str(p.get('status', '')).lower() != 'paid'
            )
        except Exception:
            outstanding_invoices_count = 0

        if outstanding_invoices_count > 0:
            alerts.append(f"Attention: {outstanding_invoices_count} invoices are currently unpaid.")

        # Check for pending purchase requests
        pending_pr_count = len(pending_purchase_requests or [])
        if pending_pr_count > 0:
            alerts.append(f"Action Required: {pending_pr_count} purchase requests await approval.")

        # Build accounts list (best-effort from DB) so we can check balances
        accounts = []
        if 'Account' in globals():
            try:
                acc_rows = db.session.query(Account).order_by(getattr(Account, "id", Account)).all()
                for a in acc_rows:
                    try:
                        accounts.append({
                            "id": getattr(a, "id", None),
                            "name": getattr(a, "name", "") or "",
                            "type": getattr(a, "type", "") or "",
                            "balance": float(getattr(a, "balance", 0) or 0),
                            "currency": getattr(a, "currency", "") or "",
                            "status": getattr(a, "status", "") or ""
                        })
                    except Exception:
                        # skip malformed account row
                        continue
            except Exception:
                app.logger.exception("Failed to load accounts for alerts")

        # Check for low account balances (example threshold 1000)
        for acc in accounts:
            try:
                if float(acc.get('balance', 0) or 0) < 1000:
                    alerts.append(f"Low Balance: Account '{acc.get('name')}' is below 1,000.")
            except Exception:
                continue

        # Basic charts extraction
        cashflow_chart = finance_chart_data.get("monthly_cashflow") or {"data": [], "layout": {}}
        expense_breakdown = finance_chart_data.get("expense_breakdown") or {"data": [], "layout": {}}
        revenue_sources = finance_chart_data.get("revenue_sources") or {"data": [], "layout": {}}

        return render_template(
            'finance-overview.html',
            financial_summary=financial_summary,
            financial_summary_list=summary_list,
            income_statement=income_statement,
            cash_flow=cash_flow,
            outstanding_payments=outstanding_payments,
            finance_chart_data=finance_chart_data,
            cashflow_chart=cashflow_chart,
            expense_breakdown=expense_breakdown,
            revenue_sources=revenue_sources,
            pending_purchase_requests=pending_purchase_requests,
            alerts=alerts
        )

    except Exception:
        app.logger.exception("DB unavailable for finance overview; falling back to file")
        data = load_finance()
        financial_summary = data.get('financial_summary', {})
        income_statement = data.get('income_statement', {})
        cash_flow = data.get('cash_flow', [])
        outstanding_payments = data.get('outstanding_payments', [])
        finance_chart_data = data.get('finance_chart_data', {})
        return render_template('finance-overview.html',
                               financial_summary=financial_summary,
                               income_statement=income_statement,
                               cash_flow=cash_flow,
                               outstanding_payments=outstanding_payments,
                               finance_chart_data=finance_chart_data,
                               pending_purchase_requests=[])


@app.route('/add_receipt', methods=['GET', 'POST'])
@login_required
def add_receipt():
    """
    Add a cashflow receipt row: prefer CashFlowEntry model; otherwise use finance JSON.
    """
    if request.method == 'POST':
        date_val = request.form.get('date', '').strip()
        description = request.form.get('description', '').strip()
        inflow = request.form.get('inflow', '').strip()
        balance = request.form.get('balance', '').strip()

        # DB attempt
        try:
            if 'CashFlowEntry' in globals():
                cfe = CashFlowEntry()
                _set_attr_if_exists(cfe, "entry_id", request.form.get('entry_id', None))
                _set_attr_if_exists(cfe, "date", date_val, date_try=True)
                _set_attr_if_exists(cfe, "description", description)
                _set_attr_if_exists(cfe, "inflow", inflow, cast_float=True)
                _set_attr_if_exists(cfe, "outflow", 0, cast_float=True)
                _set_attr_if_exists(cfe, "balance", balance, cast_float=True)
                db.session.add(cfe)
                db.session.commit()
                flash('Receipt added successfully!', 'success')
                return redirect(url_for('finance_overview'))
        except Exception:
            db.session.rollback()
            app.logger.exception("Failed to insert cashflow entry into DB; falling back to file")

        # File fallback
        try:
            data = load_finance()
            cash_flow = data.get('cash_flow', [])
            new_receipt = {
                'date': date_val,
                'description': description,
                'inflow': float(inflow or 0),
                'outflow': 0.0,
                'balance': float(balance or 0)
            }
            cash_flow.append(new_receipt)
            data['cash_flow'] = cash_flow
            save_finance(data)
            flash('Receipt added successfully (file fallback)!', 'success')
        except Exception:
            app.logger.exception("Failed to append receipt to finance JSON")
            flash('Failed to add receipt.', 'error')

        return redirect(url_for('finance_overview'))

    return render_template('add_receipt.html')


@app.route('/download_finance_report')
def download_finance_report():
    data = load_finance()
    financial_summary = data.get('financial_summary', {})
    income_statement = data.get('income_statement', {})
    cash_flow = data.get('cash_flow', [])
    outstanding_payments = data.get('outstanding_payments', [])

    html_out = render_template(
        'finance_report.html',
        financial_summary=financial_summary,
        income_statement=income_statement,
        cash_flow=cash_flow,
        outstanding_payments=outstanding_payments
    )

    pdf = pdfkit.from_string(html_out, False, configuration=config)
    response = make_response(pdf)
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = 'attachment; filename=finance_report.pdf'
    return response

@app.route('/generate_report')
def generate_report():
    data = load_finance()
    financial_summary = data.get('financial_summary', {})
    income_statement = data.get('income_statement', {})
    cash_flow = data.get('cash_flow', [])
    outstanding_payments = data.get('outstanding_payments', [])
    return render_template(
        'finance_report.html',
        financial_summary=financial_summary,
        income_statement=income_statement,
        cash_flow=cash_flow,
        outstanding_payments=outstanding_payments
    )

#HUMAN RESOURCES

@app.route('/add_employee', methods=['GET', 'POST'])
def add_employee():
    """
    Create an Employee row in the DB when possible, otherwise fall back to file-based storage.
    """
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        department = request.form.get('department', '').strip()
        role = request.form.get('role', '').strip()
        email = request.form.get('email', '').strip()
        phone = request.form.get('phone', '').strip()
        status = request.form.get('status', '').strip() or 'Active'

        # Try DB insert first
        try:
            if 'Employee' in globals():
                emp = Employee()
                _set_attr_if_exists(emp, "name", name)
                _set_attr_if_exists(emp, "department", department)
                _set_attr_if_exists(emp, "role", role)
                _set_attr_if_exists(emp, "email", email)
                _set_attr_if_exists(emp, "phone", phone)
                _set_attr_if_exists(emp, "status", status)
                # optional timestamp fields
                for ts in ("hired_at", "created_at", "joined_on", "created"):
                    if hasattr(emp, ts):
                        _set_attr_if_exists(emp, ts, datetime.utcnow().isoformat())
                        break
                db.session.add(emp)
                db.session.commit()
                flash('Employee added successfully!', 'success')
                return redirect(url_for('human_resources_overview'))
        except Exception:
            db.session.rollback()
            app.logger.exception("Failed to add employee to DB; falling back to file")
            # fall through to render form (with flashes) so response is always returned

    # If GET or POST fallback, render the form
    return render_template('add_employee.html')

@app.route('/edit_employee/<employee_id>', methods=['GET', 'POST'])
def edit_employee(employee_id):
    """
    Edit an employee: prefer DB row update; fallback to JSON file.
    """
    # Try DB first
    emp_obj = None
    try:
        # allow numeric id or string id depending on model
        if 'Employee' in globals():
            try:
                eid = int(employee_id)
            except Exception:
                eid = employee_id
            emp_obj = db.session.get(Employee, eid)
    except Exception:
        app.logger.exception("DB lookup failed for edit_employee")
        emp_obj = None

    if emp_obj:
        if request.method == 'POST':
            name = request.form.get('name', '').strip()
            department = request.form.get('department', '').strip()
            role = request.form.get('role', '').strip()
            email = request.form.get('email', '').strip()
            phone = request.form.get('phone', '').strip()
            status = request.form.get('status', '').strip()
            try:
                _set_attr_if_exists(emp_obj, "name", name)
                _set_attr_if_exists(emp_obj, "department", department)
                _set_attr_if_exists(emp_obj, "role", role)
                _set_attr_if_exists(emp_obj, "email", email)
                _set_attr_if_exists(emp_obj, "phone", phone)
                _set_attr_if_exists(emp_obj, "status", status)
                # update timestamp if available
                for ts in ("updated_at", "modified_at", "updated"):
                    if hasattr(emp_obj, ts):
                        _set_attr_if_exists(emp_obj, ts, datetime.utcnow().isoformat())
                        break
                db.session.add(emp_obj)
                db.session.commit()
                flash('Employee updated successfully!', 'success')
                return redirect(url_for('human_resources_overview'))
            except Exception:
                db.session.rollback()
                app.logger.exception("Failed to update employee in DB")
                flash('Failed to update employee (database error).', 'danger')
                return redirect(url_for('human_resources_overview'))
        # GET -> prepare dict for template
        employee_dict = {
            "id": getattr(emp_obj, "id", None),
            "name": getattr(emp_obj, "name", "") or "",
            "department": getattr(emp_obj, "department", "") or "",
            "role": getattr(emp_obj, "role", "") or "",
            "email": getattr(emp_obj, "email", "") or "",
            "phone": getattr(emp_obj, "phone", "") or "",
            "status": getattr(emp_obj, "status", "") or ""
        }
        return render_template('edit_employee.html', employee=employee_dict)


@app.route('/delete_employee/<employee_id>', methods=['POST'])
def delete_employee(employee_id):
    """
    Delete employee from DB when possible; otherwise remove from HR JSON.
    """
    # Try DB delete
    try:
        if 'Employee' in globals():
            try:
                eid = int(employee_id)
            except Exception:
                eid = employee_id
            emp = db.session.get(Employee, eid)
            if emp:
                db.session.delete(emp)
                db.session.commit()
                flash('Employee deleted successfully!', 'success')
                return redirect(url_for('human_resources_overview'))
    except Exception:
        db.session.rollback()
        app.logger.exception("Failed to delete employee from DB")

#Fix this
@app.route('/departments_overview')
def departments_overview():
    """
    Aggregate departments from DB Employee table when available; fallback to HR JSON.
    """
    try:
        if 'Employee' in globals():
            rows = db.session.query(Employee).all()
            departments = sorted(set(getattr(r, "department", "") or "" for r in rows if getattr(r, "department", None)))
            department_stats = [
                {'name': dept, 'employees': sum(1 for r in rows if (getattr(r, "department", "") or "") == dept)}
                for dept in departments
            ]
            return render_template('departments_overview.html', departments=department_stats)
    except Exception:
        app.logger.exception("DB unavailable for departments_overview; falling back to file")

    # file fallback
    employees = ('employees', [])
    departments = sorted(set(e.get('department') for e in employees))
    department_stats = [
        {'name': dept, 'employees': sum(1 for e in employees if e.get('department') == dept)}
        for dept in departments
    ]
    return render_template('departments_overview.html', departments=department_stats)

def _is_hr_or_admin(user=None):
    """Return True if user is HR or Admin (legacy checks: role string or group_id==0)."""
    u = user or current_user
    try:
        m = getattr(u, "model", None)
        if not m:
            return False
        role = (getattr(m, "role", "") or "").lower()
        if role in ("hr", "admin"):
            return True
        if getattr(m, "group_id", None) == 0:
            return True
        return False
    except Exception:
        return False

@app.route('/attendance_overview')
@login_required
def attendance_overview():
    """
    List attendance records with filters and pagination.
    Query params: employee_id, date_from (YYYY-MM-DD), date_to (YYYY-MM-DD), status, page, per_page
    """
    def safe_get(row, *candidates, default=None):
        for c in candidates:
            if hasattr(row, c):
                try:
                    val = getattr(row, c)
                    if hasattr(val, "isoformat"):
                        return val.isoformat()
                    return val
                except Exception:
                    continue
        return default

    employee_id = request.args.get('employee_id', '').strip()
    date_from = request.args.get('date_from', '').strip()
    date_to = request.args.get('date_to', '').strip()
    status = request.args.get('status', '').strip()
    try:
        page = max(1, int(request.args.get('page', 1)))
    except Exception:
        page = 1
    try:
        per_page = max(10, int(request.args.get('per_page', 20)))
    except Exception:
        per_page = 20

    try:
        if 'Attendance' in globals():
            q = db.session.query(Attendance)
            # date column handling
            date_col = getattr(Attendance, "date", None)
            if employee_id:
                if hasattr(Attendance, "employee_id"):
                    try:
                        q = q.filter(getattr(Attendance, "employee_id") == int(employee_id))
                    except Exception:
                        q = q.filter(getattr(Attendance, "employee_id") == employee_id)
                elif hasattr(Attendance, "employee"):
                    q = q.filter(getattr(Attendance, "employee") == employee_id)
            if status:
                if hasattr(Attendance, "status"):
                    q = q.filter(getattr(Attendance, "status") == status)
            # date range
            if date_from and date_col is not None:
                try:
                    df = datetime.fromisoformat(date_from)
                    q = q.filter(date_col >= df)
                except Exception:
                    pass
            if date_to and date_col is not None:
                try:
                    dt = datetime.fromisoformat(date_to)
                    q = q.filter(date_col <= dt)
                except Exception:
                    pass

            total = q.count()
            rows = q.order_by(getattr(Attendance, "date", Attendance).desc()).offset((page - 1) * per_page).limit(per_page).all()

            attendance_records = []
            for r in rows:
                attendance_records.append({
                    "id": safe_get(r, "id"),
                    "employee_id": safe_get(r, "employee_id", "employee"),
                    "date": safe_get(r, "date"),
                    "status": safe_get(r, "status"),
                    "check_in": safe_get(r, "check_in"),
                    "check_out": safe_get(r, "check_out"),
                    # preserve model instance where useful
                    "_model": r
                })

            # Audit: record view (best-effort)
            try:
                log_audit(action="view", resource_type="Attendance", resource_id=None,
                          after={"filters": {"employee_id": employee_id, "date_from": date_from, "date_to": date_to, "status": status, "page": page, "per_page": per_page}, "count": len(attendance_records)})
            except Exception:
                app.logger.debug("Attendance view audit failed", exc_info=True)

            pagination = {"page": page, "per_page": per_page, "total": total}
            return render_template('attendance_overview.html', attendance_records=attendance_records, pagination=pagination, filters={"employee_id": employee_id, "date_from": date_from, "date_to": date_to, "status": status})
    except Exception:
        app.logger.exception("DB unavailable for attendance_overview; falling back to file")

    # file fallback (existing behavior)
    attendance_records = ('attendance', [])
    return render_template('attendance_overview.html', attendance_records=attendance_records, pagination={"page":1,"per_page":len(attendance_records),"total":len(attendance_records)}, filters={})

@app.route('/attendance/add', methods=['GET', 'POST'])
@login_required
def add_attendance():
    """
    Add an attendance record. POST: employee_id, date (YYYY-MM-DD), status, check_in, check_out
    """
    if request.method == 'POST':
        employee_id = request.form.get('employee_id', '').strip()
        date_val = request.form.get('date', '').strip()
        status = request.form.get('status', '').strip() or 'Present'
        check_in = request.form.get('check_in', '').strip()
        check_out = request.form.get('check_out', '').strip()

        try:
            if 'Attendance' in globals():
                a = Attendance()
                _set_attr_if_exists(a, "employee_id", int(employee_id) if employee_id.isdigit() else employee_id)
                _set_attr_if_exists(a, "employee", employee_id)
                _set_attr_if_exists(a, "date", date_val, date_try=True)
                _set_attr_if_exists(a, "status", status)
                _set_attr_if_exists(a, "check_in", check_in)
                _set_attr_if_exists(a, "check_out", check_out)
                db.session.add(a)
                db.session.commit()
                try:
                    log_audit(action="create", resource_type="Attendance", resource_id=getattr(a, "id", None), after=a)
                except Exception:
                    app.logger.debug("Audit log failed for add_attendance", exc_info=True)
                flash("Attendance record added.", "success")
                return redirect(url_for('attendance_overview'))
        except Exception:
            db.session.rollback()
            app.logger.exception("Failed to add attendance to DB")
            flash("Failed to add attendance record.", "error")
            return redirect(url_for('attendance_overview'))

    return render_template('add_attendance.html')

@app.route('/attendance/edit/<int:id>', methods=['GET', 'POST'])
@login_required
def edit_attendance(id):
    """
    Edit an attendance record by id.
    """
    if 'Attendance' not in globals():
        flash("Attendance model not available.", "error")
        return redirect(url_for('attendance_overview'))

    rec = db.session.get(Attendance, id)
    if not rec:
        flash("Attendance record not found.", "error")
        return redirect(url_for('attendance_overview'))

    if request.method == 'POST':
        try:
            before = rec.to_dict() if hasattr(rec, "to_dict") else None
            _set_attr_if_exists(rec, "employee_id", request.form.get('employee_id'))
            _set_attr_if_exists(rec, "employee", request.form.get('employee_id'))
            _set_attr_if_exists(rec, "date", request.form.get('date'), date_try=True)
            _set_attr_if_exists(rec, "status", request.form.get('status'))
            _set_attr_if_exists(rec, "check_in", request.form.get('check_in'))
            _set_attr_if_exists(rec, "check_out", request.form.get('check_out'))
            db.session.add(rec)
            db.session.commit()
            try:
                log_audit(action="update", resource_type="Attendance", resource_id=getattr(rec, "id", None), before=before, after=rec)
            except Exception:
                app.logger.debug("Audit log failed for edit_attendance", exc_info=True)
            flash("Attendance updated.", "success")
        except Exception:
            db.session.rollback()
            app.logger.exception("Failed to update attendance")
            flash("Failed to update attendance.", "error")
        return redirect(url_for('attendance_overview'))

    # GET: prepare dict for template
    rec_dict = {
        "id": getattr(rec, "id", None),
        "employee_id": getattr(rec, "employee_id", None) or getattr(rec, "employee", ""),
        "date": getattr(rec, "date", None),
        "status": getattr(rec, "status", None),
        "check_in": getattr(rec, "check_in", None),
        "check_out": getattr(rec, "check_out", None)
    }
    try:
        return render_template('edit_attendance.html', attendance=rec_dict)
    except Exception:
        return redirect(url_for('attendance_overview'))

@app.route('/attendance/delete/<int:id>', methods=['POST'])
@login_required
def delete_attendance(id):
    """
    Delete attendance record. Only HR or Admin allowed.
    """
    if not _is_hr_or_admin():
        return render_template("403.html"), 403

    if 'Attendance' not in globals():
        flash("Attendance model not available.", "error")
        return redirect(url_for('attendance_overview'))

    rec = db.session.get(Attendance, id)
    if not rec:
        flash("Attendance record not found.", "error")
        return redirect(url_for('attendance_overview'))

    try:
        before = rec.to_dict() if hasattr(rec, "to_dict") else None
        db.session.delete(rec)
        db.session.commit()
        try:
            log_audit(action="delete", resource_type="Attendance", resource_id=id, before=before)
        except Exception:
            app.logger.debug("Audit log failed for delete_attendance", exc_info=True)
        flash("Attendance record deleted.", "success")
    except Exception:
        db.session.rollback()
        app.logger.exception("Failed to delete attendance")
        flash("Failed to delete attendance record.", "error")

    return redirect(url_for('attendance_overview'))

#Fix this
@app.route('/payroll_overview')
def payroll_overview():
    """
    Prefer DB Payroll table when available; otherwise use HR JSON 'payroll'.
    """
    try:
        if 'Payroll' in globals():
            payroll_rows = db.session.query(Payroll).order_by(getattr(Payroll, "id", Payroll)).all()
            payroll = []
            for p in payroll_rows:
                payroll.append({
                    'id': getattr(p, 'id', None),
                    'employee_id': getattr(p, 'employee_id', None),
                    'month': getattr(p, 'month', None),
                    'gross_pay': getattr(p, 'gross_pay', None),
                    'deductions': getattr(p, 'deductions', None),
                    'net_pay': getattr(p, 'net_pay', None),
                    'status': getattr(p, 'status', None)
                })
            return render_template('payroll_overview.html', payroll=payroll)
    except Exception:
        app.logger.exception("DB unavailable for payroll_overview; falling back to file")

    payroll = ('payroll', [])
    return render_template('payroll_overview.html', payroll=payroll)

#Fix this
@app.route('/leave_overview')
def leave_overview():
    """
    Prefer DB LeaveRequest table when available; otherwise use HR JSON 'leave_requests'.
    """
    try:
        if 'LeaveRequest' in globals():
            leaves = db.session.query(LeaveRequest).order_by(getattr(LeaveRequest, "id", LeaveRequest)).all()
            leave_requests = []
            for l in leaves:
                leave_requests.append({
                    'id': getattr(l, 'id', None),
                    'employee_id': getattr(l, 'employee_id', None),
                    'start_date': getattr(l, 'start_date', None),
                    'end_date': getattr(l, 'end_date', None),
                    'leave_type': getattr(l, 'leave_type', None),
                    'reason': getattr(l, 'reason', None),
                    'status': getattr(l, 'status', None),
                    'requested_on': getattr(l, 'requested_on', None)
                })
            return render_template('leave_overview.html', leave_requests=leave_requests)
    except Exception:
        app.logger.exception("DB unavailable for leave_overview")

#Fix this
@app.route('/hr_reports')
def hr_reports():
    """
    Prefer DB reports if a Reports model/table exists; otherwise load from HR JSON.
    """
    try:
        if 'HRReport' in globals():
            reports_rows = db.session.query(HRReport).order_by(getattr(HRReport, "date", HRReport)).all()
            reports = [{"title": getattr(r, "title", ""), "date": getattr(r, "date", "")} for r in reports_rows]
            return render_template('hr_reports.html', reports=reports)
    except Exception:
        app.logger.exception("DB unavailable for hr_reports; falling back to file")

    reports =('reports', [
        {"title": "Headcount Report", "date": "2025-10-01"},
        {"title": "Attendance Summary", "date": "2025-10-10"}
    ])
    return render_template('hr_reports.html', reports=reports)

#TIME-TASK ANALYSIS

# ---------- Time-Task Analysis endpoints ----------
@app.route('/time-task-analysis')
@login_required
def time_task_analysis_page():
    """Render the time-task analysis UI."""
    return render_template('time-task-analysis.html')


def _set_duration_field(obj, value):
    """Try to set duration on common column names; fallback into meta if none exist."""
    if value is None or value == "":
        return False
    # normalize to float seconds
    val = None
    try:
        val = float(value)
    except Exception:
        try:
            # timedelta-like
            if hasattr(value, "total_seconds"):
                val = float(value.total_seconds())
        except Exception:
            pass
    if val is None:
        return False
    for fld in ("duration_seconds", "duration", "duration_sec", "length_seconds", "seconds"):
        if hasattr(obj, fld):
            try:
                setattr(obj, fld, val)
                return True
            except Exception:
                continue
    # fallback: store inside meta JSON if available
    try:
        meta = getattr(obj, "meta", None)
        if meta is None:
            try:
                setattr(obj, "meta", {"duration_seconds": val})
            except Exception:
                pass
        elif isinstance(meta, dict):
            meta["duration_seconds"] = val
            try:
                setattr(obj, "meta", meta)
            except Exception:
                pass
        return True
    except Exception:
        return False


def _emit_task_event(task_type: str,
                     event_type: str,
                     task_id: str = None,
                     duration_seconds: float = None,
                     work_center_id: int = None,
                     production_order_id = None,
                     employee_id: int = None,
                     meta: dict = None):
    """
    Create a TaskEvent row (used as the local implementation of calling /api/task_events).
    Using direct DB insert is more reliable than issuing an HTTP request to self.
    """
    try:
        from models import TaskEvent
        te = TaskEvent()
        if task_id is not None:
            try:
                setattr(te, "task_id", str(task_id))
            except Exception:
                pass
        _set_attr_if_exists(te, "task_type", task_type)
        _set_attr_if_exists(te, "event_type", event_type)
        _set_attr_if_exists(te, "timestamp", datetime.utcnow())
        # Explicitly set the persisted duration column if available
        if duration_seconds is not None:
            try:
                dur_val = float(duration_seconds)
            except Exception:
                try:
                    if hasattr(duration_seconds, "total_seconds"):
                        dur_val = float(duration_seconds.total_seconds())
                    else:
                        dur_val = None
                except Exception:
                    dur_val = None
            if dur_val is not None:
                # prefer direct attribute write to ensure column is populated
                try:
                    if hasattr(te, "duration_seconds"):
                        setattr(te, "duration_seconds", dur_val)
                    else:
                        # fallback into tolerant helper (stores in meta)
                        _set_duration_field(te, dur_val)
                except Exception:
                    _set_duration_field(te, dur_val)
        # ...existing code continues...
        if work_center_id is not None:
            try:
                setattr(te, "work_center_id", int(work_center_id))
            except Exception:
                setattr(te, "work_center_id", work_center_id)
        if production_order_id is not None:
            try:
                setattr(te, "production_order_id", int(production_order_id))
            except Exception:
                setattr(te, "production_order_id", production_order_id)
        if employee_id is not None:
            try:
                setattr(te, "employee_id", int(employee_id))
            except Exception:
                setattr(te, "employee_id", employee_id)
        if isinstance(meta, dict):
            # merge duration into meta if duration not on column
            try:
                existing_meta = getattr(te, "meta", {}) or {}
                if dur_val is not None and "duration_seconds" not in existing_meta:
                    existing_meta["duration_seconds"] = dur_val
                setattr(te, "meta", {**existing_meta, **meta} if meta else existing_meta)
            except Exception:
                try:
                    setattr(te, "meta", meta)
                except Exception:
                    pass
        db.session.add(te)
        db.session.commit()
        return getattr(te, "id", None)
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass
        app.logger.exception("Failed to emit task event")
        return None



@app.route('/api/task_events', methods=['POST'])
@login_required
def api_create_task_event():
    """Create a TaskEvent (JSON or form)."""
    try:
        from models import TaskEvent
        data = request.get_json(silent=True) or request.form or {}
        te = TaskEvent()
        _set_attr_if_exists(te, 'task_id', data.get('task_id'))
        _set_attr_if_exists(te, 'task_type', data.get('task_type') or data.get('type') or 'generic')
        _set_attr_if_exists(te, 'event_type', data.get('event_type') or data.get('type_event') or 'start')
        ts = data.get('timestamp')
        if ts:
            try:
                _set_attr_if_exists(te, 'timestamp', datetime.fromisoformat(ts))
            except Exception:
                _set_attr_if_exists(te, 'timestamp', ts, date_try=True)
        # support multiple names for duration in incoming payload and set persisted column explicitly
        dur = None
        if isinstance(data, dict):
            for key in ('duration_seconds', 'duration', 'duration_sec', 'durationSec'):
                if key in data and data.get(key) not in (None, ''):
                    dur = data.get(key)
                    break
        else:
            # form-like MultiDict: check keys
            for key in ('duration_seconds', 'duration', 'duration_sec'):
                if data.get(key):
                    dur = data.get(key)
                    break
        if dur is not None:
            try:
                dur_val = float(dur)
            except Exception:
                try:
                    if hasattr(dur, "total_seconds"):
                        dur_val = float(dur.total_seconds())
                    else:
                        dur_val = None
                except Exception:
                    dur_val = None
            if dur_val is not None:
                try:
                    if hasattr(te, "duration_seconds"):
                        setattr(te, "duration_seconds", dur_val)
                    else:
                        _set_duration_field(te, dur_val)
                except Exception:
                    _set_duration_field(te, dur_val)
        # ...existing code continues (work_center, production_order, meta etc.) ...
        if data.get('work_center_id'):
            try:
                _set_attr_if_exists(te, 'work_center_id', int(data.get('work_center_id')))
            except Exception:
                _set_attr_if_exists(te, 'work_center_id', data.get('work_center_id'))
        if data.get('production_order_id'):
            try:
                _set_attr_if_exists(te, 'production_order_id', int(data.get('production_order_id')))
            except Exception:
                _set_attr_if_exists(te, 'production_order_id', data.get('production_order_id'))
        if data.get('employee_id'):
            try:
                _set_attr_if_exists(te, 'employee_id', int(data.get('employee_id')))
            except Exception:
                _set_attr_if_exists(te, 'employee_id', data.get('employee_id'))
        meta = data.get('meta')
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except Exception:
                meta = {"raw": meta}
        _set_attr_if_exists(te, 'meta', meta)
        db.session.add(te)
        db.session.commit()
        return jsonify(ok=True, id=getattr(te, 'id', None)), 201
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass
        app.logger.exception("Failed to create TaskEvent")
        return jsonify(ok=False, error="Failed to create task event"), 500


@app.route('/api/task_events', methods=['GET'])
@login_required
def api_list_task_events():
    """List task events (paginated)."""
    from models import TaskEvent
    try:
        page = max(1, int(request.args.get('page', 1)))
        per_page = min(200, max(10, int(request.args.get('per_page', 50))))
    except Exception:
        page, per_page = 1, 50
    q = db.session.query(TaskEvent)
    tt = request.args.get('task_type')
    if tt:
        q = q.filter(TaskEvent.task_type == tt)
    total = q.count()
    rows = q.order_by(TaskEvent.timestamp.desc()).offset((page - 1) * per_page).limit(per_page).all()
    out = []
    for r in rows:
        # tolerant duration read: check multiple possible field names
        dur = None
        for fld in ("duration_seconds", "duration", "duration_sec", "length_seconds", "seconds"):
            try:
                if hasattr(r, fld):
                    dur = getattr(r, fld)
                    if dur is not None:
                        dur = float(dur)
                        break
            except Exception:
                continue
        # if not on a column, check meta
        if dur is None:
            try:
                meta = getattr(r, "meta", None)
                if isinstance(meta, dict):
                    if "duration_seconds" in meta:
                        dur = float(meta.get("duration_seconds"))
            except Exception:
                dur = None
        out.append({
            "id": getattr(r, "id", None),
            "task_id": getattr(r, "task_id", None),
            "task_type": getattr(r, "task_type", None),
            "event_type": getattr(r, "event_type", None),
            "timestamp": getattr(r, "timestamp").isoformat() if getattr(r, "timestamp", None) else None,
            "duration_seconds": dur if dur is not None else 0.0,
            "work_center_id": getattr(r, "work_center_id", None),
            "production_order_id": getattr(r, "production_order_id", None),
            "employee_id": getattr(r, "employee_id", None),
            "meta": getattr(r, "meta", None)
        })
    return jsonify(ok=True, total=total, page=page, per_page=per_page, events=out)


@app.route('/api/task_events/analysis', methods=['POST'])
@login_required
def api_task_events_analysis():
    """
    Compute simple time-task metrics for a date range and optional task types.
    Returns per-task-type aggregates and work-center utilization.
    """
    from models import TaskEvent
    try:
        params = request.get_json(silent=True) or request.form or {}
        date_from_raw = params.get('date_from')
        date_to_raw = params.get('date_to')
        task_types = params.get('task_types')
        if isinstance(task_types, str):
            task_types = [t.strip() for t in task_types.split(',') if t.strip()]
        try:
            date_to = datetime.fromisoformat(date_to_raw).date() if date_to_raw else datetime.utcnow().date()
        except Exception:
            date_to = datetime.utcnow().date()
        try:
            date_from = datetime.fromisoformat(date_from_raw).date() if date_from_raw else (date_to - timedelta(days=30))
        except Exception:
            date_from = date_to - timedelta(days=30)
        start_dt = datetime.combine(date_from, datetime.min.time())
        end_dt = datetime.combine(date_to, datetime.max.time())

        q = db.session.query(TaskEvent).filter(TaskEvent.timestamp >= start_dt, TaskEvent.timestamp <= end_dt)
        if task_types:
            q = q.filter(TaskEvent.task_type.in_(task_types))
        rows = q.all()

        # Aggregate
        stats = {}
        by_wc = {}
        all_durations = []
        for r in rows:
            tt = getattr(r, 'task_type', 'generic') or 'generic'
            dur = getattr(r, 'duration_seconds', None)
            st = stats.setdefault(tt, {"count": 0, "durations": []})
            st["count"] += 1
            if dur is not None:
                st["durations"].append(float(dur))
                all_durations.append(float(dur))
            wc = getattr(r, 'work_center_id', None)
            if wc and dur:
                by_wc.setdefault(wc, 0.0)
                by_wc[wc] += float(dur)

        def summarize(durs):
            if not durs:
                return {"count": 0, "avg": None, "p50": None, "p90": None, "sum": 0.0}
            d = sorted(durs)
            n = len(d)
            s = sum(d)
            avg = s / n
            p50 = d[int(n * 0.5) - 1] if n > 0 else None
            p90 = d[min(n - 1, max(0, int(n * 0.9) - 1))]
            return {"count": n, "avg": avg, "p50": p50, "p90": p90, "sum": s}

        result_stats = {}
        for k, v in stats.items():
            result_stats[k] = summarize(v["durations"])
            result_stats[k]["events"] = v["count"]

        window_seconds = (end_dt - start_dt).total_seconds() or 1.0
        wc_util = []
        for wc, busy in by_wc.items():
            wc_util.append({"work_center_id": wc, "busy_seconds": busy, "util_percent": (busy / window_seconds) * 100.0})

        overall = summarize(all_durations)

        return jsonify(ok=True, window_seconds=window_seconds, overall=overall, by_task_type=result_stats, work_center_utilization=wc_util)
    except Exception:
        app.logger.exception("Task events analysis failed")
        return jsonify(ok=False, error="Server error"), 500


@app.route('/human_resources_overview')
@login_required
def human_resources_overview():
    query = request.args.get("query", "").strip()
    selected_department = request.args.get("department", "").strip()
    selected_status = request.args.get("status", "").strip()

    try:
        employees_query = Employee.query

        if query:
            like_value = f"%{query}%"
            employees_query = employees_query.filter(Employee.name.ilike(like_value))

        if selected_department:
            employees_query = employees_query.filter(Employee.department == selected_department)

        if selected_status:
            employees_query = employees_query.filter(Employee.status == selected_status)

        employees = employees_query.order_by(Employee.id.asc()).all()

        department_rows = (
            db.session.query(Employee.department)
            .filter(Employee.department.isnot(None))
            .distinct()
            .all()
        )
        departments = sorted({dept for (dept,) in department_rows if dept})

        total_employees = db.session.query(func.count(Employee.id)).scalar() or 0
        active_employees = (
            db.session.query(func.count(Employee.id))
            .filter(Employee.status == "Active")
            .scalar()
            or 0
        )
        inactive_employees = max(total_employees - active_employees, 0)

        hr_summary = [
            {"label": "Total Employees", "value": total_employees},
            {"label": "Active Employees", "value": active_employees},
            {"label": "Inactive Employees", "value": inactive_employees},
            {"label": "Departments", "value": len(departments)},
        ]

        alerts = []
        if total_employees == 0:
            alerts.append("No employees found in the system. Add employee records to get started.")
        if inactive_employees > 0:
            alerts.append(f"{inactive_employees} employees are currently marked as inactive.")
        if query and not employees:
            alerts.append("No employees matched your current filters.")

        recent_activity = []
        try:
            recent_logs = AuditLog.query.order_by(AuditLog.id.desc()).limit(5).all()
            for log in recent_logs:
                action = getattr(log, "action", "HR activity")
                resource_type = getattr(log, "resource_type", None)
                resource_id = getattr(log, "resource_id", None)
                parts = [action]
                if resource_type:
                    parts.append(resource_type)
                if resource_id:
                    parts.append(f"(ID {resource_id})")
                recent_activity.append(" ".join(parts))
        except Exception:
            recent_activity = []

        status_counts = (
            db.session.query(Employee.status, func.count(Employee.id))
            .group_by(Employee.status)
            .all()
        )
        if status_counts:
            labels = [label or "Unspecified" for label, _ in status_counts]
            values = [count for _, count in status_counts]
        else:
            labels = ["No Data"]
            values = [1]

        hr_chart_data = {
            "data": [
                {
                    "type": "pie",
                    "labels": labels,
                    "values": values,
                    "hole": 0.35,
                    "textinfo": "label+percent",
                }
            ],
            "layout": {
                "margin": {"l": 10, "r": 10, "t": 30, "b": 10},
                "showlegend": True,
            },
        }
        hr_chart_data_json = json.dumps(hr_chart_data, cls=PlotlyJSONEncoder)

    except (OperationalError, DataError):
        fallback_employees = [
            {
                "id": 1,
                "name": "Alice Banda",
                "department": "HR",
                "role": "HR Manager",
                "email": "alice.banda@example.com",
                "phone": "+260-971-000-001",
                "status": "Active",
            },
            {
                "id": 2,
                "name": "Brian Mwale",
                "department": "Finance",
                "role": "Accountant",
                "email": "brian.mwale@example.com",
                "phone": "+260-971-000-002",
                "status": "Active",
            },
            {
                "id": 3,
                "name": "Chipo Zulu",
                "department": "Logistics",
                "role": "Coordinator",
                "email": "chipo.zulu@example.com",
                "phone": "+260-971-000-003",
                "status": "Inactive",
            },
        ]

        def _matches_filters(record):
            if query and query.lower() not in record["name"].lower():
                return False
            if selected_department and record["department"] != selected_department:
                return False
            if selected_status and record["status"] != selected_status:
                return False
            return True

        filtered_records = [rec for rec in fallback_employees if _matches_filters(rec)]
        employees = [SimpleNamespace(**rec) for rec in filtered_records]

        departments = sorted({rec["department"] for rec in fallback_employees if rec.get("department")})

        total_employees = len(fallback_employees)
        active_employees = len([rec for rec in fallback_employees if rec["status"] == "Active"])
        inactive_employees = total_employees - active_employees

        hr_summary = [
            {"label": "Total Employees", "value": total_employees},
            {"label": "Active Employees", "value": active_employees},
            {"label": "Inactive Employees", "value": inactive_employees},
            {"label": "Departments", "value": len(departments)},
        ]

        alerts = ["Showing fallback HR data because the primary data source is unavailable."]
        if not employees:
            alerts.append("No employees matched your current filters in the fallback dataset.")

        recent_activity = ["Fallback: Unable to load HR activity because the database query failed."]

        status_totals = {}
        for rec in fallback_employees:
            status_totals[rec["status"]] = status_totals.get(rec["status"], 0) + 1
        labels = list(status_totals.keys()) or ["No Data"]
        values = list(status_totals.values()) or [1]

        hr_chart_data = {
            "data": [
                {
                    "type": "pie",
                    "labels": labels,
                    "values": values,
                    "hole": 0.35,
                    "textinfo": "label+percent",
                }
            ],
            "layout": {
                "margin": {"l": 10, "r": 10, "t": 30, "b": 10},
                "showlegend": True,
            },
        }
        hr_chart_data_json = json.dumps(hr_chart_data, cls=PlotlyJSONEncoder)

    return render_template(
        "human-resources-overview.html",
        employees=employees,
        hr_summary=hr_summary,
        alerts=alerts,
        departments=departments,
        selected_department=selected_department,
        selected_status=selected_status,
        query=query,
        recent_activity=recent_activity,
        hr_chart_data=hr_chart_data_json,
    )

#PROCUREMENT

@app.route('/procurement-overview')
def procurement_overview():
    """
    Load procurement overview from PostgreSQL tables (PurchaseRequest, PurchaseOrder, Supplier).
    Falls back to file-based JSON when DB access fails.
    """
    try:
        def safe_get(row, *candidates, default=None):
            for c in candidates:
                if hasattr(row, c):
                    try:
                        val = getattr(row, c)
                        if hasattr(val, "isoformat"):
                            return val.isoformat()
                        return val
                    except Exception:
                        continue
            return default

        pr_rows = db.session.query(PurchaseRequest).order_by(getattr(PurchaseRequest, "id", PurchaseRequest)).all()
        po_rows = db.session.query(PurchaseOrder).order_by(getattr(PurchaseOrder, "id", PurchaseOrder)).all()
        supplier_rows = db.session.query(Supplier).order_by(getattr(Supplier, "id", Supplier)).all()

        purchase_requests = []
        for r in pr_rows:
            purchase_requests.append({
                "id": safe_get(r, "id", "request_id"),
                "request_id": safe_get(r, "request_id", "id"),
                "requested_by": safe_get(r, "requested_by", "requested_by_name", default=""),
                "date": safe_get(r, "date", "requested_on", "created_at", default=""),
                "item": safe_get(r, "item", "description", default=""),
                "quantity": float(safe_get(r, "quantity", "qty", default=0) or 0),
                "status": safe_get(r, "status", "state", default="")
            })

        purchase_orders = []
        for r in po_rows:
            purchase_orders.append({
                "id": safe_get(r, "id", "order_id"),
                "order_id": safe_get(r, "order_id", "id"),
                "vendor": safe_get(r, "vendor", "supplier", default=""),
                "date": safe_get(r, "date", "order_date", "created_at", default=""),
                "item": safe_get(r, "item", "description", default=""),
                "quantity": float(safe_get(r, "quantity", "qty", default=0) or 0),
                "amount": float(safe_get(r, "amount", "total", default=0) or 0),
                "status": safe_get(r, "status", "state", default="")
            })

        suppliers = []
        for s in supplier_rows:
            suppliers.append({
                "id": safe_get(s, "id"),
                "name": safe_get(s, "name", default=""),
                "contact_person": safe_get(s, "contact_person", "contact", default=""),
                "email": safe_get(s, "email", default=""),
                "phone": safe_get(s, "phone", default=""),
                "status": safe_get(s, "status", default="")
            })

        # --- NEW ALERTS LOGIC ---
        alerts = []
        
        # 1. Pending Requests Alert
        pending_requests = sum(1 for r in purchase_requests if r.get('status', '').lower() == 'pending')
        if pending_requests > 0:
            alerts.append(f"Action Required: {pending_requests} purchase request(s) are waiting for approval.")

        # 2. Late Delivery Alert (Simple check against current date)
        today_str = datetime.now().strftime('%Y-%m-%d')
        late_orders = 0
        for po in purchase_orders:
            # Assuming 'date' is order date, real ERPs would have 'expected_delivery_date'
            # Here we just check if status is 'Pending' and order is older than 7 days (example)
            po_date = po.get('date', '')
            po_status = po.get('status', '').lower()
            if po_status == 'pending' and po_date < today_str:
                # This is a simplification; ideally check specific delivery deadlines
                pass 
        
        # 3. Supplier Status Alert
        inactive_suppliers = sum(1 for s in suppliers if s.get('status', '').lower() in ['inactive', 'blacklisted'])
        if inactive_suppliers > 0:
            alerts.append(f"Notice: {inactive_suppliers} supplier(s) are marked as Inactive/Blacklisted.")


        rq_q = (request.args.get('request_query') or '').strip().lower()
        rq_status = (request.args.get('request_status') or '').strip()
        ord_q = (request.args.get('order_query') or '').strip().lower()
        ord_status = (request.args.get('order_status') or '').strip()
        sup_q = (request.args.get('supplier_query') or '').strip().lower()
        sup_status = (request.args.get('supplier_status') or '').strip()

        def matches(item, q, status, text_fields):
            if status and str(item.get('status','')).strip() != status:
                return False
            if not q:
                return True
            ql = q
            for f in text_fields:
                v = str(item.get(f, '') or '').lower()
                if ql in v:
                    return True
            return False

        if isinstance(purchase_requests, list):
            purchase_requests = [
                r for r in purchase_requests
                if matches(r, rq_q, rq_status, ['request_id','requested_by','item'])
            ]
        if isinstance(purchase_orders, list):
            purchase_orders = [
                o for o in purchase_orders
                if matches(o, ord_q, ord_status, ['order_id','vendor','item'])
            ]
        if isinstance(suppliers, list):
            suppliers = [
                s for s in suppliers
                if matches(s, sup_q, sup_status, ['name','contact_person','email'])
            ]

        return render_template(
            'procurement-overview.html',
            purchase_requests=purchase_requests,
            purchase_orders=purchase_orders,
            suppliers=suppliers,
            request_query=rq_q, request_status=rq_status,
            order_query=ord_q, order_status=ord_status,
            supplier_query=sup_q, supplier_status=sup_status,
            alerts=alerts
        )

    except Exception:
        app.logger.exception("DB unavailable for procurement overview")


@app.route('/add_purchase_request', methods=['GET', 'POST'])
def add_purchase_request():
    if request.method == 'POST':
        requested_by = request.form.get('requested_by', '').strip()
        date_val = request.form.get('date', '').strip()
        item = request.form.get('item', '').strip()
        quantity = request.form.get('quantity', '').strip()
        status = request.form.get('status', '').strip()
        request_id = request.form.get('request_id', '').strip()

        # Try DB insert first
        try:
            pr = PurchaseRequest()
            _set_attr_if_exists(pr, "request_id", request_id)
            _set_attr_if_exists(pr, "requested_by", requested_by)
            _set_attr_if_exists(pr, "requested_by_name", requested_by)
            _set_attr_if_exists(pr, "date", date_val, date_try=True)
            _set_attr_if_exists(pr, "item", item)
            _set_attr_if_exists(pr, "description", item)
            _set_attr_if_exists(pr, "quantity", quantity, cast_float=True)
            _set_attr_if_exists(pr, "qty", quantity, cast_float=True)
            _set_attr_if_exists(pr, "status", status)
            db.session.add(pr)
            db.session.commit()
            flash('Purchase request added!', 'success')
            try:
                log_audit(action="create", resource_type="PurchaseRequest", resource_id=getattr(pr, "id", None), after=pr)
            except Exception:
                app.logger.debug("Audit log failed for add_purchase_request", exc_info=True)
            return redirect(url_for('procurement_overview'))
        except Exception:
            db.session.rollback()
            app.logger.exception("Failed to add purchase request to DB; falling back to file")
            return redirect(url_for('procurement_overview'))
        
    return render_template('add_purchase_request.html')

@app.route('/add_purchase_order', methods=['GET', 'POST'])
def add_purchase_order():
    if request.method == 'POST':
        order_id = request.form.get('order_id', '').strip()
        vendor = request.form.get('vendor', '').strip()
        date_val = request.form.get('date', '').strip()
        item = request.form.get('item', '').strip()
        quantity = request.form.get('quantity', '').strip()
        amount = request.form.get('amount', '').strip()
        status = request.form.get('status', '').strip()

        try:
            po = PurchaseOrder()
            _set_attr_if_exists(po, "order_id", order_id)
            _set_attr_if_exists(po, "vendor", vendor)
            _set_attr_if_exists(po, "supplier", vendor)
            _set_attr_if_exists(po, "date", date_val, date_try=True)
            _set_attr_if_exists(po, "item", item)
            _set_attr_if_exists(po, "quantity", quantity, cast_float=True)
            _set_attr_if_exists(po, "amount", amount, cast_float=True)
            _set_attr_if_exists(po, "total", amount, cast_float=True)
            _set_attr_if_exists(po, "status", status)
            db.session.add(po)
            db.session.commit()
            flash('Purchase order added!', 'success')
            return redirect(url_for('procurement_overview'))
        except Exception:
            db.session.rollback()
            app.logger.exception("Failed to add purchase order to DB; falling back to file")
            return redirect(url_for('procurement_overview'))
        
    return render_template('add_purchase_order.html')

@app.route('/add_supplier', methods=['GET', 'POST'])
def add_supplier():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        contact_person = request.form.get('contact_person', '').strip()
        email = request.form.get('email', '').strip()
        phone = request.form.get('phone', '').strip()
        status = request.form.get('status', '').strip()

        try:
            s = Supplier()
            _set_attr_if_exists(s, "name", name)
            _set_attr_if_exists(s, "contact_person", contact_person)
            _set_attr_if_exists(s, "contact", contact_person)
            _set_attr_if_exists(s, "email", email)
            _set_attr_if_exists(s, "phone", phone)
            _set_attr_if_exists(s, "status", status)
            db.session.add(s)
            db.session.commit()
            flash('Supplier added!', 'success')
            return redirect(url_for('procurement_overview'))
        except Exception:
            db.session.rollback()
            app.logger.exception("Failed to add supplier to DB; falling back to file")
            return redirect(url_for('procurement_overview'))
    
    return render_template('add_supplier.html')

@app.route('/supplier/update_status/<int:id>', methods=['POST'])
@login_required
def update_supplier_status(id):
    if 'Supplier' not in globals():
        flash("Supplier model not available.", "error")
        return redirect(url_for('procurement_overview'))

    try:
        sup = db.session.get(Supplier, id)
        if not sup:
            flash("Supplier not found.", "error")
            return redirect(url_for('procurement_overview'))

        new_status = (request.form.get('status') or '').strip()
        if new_status:
            before = sup.to_dict() if hasattr(sup, "to_dict") else None
            if hasattr(sup, "status"):
                setattr(sup, "status", new_status)
            elif hasattr(sup, "state"):
                setattr(sup, "state", new_status)
            db.session.add(sup)
            db.session.commit()
            flash(f"Supplier status updated to {new_status}.", "success")
            try:
                log_audit(action="update", resource_type="Supplier", resource_id=getattr(sup, "id", None), before=before, after=sup)
            except Exception:
                app.logger.debug("Audit log failed for update_supplier_status", exc_info=True)
        else:
            flash("No status selected.", "warning")
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass
        app.logger.exception("Failed to update supplier status")
        flash("Failed to update supplier status.", "error")

    return redirect(url_for('procurement_overview'))

PRODUCTION_DATA_FILE = 'static/js/test_production_data.json'

def load_production_data():
    with open(PRODUCTION_DATA_FILE, 'r') as f:
        return json.load(f)

def save_production_data(data):
    with open(PRODUCTION_DATA_FILE, 'w') as f:
        json.dump(data, f, indent=2)


@app.route('/add_production_order', methods=['GET', 'POST'])
@login_required
def add_production_order():
    """
    Create a production order. Validate inputs and ensure order_id is always non-null.
    Generates a fallback order_id when the form does not provide one.
    """
    if request.method == 'POST':
        raw_order_id = (request.form.get('order_id') or '').strip()
        product = (request.form.get('product') or '').strip()
        raw_quantity = (request.form.get('quantity') or '').strip()
        raw_start = (request.form.get('start_date') or '').strip()
        raw_end = (request.form.get('end_date') or '').strip()
        status = (request.form.get('status') or '').strip()

        # basic validation: require either product or an order_id
        if not raw_order_id and not product:
            flash('Please provide at least an Order ID or Product name.', 'error')
            return redirect(url_for('production_overview'))

        def to_float(v):
            if v is None or v == '':
                return None
            try:
                return float(v)
            except Exception:
                return None

        def to_dt(v):
            if not v or v == '':
                return None
            try:
                return datetime.fromisoformat(v)
            except Exception:
                try:
                    return datetime.strptime(v, "%Y-%m-%d")
                except Exception:
                    return None

        try:
            from models import ProductionOrder
            p = ProductionOrder()

            # set order_id if provided (try numeric first)
            if raw_order_id:
                try:
                    _set_attr_if_exists(p, 'order_id', int(raw_order_id))
                except Exception:
                    _set_attr_if_exists(p, 'order_id', raw_order_id)
            else:
                # attempt to derive next numeric order_id if column numeric
                next_oid = None
                try:
                    max_oid = db.session.query(func.max(getattr(ProductionOrder, "order_id"))).scalar()
                    if max_oid is None:
                        next_oid = 1
                    else:
                        try:
                            next_oid = int(max_oid) + 1
                        except Exception:
                            next_oid = None
                except Exception:
                    next_oid = None

                if next_oid is not None:
                    _set_attr_if_exists(p, 'order_id', next_oid)
                else:
                    import uuid
                    fallback = f"PO-{datetime.utcnow().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"
                    _set_attr_if_exists(p, 'order_id', fallback)

            # set other fields only when present
            if product:
                _set_attr_if_exists(p, 'product', product)
                _set_attr_if_exists(p, 'product_name', product)
            qty = to_float(raw_quantity)
            if qty is not None:
                _set_attr_if_exists(p, 'quantity', qty)
                _set_attr_if_exists(p, 'qty', qty)
            sd = to_dt(raw_start)
            if sd is not None:
                _set_attr_if_exists(p, 'start_date', sd)
            ed = to_dt(raw_end)
            if ed is not None:
                _set_attr_if_exists(p, 'end_date', ed)
            if status:
                _set_attr_if_exists(p, 'status', status)

            # final safety: ensure order_id is not null before insert
            if getattr(p, 'order_id', None) in (None, ''):
                import uuid
                _set_attr_if_exists(p, 'order_id', f"PO-{uuid.uuid4().hex[:8].upper()}")

            db.session.add(p)
            db.session.commit()

            # emit task events if helper present (best-effort)
            try:
                _emit_task_event(
                    task_type="production_order",
                    event_type="created",
                    task_id=getattr(p, "order_id", None) or getattr(p, "id", None),
                    production_order_id=getattr(p, "id", None),
                    meta={"product": getattr(p, "product", None), "status": getattr(p, "status", None)}
                )
            except Exception:
                app.logger.debug("Could not emit TaskEvent for production order", exc_info=True)

            flash('Production order added.', 'success')
            return redirect(url_for('production_overview'))

        except Exception:
            db.session.rollback()
            app.logger.exception("Failed to add production order to DB")
            flash('Failed to add production order.', 'error')
            return redirect(url_for('production_overview'))

    # GET
    return render_template('add_production_order.html')


@app.route('/add_bom', methods=['GET', 'POST'])
@login_required
def add_bom():
    """
    Add a Bill of Materials entry. Uses flexible attribute setter to match model column names.
    """
    if request.method == 'POST':
        product = request.form.get('product', '').strip()
        component = request.form.get('component', '').strip()
        quantity_required = request.form.get('quantity_required', '').strip()
        unit = request.form.get('unit', '').strip()

        try:
            bom = BillOfMaterials()
            _set_attr_if_exists(bom, "product", product)
            _set_attr_if_exists(bom, "product_name", product)
            _set_attr_if_exists(bom, "name", product)

            _set_attr_if_exists(bom, "component", component)
            _set_attr_if_exists(bom, "component_name", component)
            _set_attr_if_exists(bom, "part", component)

            _set_attr_if_exists(bom, "quantity_required", quantity_required, cast_float=True)
            _set_attr_if_exists(bom, "quantity", quantity_required, cast_float=True)
            _set_attr_if_exists(bom, "qty", quantity_required, cast_float=True)

            _set_attr_if_exists(bom, "unit", unit)
            _set_attr_if_exists(bom, "uom", unit)

            db.session.add(bom)
            db.session.commit()
            flash('BOM entry added!', 'success')
            return redirect(url_for('production_overview'))
        except Exception:
            db.session.rollback()
            app.logger.exception("Failed to add BOM to DB")
            flash('Failed to add BOM.', 'error')
            return redirect(url_for('production_overview'))

    return render_template('add_bom.html')

@app.route('/update_work_center', methods=['GET', 'POST'])
@login_required
def update_work_center():
    """
    Render form to update a work center (select by name) and apply changes to the DB.
    Falls back to PRODUCTION_DATA_FILE when DB is unavailable.
    """
    # load work centers for the select box
    try:
        wc_rows = db.session.query(WorkCenter).order_by(getattr(WorkCenter, "id", WorkCenter)).all()
        work_centers = [{"id": getattr(w, "id", None), "name": getattr(w, "name", "")} for w in wc_rows]
    except Exception:
        app.logger.exception("DB unavailable for update_work_center; using file fallback")
        data = load_production_data()
        work_centers = data.get("work_centers", [])

    if request.method == "POST":
        name = request.form.get("name")
        current_task = request.form.get("current_task", "")
        status = request.form.get("status", "")
        operator = request.form.get("operator", "")

        updated = False
        # Try DB update first
        try:
            wc = db.session.query(WorkCenter).filter_by(name=name).first()
            if wc:
                _set_attr_if_exists(wc, "current_task", current_task)
                _set_attr_if_exists(wc, "task", current_task)
                _set_attr_if_exists(wc, "status", status)
                _set_attr_if_exists(wc, "state", status)
                _set_attr_if_exists(wc, "operator", operator)
                _set_attr_if_exists(wc, "assigned_to", operator)
                db.session.add(wc)
                db.session.commit()
                flash("Work center updated.", "success")
                updated = True
        except Exception:
            db.session.rollback()
            app.logger.exception("Failed to update WorkCenter in DB")

        # Fallback to file-based update
        if not updated:
            try:
                data = load_production_data()
                wcs = data.get("work_centers", [])
                for item in wcs:
                    # match by name or id string
                    if (item.get("name") and item.get("name") == name) or (str(item.get("id")) == str(name)):
                        item["current_task"] = current_task
                        item["status"] = status
                        item["operator"] = operator
                        break
                data["work_centers"] = wcs
                save_production_data(data)
                flash("Work center updated (file fallback).", "success")
            except Exception:
                app.logger.exception("Failed to update work center in file fallback")
                flash("Failed to update work center.", "error")

        return redirect(url_for("production_overview"))

    return render_template("update_work_center.html", work_centers=work_centers)


@app.route('/production/edit/<int:id>', methods=['GET', 'POST'])
@login_required
def edit_production_order(id):
    if 'ProductionOrder' not in globals():
        flash("ProductionOrder model not available.", "error")
        return redirect(url_for('production_overview'))
    po = db.session.get(ProductionOrder, id)
    if not po:
        flash("Production order not found.", "error")
        return redirect(url_for('production_overview'))

    if request.method == 'POST':
        try:
            form = request.form
            _set_attr_if_exists(po, "order_id", form.get("order_id"))
            _set_attr_if_exists(po, "product", form.get("product"))
            _set_attr_if_exists(po, "product_name", form.get("product"))
            _set_attr_if_exists(po, "quantity", form.get("quantity"), cast_float=True)
            _set_attr_if_exists(po, "qty", form.get("quantity"), cast_float=True)
            _set_attr_if_exists(po, "start_date", form.get("start_date"), date_try=True)
            _set_attr_if_exists(po, "end_date", form.get("end_date"), date_try=True)
            _set_attr_if_exists(po, "status", form.get("status"))
            db.session.add(po)
            db.session.commit()
            try:
                log_audit(action="update", resource_type="ProductionOrder", resource_id=getattr(po, "id", None), after=po)
            except Exception:
                app.logger.debug("Audit log failed for edit_production_order", exc_info=True)
            flash("Production order updated.", "success")
        except Exception:
            db.session.rollback()
            app.logger.exception("Failed to update production order")
            flash("Failed to update production order.", "error")
        return redirect(url_for('production_overview'))

    # GET: prepare dict for template; render edit page if exists
    po_dict = {
        "id": getattr(po, "id", None),
        "order_id": getattr(po, "order_id", None) or getattr(po, "id", None),
        "product": getattr(po, "product", "") or getattr(po, "product_name", "") or "",
        "quantity": getattr(po, "quantity", "") or getattr(po, "qty", ""),
        "start_date": getattr(po, "start_date", "") or "",
        "end_date": getattr(po, "end_date", "") or "",
        "status": getattr(po, "status", "") or getattr(po, "state", "") or ""
    }
    # render edit template if available, otherwise redirect back
    try:
        return render_template('edit_production_order.html', order=po_dict)
    except Exception:
        return redirect(url_for('production_overview'))


@app.route('/production/delete/<int:id>', methods=['POST'])
@login_required
def delete_production_order(id):
    if 'ProductionOrder' not in globals():
        flash("ProductionOrder model not available.", "error")
        return redirect(url_for('production_overview'))
    po = db.session.get(ProductionOrder, id)
    if not po:
        flash("Production order not found.", "error")
        return redirect(url_for('production_overview'))
    try:
        before = po.to_dict() if hasattr(po, "to_dict") else None
        db.session.delete(po)
        db.session.commit()
        try:
            log_audit(action="delete", resource_type="ProductionOrder", resource_id=id, before=before)
        except Exception:
            app.logger.debug("Audit log failed for delete_production_order", exc_info=True)
        flash("Production order deleted.", "success")
    except Exception:
        db.session.rollback()
        app.logger.exception("Failed to delete production order")
        flash("Failed to delete production order.", "error")
    return redirect(url_for('production_overview'))


@app.route('/bom/edit/<int:id>', methods=['GET', 'POST'])
@login_required
def edit_bom(id):
    if 'BillOfMaterials' not in globals():
        flash("BillOfMaterials model not available.", "error")
        return redirect(url_for('production_overview'))
    bom = db.session.get(BillOfMaterials, id)
    if not bom:
        flash("BOM entry not found.", "error")
        return redirect(url_for('production_overview'))

    if request.method == 'POST':
        try:
            form = request.form
            _set_attr_if_exists(bom, "product", form.get("product"))
            _set_attr_if_exists(bom, "product_name", form.get("product"))
            _set_attr_if_exists(bom, "component", form.get("component"))
            _set_attr_if_exists(bom, "component_name", form.get("component"))
            _set_attr_if_exists(bom, "quantity_required", form.get("quantity_required"), cast_float=True)
            _set_attr_if_exists(bom, "qty", form.get("quantity_required"), cast_float=True)
            _set_attr_if_exists(bom, "unit", form.get("unit"))
            db.session.add(bom)
            db.session.commit()
            try:
                log_audit(action="update", resource_type="BillOfMaterials", resource_id=getattr(bom, "id", None), after=bom)
            except Exception:
                app.logger.debug("Audit log failed for edit_bom", exc_info=True)
            flash("BOM updated.", "success")
        except Exception:
            db.session.rollback()
            app.logger.exception("Failed to update BOM")
            flash("Failed to update BOM.", "error")
        return redirect(url_for('production_overview'))

    bom_dict = {
        "id": getattr(bom, "id", None),
        "product": getattr(bom, "product", "") or getattr(bom, "product_name", ""),
        "component": getattr(bom, "component", "") or getattr(bom, "component_name", ""),
        "quantity_required": getattr(bom, "quantity_required", "") or getattr(bom, "quantity", "") or getattr(bom, "qty", ""),
        "unit": getattr(bom, "unit", "") or getattr(bom, "uom", "")
    }
    try:
        return render_template('edit_bom.html', bom=bom_dict)
    except Exception:
        return redirect(url_for('production_overview'))


@app.route('/bom/delete/<int:id>', methods=['POST'])
@login_required
def delete_bom(id):
    if 'BillOfMaterials' not in globals():
        flash("BillOfMaterials model not available.", "error")
        return redirect(url_for('production_overview'))
    bom = db.session.get(BillOfMaterials, id)
    if not bom:
        flash("BOM entry not found.", "error")
        return redirect(url_for('production_overview'))
    try:
        before = bom.to_dict() if hasattr(bom, "to_dict") else None
        db.session.delete(bom)
        db.session.commit()
        try:
            log_audit(action="delete", resource_type="BillOfMaterials", resource_id=id, before=before)
        except Exception:
            app.logger.debug("Audit log failed for delete_bom", exc_info=True)
        flash("BOM entry deleted.", "success")
    except Exception:
        db.session.rollback()
        app.logger.exception("Failed to delete BOM")
        flash("Failed to delete BOM.", "error")
    return redirect(url_for('production_overview'))


@app.route('/workcenter/edit/<int:id>', methods=['GET', 'POST'])
@login_required
def edit_work_center(id):
    if 'WorkCenter' not in globals():
        flash("WorkCenter model not available.", "error")
        return redirect(url_for('production_overview'))
    wc = db.session.get(WorkCenter, id)
    if not wc:
        flash("Work center not found.", "error")
        return redirect(url_for('production_overview'))

    if request.method == 'POST':
        try:
            form = request.form
            _set_attr_if_exists(wc, "name", form.get("name"))
            _set_attr_if_exists(wc, "current_task", form.get("current_task"))
            _set_attr_if_exists(wc, "task", form.get("current_task"))
            _set_attr_if_exists(wc, "status", form.get("status"))
            _set_attr_if_exists(wc, "state", form.get("status"))
            _set_attr_if_exists(wc, "operator", form.get("operator"))
            _set_attr_if_exists(wc, "assigned_to", form.get("operator"))
            db.session.add(wc)
            db.session.commit()
            try:
                log_audit(action="update", resource_type="WorkCenter", resource_id=getattr(wc, "id", None), after=wc)
            except Exception:
                app.logger.debug("Audit log failed for edit_work_center", exc_info=True)
            flash("Work center updated.", "success")
        except Exception:
            db.session.rollback()
            app.logger.exception("Failed to update work center")
            flash("Failed to update work center.", "error")
        return redirect(url_for('production_overview'))

    wc_dict = {
        "id": getattr(wc, "id", None),
        "name": getattr(wc, "name", "") or "",
        "current_task": getattr(wc, "current_task", "") or getattr(wc, "task", "") or "",
        "status": getattr(wc, "status", "") or getattr(wc, "state", "") or "",
        "operator": getattr(wc, "operator", "") or getattr(wc, "assigned_to", "") or ""
    }
    try:
        return render_template('edit_work_center.html', work_center=wc_dict)
    except Exception:
        return redirect(url_for('production_overview'))


@app.route('/workcenter/delete/<int:id>', methods=['POST'])
@login_required
def delete_work_center(id):
    if 'WorkCenter' not in globals():
        flash("WorkCenter model not available.", "error")
        return redirect(url_for('production_overview'))
    wc = db.session.get(WorkCenter, id)
    if not wc:
        flash("Work center not found.", "error")
        return redirect(url_for('production_overview'))
    try:
        before = wc.to_dict() if hasattr(wc, "to_dict") else None
        db.session.delete(wc)
        db.session.commit()
        try:
            log_audit(action="delete", resource_type="WorkCenter", resource_id=id, before=before)
        except Exception:
            app.logger.debug("Audit log failed for delete_work_center", exc_info=True)
        flash("Work center deleted.", "success")
    except Exception:
        db.session.rollback()
        app.logger.exception("Failed to delete work center")
        flash("Failed to delete work center.", "error")
    return redirect(url_for('production_overview'))

@app.route('/production-overview')
def production_overview():
    """
    Load production overview from PostgreSQL tables (ProductionOrder, BillOfMaterials, WorkCenter).
    Falls back to file-based JSON when DB access fails.
    """
    try:
        def safe_get(row, *candidates, default=None):
            for c in candidates:
                if hasattr(row, c):
                    try:
                        val = getattr(row, c)
                        if hasattr(val, "isoformat"):
                            return val.isoformat()
                        return val
                    except Exception:
                        continue
            return default

        prod_rows = db.session.query(ProductionOrder).order_by(getattr(ProductionOrder, "id", ProductionOrder)).all()
        bom_rows = db.session.query(BillOfMaterials).order_by(getattr(BillOfMaterials, "id", BillOfMaterials)).all()
        wc_rows = db.session.query(WorkCenter).order_by(getattr(WorkCenter, "id", WorkCenter)).all()

        production_orders = []
        for r in prod_rows:
            production_orders.append({
                "id": safe_get(r, "id"),
                "order_id": safe_get(r, "order_id", "id"),
                "product": safe_get(r, "product", "product_name", "name"),
                "quantity": float(safe_get(r, "quantity", "qty", "amount", default=0) or 0),
                "start_date": safe_get(r, "start_date", "start"),
                "end_date": safe_get(r, "end_date", "end"),
                "status": safe_get(r, "status", "state", default="")
            })

        bill_of_materials = []
        for r in bom_rows:
            bill_of_materials.append({
                "id": safe_get(r, "id"),
                "product": safe_get(r, "product", "product_name", "name", default=""),
                "component": safe_get(r, "component", "component_name", "part", default=""),
                "quantity_required": float(safe_get(r, "quantity_required", "quantity", "qty", default=0) or 0),
                "unit": safe_get(r, "unit", "uom", default="pcs")
            })

        work_centers = []
        for r in wc_rows:
            work_centers.append({
                "id": safe_get(r, "id"),
                "name": safe_get(r, "name", "work_center", default=""),
                "current_task": safe_get(r, "current_task", "task", default=""),
                "status": safe_get(r, "status", "state", default=""),
                "operator": safe_get(r, "operator", "assigned_to", default=""),
                "capacity": float(safe_get(r, "capacity", default=0) or 0),
                "throughput_per_hour": float(safe_get(r, "throughput_per_hour", "throughput", default=0) or 0)
            })

        # --- NEW ALERTS LOGIC ---
        alerts = []

        # 1. Work Center Maintenance Alert
        down_centers = [wc['name'] for wc in work_centers if wc.get('status', '').lower() in ['maintenance', 'broken', 'inactive']]
        if down_centers:
            alerts.append(f"Critical: The following work centers are down: {', '.join(down_centers)}.")

        # 2. Overdue Production Orders
        today_iso = datetime.now().date().isoformat()
        overdue_orders = 0
        for po in production_orders:
            end_date = po.get('end_date')
            status = po.get('status', '').lower()
            if status not in ['completed', 'cancelled'] and end_date and end_date < today_iso:
                overdue_orders += 1
        
        if overdue_orders > 0:
            alerts.append(f"Warning: {overdue_orders} production order(s) are past their scheduled end date.")
        
        return render_template(
            'production-overview.html',
            production_orders=production_orders,
            bill_of_materials=bill_of_materials,
            work_centers=work_centers,
            alerts=alerts
        )

    except Exception:
        app.logger.exception("DB unavailable for production overview; falling back to file")
        data = load_production_data()
        production_orders = data.get('production_orders', [])
        bill_of_materials = data.get('bill_of_materials', [])
        work_centers = data.get('work_centers', [])
        return render_template(
            'production-overview.html',
            production_orders=production_orders,
            bill_of_materials=bill_of_materials,
            work_centers=work_centers
        )

"""
@app.route('/sales-overview')
async def sales_overview():

    sales_forecast_graph = await analytics.generate_sales_forecast()
    
    return render_template('sales_overview.html',
                           sales_forecast_graph = sales_forecast_graph, 
                           sales_trend_graph = sales_trend_graph, 
                           goods_performance_pie_chart = goods_performance_pie_chart,
                           customer_expenditure_pie_chart = customer_expenditure_pie_chart)
"""

"""
@app.route('/upload', methods=['GET', 'POST'])
def upload_file():
    if 'file' not in request.files:
        return url_for('index')
    file = request.files['file']

    if file.filename == '' or not file.filename.endswith('.csv'):
        return "Invalid file type. Please upload a CSV file."
    
    if file:
        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        
        df = spark.read.csv(filepath, header = True, inferSchema = True, encoding = 'cp1252')
        
        print(df.head())
        
        return "File uploaded and processed successfully!"
"""

"""
@app.route('/sales-data-upload-to-hadoop', methods=['GET', 'POST']) 
def upload_to_hadoop():

    if 'sales-data-file' not in request.files:
        return "No file found for upload"
    
    sales_data_file = request.files['sales-data-file']

    if sales_data_file.filename == '' or not sales_data_file.filename.endswith('.csv'):
        return "Invalid data type selected. Please select a valid file"

    if sales_data_file:
        filename = secure_filename(sales_data_file.filename)
        
        
        temp_dir = os.path.abspath(os.sep) + 'tmp' 
        if not os.path.exists(temp_dir):
            os.makedirs(temp_dir)

        temp_local_path = os.path.join(temp_dir, filename)
        sales_data_file.save(temp_local_path)

        hdfs_upload_dir = 'hdfs://localhost:19000/data/raw_uploads'
        hdfs_upload_path = os.path.join(hdfs_upload_dir, filename).replace('\\', '/')

        # Format the local path for the hadoop command, replacing backslashes with forward slashes
        posix_local_path = temp_local_path.replace(os.sep, '/')
        
        try:
            subprocess.run([hadoop_bin_path, 'fs', '-mkdir', '-p', hdfs_upload_dir], check=True)
            subprocess.run([hadoop_bin_path, 'fs', '-put', '-f', posix_local_path, hdfs_upload_path], check=True)

        except subprocess.CalledProcessError as e:
            return f"Failed to upload file to HDFS: {e}"
        except FileNotFoundError:
            return f"Hadoop executable not found at '{hadoop_bin_path}'. Please check your path.
        
        sales_dataframe = spark.read.csv(hdfs_upload_path, header=True, inferSchema=True, encoding='cp1252')

        partitioned_data_file = sales_dataframe.withColumn('SalesYear', col('OrderDate').substr(7,10))
"""
# Define available groups and roles (add more groups here as needed)
GROUPS = {
    0: "Administrator",
    1: "Pending User",
    2: "Manager",
    3: "Finance",
    4: "HR",
    5: "IT",
    6: "Warehouse/Logistics"
}

# standardized roles list
ROLES = ["pending_user", "manager", "staff"]

@app.route("/admin-users")
@login_required
def admin_users():
    model = getattr(current_user, "model", None)
    is_admin = bool(model and (getattr(model, "role", None) == "admin" or getattr(model, "group_id", None) == 0))
    if not is_admin:
        return render_template("403.html"), 403

    users = db.session.query(User).order_by(User.id).all()

    try:
        per_page = 25
        auth_page = max(1, int(request.args.get('auth_page', 1)))
        audit_page = max(1, int(request.args.get('audit_page', 1)))

        # Auth logs
        auth_total = db.session.query(func.count()).select_from(AuthLog).scalar() or 0
        auth_logs = (
            db.session.query(AuthLog)
            .order_by(getattr(AuthLog, 'created_at', AuthLog.id).desc())
            .limit(per_page)
            .offset((auth_page - 1) * per_page)
            .all()
        )

        # Audit logs
        audit_total = db.session.query(func.count()).select_from(AuditLog).scalar() or 0
        audit_logs = (
            db.session.query(AuditLog)
            .order_by(getattr(AuditLog, 'created_at', AuditLog.id).desc())
            .limit(per_page)
            .offset((audit_page - 1) * per_page)
            .all()
        )
    except Exception:
        # If logging tables or DB are unavailable, fall back to empty lists
        app.logger.debug("Auth/Audit logs not available for admin view", exc_info=True)
        auth_logs = []
        audit_logs = []
        auth_total = 0
        audit_total = 0

    return render_template(
        "admin-users.html",
        users=users,
        groups=GROUPS,
        roles=ROLES,
        auth_logs=auth_logs,
        audit_logs=audit_logs,
        auth_page=auth_page,
        audit_page=audit_page,
        auth_total=auth_total,
        audit_total=audit_total,
        per_page=per_page
    )

@app.route("/admin-users/<int:user_id>/update", methods=["POST"])
@login_required
def admin_update_user(user_id):
    model = getattr(current_user, "model", None)
    is_admin = bool(model and (getattr(model, "role", None) == "admin" or getattr(model, "group_id", None) == 0))
    if not is_admin:
        return render_template("403.html"), 403

    user = db.session.get(User, user_id)
    if not user:
        flash("User not found.", "error")
        return redirect(url_for("admin_users"))

    try:
        # Validate and set group_id
        group_id_raw = request.form.get("group_id")
        if group_id_raw is not None and group_id_raw != "":
            try:
                group_id = int(group_id_raw)
                if group_id not in GROUPS:
                    raise ValueError("Invalid group id")
                setattr(user, "group_id", group_id)
            except ValueError:
                flash("Invalid group selected.", "error")
                return redirect(url_for("admin_users"))

        # Validate and set role
        role = request.form.get("role")
        if role:
            if role not in ROLES:
                flash("Invalid role selected.", "error")
                return redirect(url_for("admin_users"))
            setattr(user, "role", role)

        # is_active checkbox
        is_active = request.form.get("is_active") == "on"
        # Some models may use boolean column or attribute name; handle both
        if hasattr(user, "is_active"):
            setattr(user, "is_active", bool(is_active))
        else:
            setattr(user, "is_active", bool(is_active))

        db.session.add(user)
        db.session.commit()
        flash(f"Updated {getattr(user, 'username', 'user')}.", "success")
        try:
            log_audit(action="update_user", resource_type="User", resource_id=getattr(user, "id", None), after=user)
        except Exception:
            app.logger.debug("Audit log failed for update_user", exc_info=True)
    except Exception as e:
        db.session.rollback()
        app.logger.exception("Failed to update user")
        flash("Failed to update user.", "error")
        flash(str(e))

    return redirect(url_for("admin_users"))


@app.route('/signup', methods=['GET', 'POST'])
def signup():
    """
    Simple signup route for templates/signup.html.
    Creates a user with role 'pending_user'. Uses model fields if present.
    """
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        if not username or not password:
            flash('Username and password are required.', 'error')
            return redirect(url_for('signup'))

        try:
            new_user = User(username=username)
            # prefer model helper if exists
            if hasattr(new_user, 'set_password'):
                new_user.set_password(password)
            else:
                if hasattr(new_user, 'password_hash'):
                    setattr(new_user, 'password_hash', generate_password_hash(password))
                elif hasattr(new_user, 'password'):
                    setattr(new_user, 'password', generate_password_hash(password))
                else:
                    setattr(new_user, 'password_hash', generate_password_hash(password))

            # set defaults where possible
            if hasattr(new_user, 'role'):
                setattr(new_user, 'role', 'pending_user')
            if hasattr(new_user, 'group_id'):
                setattr(new_user, 'group_id', 1)
            if hasattr(new_user, 'is_active'):
                setattr(new_user, 'is_active', True)

            db.session.add(new_user)
            db.session.commit()
            flash('Account created. Please log in.', 'success')
            return redirect(url_for('login'))
        except Exception:
            db.session.rollback()
            app.logger.exception("Signup failed")
            flash('Failed to create account.', 'error')
            return redirect(url_for('signup'))

    return render_template('signup.html')

@app.route('/process_payroll', methods=['GET', 'POST'])
@login_required
def process_payroll():
    """
    Process payroll and persist to PostgreSQL Payroll table when available.
    Falls back to flashing an error if the DB model is not present or commit fails.
    """
    if request.method == 'POST':
        employee = request.form.get('employee', '').strip()
        month = request.form.get('month', '').strip()
        gross_pay = request.form.get('gross_pay', '').strip()
        deductions = request.form.get('deductions', '').strip()
        net_pay = request.form.get('net_pay', '').strip()
        status = request.form.get('status', 'Processed').strip()

        try:
            if 'Payroll' in globals():
                p = Payroll()
                # accept either employee id or name depending on your model
                if employee.isdigit():
                    _set_attr_if_exists(p, "employee_id", int(employee))
                _set_attr_if_exists(p, "employee", employee)
                _set_attr_if_exists(p, "month", month)
                _set_attr_if_exists(p, "gross_pay", gross_pay, cast_float=True)
                _set_attr_if_exists(p, "deductions", deductions, cast_float=True)
                _set_attr_if_exists(p, "net_pay", net_pay, cast_float=True)
                _set_attr_if_exists(p, "status", status)
                # set processed/created timestamp if model supports it
                for ts in ("processed_at", "processed_on", "created_at", "date"):
                    if hasattr(p, ts):
                        _set_attr_if_exists(p, ts, datetime.utcnow().isoformat())
                        break

                db.session.add(p)
                db.session.commit()
                flash('Payroll processed and saved to database.', 'success')
                return redirect(url_for('payroll_overview'))
            else:
                flash('Payroll model is not available. Unable to save to database.', 'error')
        except Exception:
            db.session.rollback()
            app.logger.exception("Failed to insert payroll record into DB")
            flash('Failed to process payroll (database error).', 'error')

        return redirect(url_for('payroll_overview'))

    return render_template('process_payroll.html')


@app.route('/sales-overview')
@login_required
def sales_overview():
    try:
        customers = []
        if 'Customer' in globals():
            try:
                rows = db.session.query(Customer).order_by(getattr(Customer, "name", Customer)).all()
                for c in rows:
                    customers.append({"id": getattr(c, "id", None), "name": getattr(c, "name", "") or ""})
            except Exception:
                app.logger.exception("Failed to load customers for sales_overview")

        inventory = []
        if 'InventoryItem' in globals():
            try:
                inv_rows = db.session.query(InventoryItem).order_by(getattr(InventoryItem, "product", InventoryItem)).all()
                for it in inv_rows:
                    inventory.append({
                        "id": getattr(it, "id", None),
                        "product": getattr(it, "product", None) or getattr(it, "name", "") or f"Item {getattr(it,'id', '')}",
                        "quantity": float(getattr(it, "quantity", 0) or 0),
                        "unit_cost": float(getattr(it, "unit_cost", 0) or 0),
                        "reorder_level": float(getattr(it, "reorder_level", 0) or 0)
                    })
            except Exception:
                app.logger.exception("Failed to load inventory for sales_overview")
            
        alerts = []
        stockouts = [item['product'] for item in inventory if item['quantity'] <= 0]
        if stockouts:
            display_stockouts = stockouts[:3]
            msg = f"Stockout Alert: {', '.join(display_stockouts)}"
            if len(stockouts) > 3:
                msg += f" and {len(stockouts) - 3} others"
            msg += " are out of stock."
            alerts.append(msg)

        #Goods performance
        sales_trend = globals().get("sales_trend_graph", {"data": [], "layout": {}})
        goods_perf = {"data": [], "layout": {}}

        try:
            if 'SalesOrder' in globals() and 'InventoryItem' in globals():
                value_col = getattr(SalesOrder, "amount", None)
                if value_col is not None:
                    inv_label_col = getattr(InventoryItem, "product", None)
                    grouping_col = getattr(SalesOrder, "id", None)
                    rows = (
                        db.session.query(inv_label_col, func.coalesce(func.sum(value_col), 0).label('amount'))
                        .join(SalesOrder, getattr(SalesOrder, "inventory_id") == getattr(InventoryItem, "id"))
                        .group_by(inv_label_col)
                        .order_by(func.sum(value_col).desc())
                        .limit(10)
                        .all()
                    )
                    labels = [str(r[0]) if r[0] is not None else "Unknown" for r in rows]
                    vals = [float(r[1]) for r in rows]
                    if labels and vals:
                        goods_perf = {"data": [{"labels": labels, "values": vals, "type": "pie", "name": "Goods Performance"}],
                                     "layout": {"title": "Goods Performance by Sales"}}
                    else:
                        pass
        except Exception:
            app.logger.exception("Failed to build goods performance chart from DB; falling back to legacy figure")

        cust_expenditure = globals().get("customer_expenditure_pie_chart", {"data": [], "layout": {}})

        kpi = None
        try:
            orders_count = 0
            try:
                orders_count = int(db.session.query(func.count()).select_from(SalesOrder).scalar() or 0)
            except Exception:
                orders_count = len(db.session.query(SalesOrder).all()) if 'SalesOrder' in globals() else 0

            customers_count = 0
            try:
                customers_count = int(db.session.query(func.count()).select_from(Customer).scalar() or 0)
            except Exception:
                customers_count = len(customers)

            total_revenue = 0.0
            try:
                if 'SalesOrder' in globals():
                    value_col = getattr(SalesOrder, "total", None) or getattr(SalesOrder, "amount", None)
                    if value_col is not None:
                        total_revenue = float(db.session.query(func.coalesce(func.sum(value_col), 0)).scalar() or 0.0)
            except Exception:
                app.logger.exception("Failed to compute total_revenue for sales_overview")
                total_revenue = 0.0

            kpi = {
                "total_sales": f"{total_revenue:,.2f}",
                "orders": int(orders_count),
                "customers": int(customers_count),
                "revenue": f"{total_revenue:,.2f}"
            }
        except Exception:
            app.logger.exception("Failed to build KPIs for sales_overview")
            kpi = {"total_sales": "N/A", "orders": "N/A", "customers": "N/A", "revenue": "N/A"}

        # Top customers by revenue
        top_customers = {"data": [], "layout": {"title": "Top Customers"}}
        if 'Customer' in globals() and 'SalesOrder' in globals() and hasattr(SalesOrder, "customer_id"):
            value_col = getattr(SalesOrder, "total", getattr(SalesOrder, "amount", None))
            if value_col is not None:
                rows = (
                    db.session.query(Customer.name, func.coalesce(func.sum(value_col), 0).label('total'))
                    .join(SalesOrder, getattr(SalesOrder, "customer_id") == getattr(Customer, "id"))
                    .group_by(Customer.name)
                    .order_by(func.sum(value_col).desc())
                    .limit(10)
                    .all()
                )
                labels = [r[0] or "Unknown" for r in rows]
                vals = [float(r[1]) for r in rows]
                if labels and vals:
                    top_customers = {"data": [{"labels": labels, "values": vals, "type": "pie", "name": "Customers"}],
                                 "layout": {"title": "Top Customers by Expenditure"}}

        
        return render_template('sales_overview.html',
                               sales_trend_graph=sales_trend,
                               goods_performance_pie_chart=goods_perf,
                               customer_expenditure_pie_chart=cust_expenditure,
                               customers=customers,
                               top_customers=top_customers,
                               inventory=inventory,
                               kpi=kpi,
                               alerts=alerts)
    except Exception:
        app.logger.exception("Failed to render sales_overview")
        return render_template('sales_overview.html',
                               sales_trend_graph={"data": [], "layout": {}},
                               goods_performance_pie_chart={"data": [], "layout": {}},
                               customer_expenditure_pie_chart={"data": [], "layout": {}},
                               customers=[], inventory=[], kpi={"total_sales":"N/A","orders":"N/A","customers":"N/A","revenue":"N/A"})


@app.route('/create_sale', methods=['POST'])
@login_required
def create_sale():
    data = request.get_json(silent=True) or request.form or {}
    try:
        customer_id = int((data.get('customer_id') or 0))
        inventory_id = int((data.get('inventory_id') or 0))
        quantity = float((data.get('quantity') or 0))

    except Exception:
        return jsonify(ok=False, error="Invalid input types"), 400

    if quantity <= 0:
        return jsonify(ok=False, error="Quantity must be greater than zero"), 400

    # load customer
    cust = None
    if 'Customer' in globals():
        try:
            cust = db.session.get(Customer, customer_id)
        except Exception:
            app.logger.exception("Customer lookup failed")
    if cust is None:
        return jsonify(ok=False, error="Customer not found"), 404

    # load inventory item
    item = None
    if 'InventoryItem' in globals():
        try:
            item = db.session.get(InventoryItem, inventory_id)
        except Exception:
            app.logger.exception("Inventory lookup failed")
    if item is None:
        return jsonify(ok=False, error="Inventory item not found"), 404

    # realtime inventory check
    try:
        available = float(getattr(item, "quantity", 0) or 0)
    except Exception:
        available = 0
    if available < quantity:
        return jsonify(ok=False, error="Insufficient stock"), 400

    # optional credit check (best-effort)
    try:
        credit_limit = getattr(cust, "credit_limit", None)
        if credit_limit is not None and 'Invoice' in globals():
            unpaid_total = db.session.query(func.coalesce(func.sum(Invoice.amount), 0)).filter(
                getattr(Invoice, "customer_id") == customer_id,
                getattr(Invoice, "status") != "Paid"
            ).scalar() or 0
            unit_price = float(getattr(item, "unit_cost", 0) or 0)
            order_total = unit_price * quantity
            if (unpaid_total + order_total) > float(credit_limit):
                return jsonify(ok=False, error="Customer credit limit exceeded"), 400
    except Exception:
        db.session.rollback()
        app.logger.exception("Credit check failed; continuing without blocking")

    # Perform DB transaction
    try:
        db.session.rollback()

        total = float(getattr(item, "unit_cost", 0) or 0) * quantity
        so_id = None
        inv_id = None

        so = None
        if 'SalesOrder' in globals():
            so = SalesOrder()
            if hasattr(SalesOrder, "order_id"):
                try:
                    next_oid = db.session.query(func.coalesce(func.max(getattr(SalesOrder, "order_id")), 0) + 1).scalar()
                except Exception:
                    db.session.rollback()
                    next_oid = None
                if not next_oid:
                    next_oid = int(datetime.now().timestamp())
                _set_attr_if_exists(so, "order_id", next_oid)

            _set_attr_if_exists(so, "customer_id", customer_id)
            _set_attr_if_exists(so, "inventory_id", inventory_id)
            _set_attr_if_exists(so, "product", getattr(item, "product", None) or getattr(item, "name", None))
            _set_attr_if_exists(so, "quantity", quantity)
            _set_attr_if_exists(so, "amount", total)
            _set_attr_if_exists(so, "status", "Pending")
            _set_attr_if_exists(so, "date", datetime.now(timezone.utc).date(), date_try=True)
            _set_attr_if_exists(so, "created_at", datetime.now(timezone.utc), date_try=True)
            db.session.add(so)
            db.session.flush() 
            so_id = getattr(so, "id", None)

        # decrement inventory
        new_qty = available - quantity
        try:
            if hasattr(item, "quantity"):
                setattr(item, "quantity", new_qty)
            else:
                _set_attr_if_exists(item, "qty", new_qty)
            db.session.add(item)
            db.session.flush()
        except Exception:
            app.logger.exception("Failed to decrement inventory quantity; aborting")
            raise

        # create invoice
        if 'Invoice' in globals():
            inv = Invoice()
            _set_attr_if_exists(inv, "customer_id", customer_id)
            _set_attr_if_exists(inv, "customer", getattr(cust, "name", None) or customer_id)
            _set_attr_if_exists(inv, "amount", total)
            _set_attr_if_exists(inv, "total", total)
            _set_attr_if_exists(inv, "status", "Unpaid")
            _set_attr_if_exists(inv, "date", datetime.now().date(), date_try=True)
            db.session.add(inv)
            db.session.flush()
            inv_id = getattr(inv, "id", None)

            # link invoice to sales order if possible
            if so is not None:
                _set_attr_if_exists(so, "invoice_id", inv_id)
                db.session.add(so)
                db.session.flush()

        # notify warehouse by creating Shipment record
        if 'Shipment' in globals():
            sh = Shipment()
            # Generate shipment_id
            try:
                shipment_count = db.session.query(Shipment).count()
                _set_attr_if_exists(sh, "shipment_id", shipment_count + 1)
            except Exception:
                app.logger.warning("Could not generate shipment_id from count")

            _set_attr_if_exists(sh, "date", datetime.now().date(), date_try=True)
            _set_attr_if_exists(sh, "carrier", "")
            _set_attr_if_exists(sh, "destination", "")
            _set_attr_if_exists(sh, "status", "Pending")
            _set_attr_if_exists(sh, "order_id", so_id)
            _set_attr_if_exists(sh, "order", so_id)
            _set_attr_if_exists(sh, "customer_id", customer_id)
            db.session.add(sh)

        # finalize
        db.session.commit()
 
        return jsonify(ok=True, order_id=so_id or inv_id), 201
    except Exception as exc:
         # ensure rollback and return error JSON
         try:
             db.session.rollback()
         except Exception:
             pass
         app.logger.exception("Failed to create sale")
         return jsonify(ok=False, error="Failed to create sale due to server error"), 500


@app.route('/sales-forecast')
def sales_forecast():
    try:
        import asyncio
        sales_forecast_graph, forecast_metrics_mean, forecast_metrics_rmse, forecast_metrics_nrmse = asyncio.run(analytics.generate_sales_forecast())

    except Exception:
        app.logger.exception("Failed to generate sales forecast")
        sales_forecast_graph = {"data": [], "layout": {}}
    
    """
    sales_forecast_graph = analytics.generate_forecast()
    """


    return render_template('sales_forecast.html', sales_forecast_graph=sales_forecast_graph, forecast_metrics_mean_=forecast_metrics_mean, forecast_metrics_rmse=forecast_metrics_rmse, forecast_metrics_nrmse=forecast_metrics_nrmse)

if __name__ == '__main__':
     # Ensure DB tables exist (create missing tables from models.py)
    try:
        with app.app_context():
            db.create_all()

            # seed a default admin user if no users exist
            try:
                user_count = 0
                try:
                    user_count = db.session.query(User).count()
               
                except Exception:
                    user_count = 0

                if user_count == 0:
                    admin = User(username='admin')
                    if hasattr(admin, 'email'):
                        setattr(admin, 'email', 'admin@example.com')
                    if hasattr(admin, 'full_name'):
                        setattr(admin, 'full_name', 'Administrator')
                    if hasattr(admin, 'group_id'):
                        setattr(admin, 'group_id', 0)
                    if hasattr(admin, 'role'):
                        setattr(admin, 'role', 'admin')
                    if hasattr(admin, 'is_active'):
                        setattr(admin, 'is_active', True)

                    if hasattr(admin, 'set_password'):
                        admin.set_password('admin123')
                    else:
                        if hasattr(admin, 'password_hash'):
                            setattr(admin, 'password_hash', generate_password_hash('admin123'))
                        elif hasattr(admin, 'password'):
                            setattr(admin, 'password', generate_password_hash('admin123'))
                        else:
                            setattr(admin, 'password_hash', generate_password_hash('admin123'))

                    db.session.add(admin)
                    db.session.commit()
                    print("Created default admin user (username=admin, password=admin123). Change immediately.")
            except Exception as seed_err:
                print("Warning: could not seed admin user:", seed_err)

    except OperationalError as e:
        print("Database not available, skipping automatic table creation:", e)

    app.run(debug=True)