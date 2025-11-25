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
from datetime import datetime
import pdfkit
from sqlalchemy import text, func
from sqlalchemy.exc import OperationalError, DataError
from flask_migrate import Migrate
from routes.assets_upload import bp as assets_upload_bp
from plotly.utils import PlotlyJSONEncoder
import csv
import json
import traceback

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

# Global error handler to persist uncaught exceptions to DB (best-effort)
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

# remove the local shadowing User class (it previously hid models.User)

@login_manager.user_loader
def load_user(user_id):
    """
    Load a user by id. Try numeric-id lookup in DB first, otherwise username lookup.
    If DB is unavailable or lookup fails, fall back to in-memory USERS.
    Returns an AuthUser (wrapping the DB model) or None.
    """
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
goods_performance_pie_chart = analytics.generate_goods_performance_pchart()
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
    """
    Dashboard index: try to load live KPIs from the database, fall back to static JSON file.
    """
    def safe_get(obj, attr, default=None):
        try:
            val = getattr(obj, attr)
            # if it's a column/property InstrumentedAttribute on model instance, getattr returns value
            return val if val is not None else default
        except Exception:
            return default

    try:
        # KPIs
        total_orders = db.session.query(func.count()).select_from(SalesOrder).scalar() or 0
        total_customers = db.session.query(func.count()).select_from(Customer).scalar() or 0

        # Try to sum a sensible revenue column if present
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
            # Prefer a DB join between SalesOrder and Customer for accurate recent orders
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
                # fallback: get recent orders from SalesOrder only
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
                # choose the value column (total or amount)
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
        
        t_customers = []

        top_cust = db.session.query(Customer).all()
        #Total inventory value
        try:
            # prefer DB-side aggregate since columns are known: quantity (int) and unit_cost (float)
            if 'InventoryItem' in globals():
                total_inventory_value = float(
                    db.session.query(
                        func.coalesce(func.sum(InventoryItem.quantity * InventoryItem.unit_cost), 0)
                    ).scalar() or 0.0
                )
                print("Yay")
            else:
                print("nay")
                # fallback: compute in Python row-wise
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
            # Sales trend (monthly totals) from SalesOrder (prefer 'total' then 'amount')
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
                                     "layout": {"title": "Top Customers by Sales"}}


            
            # Product performance: prefer join SalesOrder.inventory_id -> InventoryItem.id to get product names
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
                # Prefer a join from SalesOrder.inventory_id -> InventoryItem.id to get product names
                if 'SalesOrder' in globals() and 'InventoryItem' in globals() and hasattr(SalesOrder, "inventory_id") and hasattr(InventoryItem, "id"):
                    # choose best product/name column on InventoryItem
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
                # fallback: try SalesOrder product-like fields
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
                # final fallback: aggregate by InventoryItem.category if available
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

# Example mock data (replace with database queries)
def get_accounting_alerts():
    return [
        "Invoice INV-1002 is overdue.",
        "Low balance in main account.",
        "Pending approval for payment to Beta Ltd."
    ]

def get_accounting_recent_activity():
    return [
        "Payment of K5,000 made to Acme Corp.",
        "Invoice INV-1003 received from Gamma Inc.",
        "Expense report submitted by Alice Banda."
    ]

def get_accounting_summary():
    return [
        {"label": "Total Revenue", "value": "K120,000"},
        {"label": "Total Expenses", "value": "K85,000"},
        {"label": "Net Profit", "value": "K35,000"},
        {"label": "Outstanding Invoices", "value": "K9,700"}
    ]

def get_cashflow_data():
    return {
        "data": [
            {
                "x": ["2025-06", "2025-07", "2025-08", "2025-09", "2025-10"],
                "y": [12000, 15000, 11000, 17000, 16000],
                "type": "scatter",
                "mode": "lines+markers",
                "name": "Cash Flow"
            }
        ],
        "layout": {
            "title": "Monthly Cash Flow",
            "xaxis": {"title": "Month"},
            "yaxis": {"title": "Amount (USD)"}
        }
    }

def get_top_vendors():
    return [
        {"name": "Acme Corp", "total_paid": 32000},
        {"name": "Beta Ltd", "total_paid": 21000},
        {"name": "Gamma Inc", "total_paid": 18000}
    ]

def get_recent_transactions():
    return [
        {"date": "2025-10-10", "description": "Payment to Acme Corp", "amount": 5000, "type": "Debit"},
        {"date": "2025-10-09", "description": "Invoice from Gamma Inc", "amount": 3500, "type": "Credit"},
        {"date": "2025-10-08", "description": "Salary Payment", "amount": 12000, "type": "Debit"}
    ]

def get_expense_breakdown_data():
    return {
        "data": [
            {
                "labels": ["Salaries", "Supplies", "Utilities", "Travel"],
                "values": [12000, 4000, 2000, 1500],
                "type": "pie",
                "name": "Expenses"
            }
        ],
        "layout": {"title": "Expense Breakdown"}
    }

def get_revenue_sources_data():
    return {
        "data": [
            {
                "labels": ["Product Sales", "Services", "Investments"],
                "values": [18000, 7000, 3000],
                "type": "pie",
                "name": "Revenue"
            }
        ],
        "layout": {"title": "Revenue Sources"}
    }

def get_outstanding_invoices_data():
    return {
        "data": [
            {
                "x": ["INV-1001", "INV-1002", "INV-1003"],
                "y": [3500, 5000, 4200],
                "type": "bar",
                "name": "Outstanding"
            }
        ],
        "layout": {"title": "Outstanding Invoices"}
    }


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
        # Attempt to read from DB models if they exist in this module's globals
        accounts = []
        invoices = []
        payments = []
        expenses = []
        journal_entries = []

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
                    "invoice_id": getattr(p, "invoice_id", None) or getattr(p, "invoice", None),
                    "date": getattr(p, "date", None) or getattr(p, "created_at", None),
                    "amount": float(getattr(p, "amount", 0) or 0),
                    "method": getattr(p, "method", "") or "",
                    "status": getattr(p, "status", "") or ""
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
        any_db_data = any([accounts, invoices, payments, expenses, journal_entries])
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
            outstanding_invoices_data={"data": [], "layout": {}}
        )

    except Exception:
        app.logger.exception("DB unavailable or accounting models missing; falling back to file-based finance data")

@app.route("/assets/edit/<int:asset_id>", methods=["GET", "POST"])
@login_required
def edit_asset(asset_id):
    """
    Edit an existing asset entry.
    Retrieves asset from the in-memory list (later replaced by DB query).
    """
    asset = next((a for a in assets_data if a["id"] == asset_id), None)
    if not asset:
        flash("Asset not found.", "error")
        return redirect(url_for("asset_overview"))

    if request.method == "POST":
        name = request.form.get("name")
        category = request.form.get("category")
        purchase_date = request.form.get("purchase_date")
        value = request.form.get("value")
        depreciation_rate = request.form.get("depreciation_rate")
        status = request.form.get("status")

        if not name or not category or not purchase_date or not value:
            flash("Please fill in all required fields.", "error")
            return redirect(url_for("edit_asset", asset_id=asset_id))

        # Update the asset data
        asset["name"] = name
        asset["category"] = category
        asset["purchase_date"] = purchase_date
        asset["value"] = float(value)
        asset["depreciation_rate"] = float(depreciation_rate or 0)
        asset["status"] = status

        # audit log update
        try:
            log_audit(action="update", resource_type="Asset", resource_id=asset_id, before=before, after=asset)
        except Exception:
            app.logger.debug("Audit log failed for edit_asset", exc_info=True)


        flash(f"Asset '{name}' updated successfully!", "success")
        return redirect(url_for("asset_overview"))

    return render_template("edit-asset.html", asset=asset)

@app.route("/assets/delete/<int:asset_id>", methods=["POST"])
@login_required
def delete_asset(asset_id):
   
    global assets_data

    asset = next((a for a in assets_data if a["id"] == asset_id), None)

    if not asset:
        flash("Asset not found.", "error")
        return redirect(url_for("asset_overview"))

    # capture before snapshot for audit
    before = asset.copy()
    assets_data = [a for a in assets_data if a["id"] != asset_id]

    # audit log delete
    try:
        log_audit(action="delete", resource_type="Asset", resource_id=asset_id, before=before, after=None)
    except Exception:
        app.logger.debug("Audit log failed for delete_asset", exc_info=True)
        
    assets_data = [a for a in assets_data if a["id"] != asset_id]

    flash(f"Asset '{asset['name']}' deleted successfully!", "success")
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

        # Simulate database insert
        new_asset = {
            "id": len(assets_data) + 1,
            "name": name,
            "category": category,
            "purchase_date": purchase_date,
            "value": float(value),
            "depreciation_rate": float(depreciation_rate or 0),
            "status": status or "Active"
        }

        assets_data.append(new_asset)
        # audit log create
        try:
            log_audit(action="create", resource_type="Asset", resource_id=new_asset["id"], after=new_asset)
        except Exception:
            app.logger.debug("Audit log failed for add_asset", exc_info=True)

        flash(f"Asset '{name}' added successfully!", "success")
        return redirect(url_for("asset_overview"))

    # If GET: render the form
    return render_template("add-asset.html")

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
            # assume Asset has a 'status' column; adjust if different
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

# Replace file-based distribution handlers with DB-backed versions
@app.route('/distribution-overview')
def distribution_overview():
    """
    Show inventory items, shipments and orders from the PostgreSQL DB.
    Falls back to file-based JSON if DB access fails.
    """
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

        # best-effort field lists (safe getters)
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
        for s in shipments_rows:
            shipments.append(row_to_dict(s, [
                "id" if hasattr(Shipment, "id") else "",
                "shipment_id" if hasattr(Shipment, "shipment_id") else ("id" if hasattr(Shipment, "id") else "shipment"),
                "date" if hasattr(Shipment, "date") else ("shipped_at" if hasattr(Shipment, "shipped_at") else "created_at"),
                "carrier" if hasattr(Shipment, "carrier") else "carrier_name",
                "destination" if hasattr(Shipment, "destination") else "dest",
                "status" if hasattr(Shipment, "status") else "state"
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
                        "total" if hasattr(SalesOrder, "total") else ("amount" if hasattr(SalesOrder, "amount") else "value"),
                        "status" if hasattr(SalesOrder, "status") else "state"
                    ])
                    # attach best-effort customer name from joined Customer row
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
                        "total" if hasattr(SalesOrder, "total") else ("amount" if hasattr(SalesOrder, "amount") else "value"),
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
                    "total" if hasattr(SalesOrder, "total") else ("amount" if hasattr(SalesOrder, "amount") else "value"),
                    "status" if hasattr(SalesOrder, "status") else "state"
                ])
                od["customer_name"] = getattr(o, "customer_name", "") or getattr(o, "customer", "") or ""
                orders.append(od)
        
        return render_template(
            'distribution-overview.html',
            inventory=inventory,
            shipments=shipments,
            orders=orders
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
    """
    Prefer PostgreSQL-backed finance tables when available. Fallback to static JSON file
    (load_finance/save_finance) if DB models/tables are missing or an error occurs.
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
        # Try to load from DB models if present
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
                # group by label (latest override)
                financial_summary[label] = {"value": amount, "currency": safe_get(r, "currency", ""), "period": period}

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
                summary_list.append({"label": k, "value": f"{v.get('value', 0):.2f}"})
        else:
            # fallback aggregate from income_statement / cash_flow if needed
            total_revenue = sum(i.get("amount", 0) for i in income_statement if i.get("line_type") == "income")
            total_expenses = -sum(i.get("amount", 0) for i in income_statement if i.get("line_type") == "expense")
            summary_list = [
                {"label": "Total Revenue", "value": f"{total_revenue:.2f}"},
                {"label": "Total Expenses", "value": f"{total_expenses:.2f}"}
            ]

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
            pending_purchase_requests=pending_purchase_requests
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


@app.route('/add_payment', methods=['GET', 'POST'])
@login_required
def add_payment():
    """
    Create an outstanding payment record in the DB when model exists; otherwise fallback to file.
    """
    if request.method == 'POST':
        party = request.form.get('party', '').strip()
        due_date = request.form.get('due_date', '').strip()
        amount = request.form.get('amount', '').strip()
        status = request.form.get('status', '').strip()

        # Try DB first (prefer OutstandingPayment model)
        try:
            if 'OutstandingPayment' in globals():
                op = OutstandingPayment()
                _set_attr_if_exists(op, "payment_id", request.form.get('payment_id', None))
                _set_attr_if_exists(op, "party", party)
                _set_attr_if_exists(op, "due_date", due_date, date_try=True)
                _set_attr_if_exists(op, "amount", amount, cast_float=True)
                _set_attr_if_exists(op, "status", status)
                db.session.add(op)
                db.session.commit()
                flash('Payment added successfully!', 'success')
                return redirect(url_for('finance_overview'))

            # fallback to generic Payment model if available
            if 'Payment' in globals():
                p = Payment()
                _set_attr_if_exists(p, "party", party)
                _set_attr_if_exists(p, "date", due_date, date_try=True)
                _set_attr_if_exists(p, "amount", amount, cast_float=True)
                _set_attr_if_exists(p, "status", status)
                db.session.add(p)
                db.session.commit()
                flash('Payment added successfully!', 'success')
                return redirect(url_for('finance_overview'))
        except Exception:
            db.session.rollback()
            app.logger.exception("Failed to insert payment into DB; falling back to file")

        # File fallback
        try:
            data = load_finance()
            payments = data.get('outstanding_payments', [])
            new_id = 1
            # compute numeric suffix if payment_id present
            if payments:
                try:
                    # attempt to parse last numeric suffix
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
            flash('Payment added (file fallback).', 'success')
        except Exception:
            app.logger.exception("Failed to append payment to finance JSON")
            flash('Failed to add payment.', 'error')

        return redirect(url_for('finance_overview'))

    return render_template('add_payment.html')


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

#Fix this
@app.route('/attendance_overview')
def attendance_overview():
    """
    Prefer DB Attendance table when available; otherwise use HR JSON 'attendance'.
    """
    try:
        if 'Attendance' in globals():
            att_rows = db.session.query(Attendance).order_by(getattr(Attendance, "date", Attendance)).all()
            attendance_records = []
            for a in att_rows:
                attendance_records.append({
                    'id': getattr(a, 'id', None),
                    'employee_id': getattr(a, 'employee_id', None),
                    'date': getattr(a, 'date', None),
                    'status': getattr(a, 'status', None),
                    'check_in': getattr(a, 'check_in', None),
                    'check_out': getattr(a, 'check_out', None)
                })
            return render_template('attendance_overview.html', attendance_records=attendance_records)
    except Exception:
        app.logger.exception("DB unavailable for attendance_overview; falling back to file")

    # file fallback
    attendance_records = ('attendance', [])
    return render_template('attendance_overview.html', attendance_records=attendance_records)

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


@app.route('/human_resources_overview')
def human_resources_overview():
    """
    Load HR overview preferentially from PostgreSQL Employee table, with safe fallbacks.
    """
    try:
        # Try DB employees first
        if 'Employee' in globals():
            q = db.session.query(Employee)
            query = request.args.get('query', '').strip()
            selected_department = request.args.get('department', '')
            selected_status = request.args.get('status', '')

            if query:
                # try to match name or email fields if present
                if hasattr(Employee, "name"):
                    q = q.filter(getattr(Employee, "name").ilike(f"%{query}%"))
                elif hasattr(Employee, "email"):
                    q = q.filter(getattr(Employee, "email").ilike(f"%{query}%"))

            if selected_department and hasattr(Employee, "department"):
                q = q.filter(getattr(Employee, "department") == selected_department)
            if selected_status and hasattr(Employee, "status"):
                q = q.filter(getattr(Employee, "status") == selected_status)

            rows = q.order_by(getattr(Employee, "id", Employee)).all()
            employees = []
            for e in rows:
                employees.append({
                    "id": getattr(e, "id", None),
                    "name": getattr(e, "name", "") or "",
                    "department": getattr(e, "department", "") or "",
                    "role": getattr(e, "role", "") or "",
                    "email": getattr(e, "email", "") or "",
                    "phone": getattr(e, "phone", "") or "",
                    "status": getattr(e, "status", "") or ""
                })

            alerts = []  # could be populated from DB if available
            recent_activity = []  # optionally synthesize from audit logs/payroll/leave tables

            hr_summary = [
                {'label': 'Total Employees', 'value': len(employees)},
                {'label': 'Active', 'value': sum(1 for ev in employees if ev.get('status') == 'Active')},
                {'label': 'On Leave', 'value': sum(1 for ev in employees if ev.get('status') == 'On Leave')},
                {'label': 'Inactive', 'value': sum(1 for ev in employees if ev.get('status') == 'Inactive')}
            ]
            departments = sorted(set(ev.get('department') for ev in employees if ev.get('department')))

            filtered_employees = employees
            return render_template(
                'human-resources-overview.html',
                alerts=alerts,
                hr_summary=hr_summary,
                departments=departments,
                selected_department=request.args.get('department', ''),
                selected_status=request.args.get('status', ''),
                query=request.args.get('query', ''),
                employees=filtered_employees,
                recent_activity=recent_activity,
                hr_chart_data={
                    "data": [
                        {
                            "labels": ["Active", "On Leave", "Inactive"],
                            "values": [
                                sum(1 for ev in employees if ev.get('status') == 'Active'),
                                sum(1 for ev in employees if ev.get('status') == 'On Leave'),
                                sum(1 for ev in employees if ev.get('status') == 'Inactive')
                            ],
                            "type": "pie",
                            "name": "Employee Status"
                        }
                    ],
                    "layout": {"title": "Employee Status Distribution"}
                }
            )
    except Exception:
        app.logger.exception("DB unavailable for human_resources_overview")

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
            supplier_query=sup_q, supplier_status=sup_status
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
    Backend for templates/add_production_order.html
    Creates a ProductionOrder row using flexible attribute setting helper.
    """
    if request.method == 'POST':
        order_id = request.form.get('order_id', '').strip()
        product = request.form.get('product', '').strip()
        quantity = request.form.get('quantity', '').strip()
        start_date = request.form.get('start_date', '').strip()
        end_date = request.form.get('end_date', '').strip()
        status = request.form.get('status', '').strip()

        try:
            po = ProductionOrder()
            # Accept multiple possible model column names
            _set_attr_if_exists(po, "order_id", order_id)
            _set_attr_if_exists(po, "id", order_id)
            _set_attr_if_exists(po, "product", product)
            _set_attr_if_exists(po, "product_name", product)
            _set_attr_if_exists(po, "name", product)
            _set_attr_if_exists(po, "quantity", quantity, cast_float=True)
            _set_attr_if_exists(po, "qty", quantity, cast_float=True)
            _set_attr_if_exists(po, "amount", quantity, cast_float=True)
            _set_attr_if_exists(po, "start_date", start_date, date_try=True)
            _set_attr_if_exists(po, "date", start_date, date_try=True)
            _set_attr_if_exists(po, "end_date", end_date, date_try=True)
            _set_attr_if_exists(po, "status", status)
            _set_attr_if_exists(po, "state", status)

            db.session.add(po)
            db.session.commit()
            flash('Production order added!', 'success')
            return redirect(url_for('production_overview'))
        except Exception:
            db.session.rollback()
            app.logger.exception("Failed to add production order to DB")
            flash('Failed to add production order.', 'error')
            return redirect(url_for('production_overview'))

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
                "id": safe_get(r, "id", "order_id"),
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

        return render_template(
            'production-overview.html',
            production_orders=production_orders,
            bill_of_materials=bill_of_materials,
            work_centers=work_centers
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
ROLES = ["pending_user", "admin", "manager", "finance", "hr", "it", "logistics"]

@app.route("/admin-users")
@login_required
def admin_users():
    # allow only admin (role == 'admin' or group_id == 0)
    model = getattr(current_user, "model", None)
    is_admin = bool(model and (getattr(model, "role", None) == "admin" or getattr(model, "group_id", None) == 0))
    if not is_admin:
        return render_template("403.html"), 403

    users = db.session.query(User).order_by(User.id).all()
    return render_template("admin-users.html", users=users, groups=GROUPS, roles=ROLES)

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
    # existing analytics variables are produced elsewhere; keep them
    try:
        # gather customers and inventory for the order-entry UI (best-effort)
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

        # reuse the precomputed graphs from module-level variables if present
        sales_trend = globals().get("sales_trend_graph", {"data": [], "layout": {}})
        goods_perf = globals().get("goods_performance_pie_chart", {"data": [], "layout": {}})
        cust_expenditure = globals().get("customer_expenditure_pie_chart", {"data": [], "layout": {}})

        # KPI calculation (best-effort, safe fallbacks)
        kpi = None
        try:
            # orders count
            orders_count = 0
            try:
                orders_count = int(db.session.query(func.count()).select_from(SalesOrder).scalar() or 0)
            except Exception:
                orders_count = len(db.session.query(SalesOrder).all()) if 'SalesOrder' in globals() else 0

            # customers count
            customers_count = 0
            try:
                customers_count = int(db.session.query(func.count()).select_from(Customer).scalar() or 0)
            except Exception:
                customers_count = len(customers)

            # revenue / total sales amount
            total_revenue = 0.0
            try:
                if 'SalesOrder' in globals():
                    value_col = getattr(SalesOrder, "total", None) or getattr(SalesOrder, "amount", None)
                    if value_col is not None:
                        total_revenue = float(db.session.query(func.coalesce(func.sum(value_col), 0)).scalar() or 0.0)
            except Exception:
                app.logger.exception("Failed to compute total_revenue for sales_overview")
                total_revenue = 0.0

            # human-friendly formatted totals
            kpi = {
                "total_sales": f"{total_revenue:,.2f}",
                "orders": int(orders_count),
                "customers": int(customers_count),
                "revenue": f"{total_revenue:,.2f}"
            }
        except Exception:
            app.logger.exception("Failed to build KPIs for sales_overview")
            kpi = {"total_sales": "N/A", "orders": "N/A", "customers": "N/A", "revenue": "N/A"}

        return render_template('sales_overview.html',
                               sales_trend_graph=sales_trend,
                               goods_performance_pie_chart=goods_perf,
                               customer_expenditure_pie_chart=cust_expenditure,
                               customers=customers,
                               inventory=inventory,
                               kpi=kpi)
    except Exception:
        app.logger.exception("Failed to render sales_overview")
        # safe fallback
        return render_template('sales_overview.html',
                               sales_trend_graph={"data": [], "layout": {}},
                               goods_performance_pie_chart={"data": [], "layout": {}},
                               customer_expenditure_pie_chart={"data": [], "layout": {}},
                               customers=[], inventory=[], kpi={"total_sales":"N/A","orders":"N/A","customers":"N/A","revenue":"N/A"})


@app.route('/create_sale', methods=['POST'])
@login_required
def create_sale():
    """
    Create a sale (order) safely and always return JSON.
    Accepts JSON or form: { customer_id, inventory_id, quantity }.
    """
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
        # IMPORTANT: Rollback to clear any aborted transaction state if the query failed
        db.session.rollback()
        app.logger.exception("Credit check failed; continuing without blocking")

    # Perform DB transaction
    try:
        # Ensure we start with a clean state
        db.session.rollback()

        total = float(getattr(item, "unit_cost", 0) or 0) * quantity
        so_id = None
        inv_id = None

        so = None
        if 'SalesOrder' in globals():
            so = SalesOrder()
            # ensure order_id if DB requires it
            if hasattr(SalesOrder, "order_id"):
                try:
                    next_oid = db.session.query(func.coalesce(func.max(getattr(SalesOrder, "order_id")), 0) + 1).scalar()
                except Exception:
                    # Rollback if ID generation query fails
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
            _set_attr_if_exists(so, "status", "pending")
            _set_attr_if_exists(so, "order_date", datetime.now().date(), date_try=True)
            db.session.add(so)
            db.session.flush()  # populate so.id
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

        # create invoice if model exists
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

        # notify warehouse by creating Shipment record if model exists
        if 'Shipment' in globals():
            sh = Shipment()
            # Generate shipment_id based on count + 1
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