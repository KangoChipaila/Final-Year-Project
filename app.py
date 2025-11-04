from flask import Flask, render_template, jsonify, Response, send_file, request, redirect, url_for, json, flash, make_response
from pyspark.sql import SparkSession
from pyspark.sql.functions import col
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash
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
from sqlalchemy import text
from sqlalchemy.exc import OperationalError, DataError
from flask_migrate import Migrate
from routes.assets_upload import bp as assets_upload_bp

from models import (
    db, register_extensions,
    Customer, Employee,
    InventoryItem, Shipment,
    PurchaseRequest, PurchaseOrder, Supplier,
    ProductionOrder, BillOfMaterials, WorkCenter,
    Asset, SalesOrder, SalesForecast, Payment, User
)

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
                return redirect(url_for("index"))
            else:
                return render_template("login.html", error="Invalid username or password")
        except (OperationalError, DataError):
            # DB not available — try in-memory
            pass
        """
        # fallback: in-memory USERS
        user = USERS.get(username)
        if user and user["password"] == password:
            login_user(AuthUser(FallbackUser(username)))
            return redirect(url_for("index"))"""

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
    summary = get_accounting_summary()
    alerts = get_accounting_alerts()
    recent_activity = get_accounting_recent_activity()
    top_vendors = get_top_vendors()
    recent_transactions = get_recent_transactions()
    cashflow_data = get_cashflow_data()
    expense_breakdown_data = get_expense_breakdown_data()
    revenue_sources_data = get_revenue_sources_data()
    outstanding_invoices_data = get_outstanding_invoices_data()
    return render_template(
        "accounting-overview.html",
        summary=summary,
        alerts=alerts,
        recent_activity=recent_activity,
        top_vendors=top_vendors,
        recent_transactions=recent_transactions,
        cashflow_data=cashflow_data,
        expense_breakdown_data=expense_breakdown_data,
        revenue_sources_data=revenue_sources_data,
        outstanding_invoices_data=outstanding_invoices_data,
        current_user=current_user
    )

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

    except Exception as exc:
        app.logger.exception("Failed to load assets from DB, falling back to in-memory list")
        flash("Could not load assets from database. Showing in-memory data.", "warning")
        # fallback to previous in-memory list (assets_data)
        assets = assets_data
        categories = sorted(list(set([a["category"] for a in assets])))

    return render_template(
        "assets-overview.html",
        assets=assets,
        categories=categories,
        query=query,
        selected_category=category,
        selected_status=status
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

CUSTOMER_DATA_FILE = 'static/js/test_customer_data.json'

def load_customers():
    with open(CUSTOMER_DATA_FILE, 'r') as f:
        return json.load(f)

def save_customers(customers):
    with open(CUSTOMER_DATA_FILE, 'w') as f:
        json.dump(customers, f, indent=2)

@app.route('/add_customer', methods=['GET', 'POST'])
def add_customer():
    if request.method == 'POST':
        customers = load_customers()
        new_id = max(int(c['id']) for c in customers) + 1 if customers else 1
        new_customer = {
            'id': new_id,
            'name': request.form[str('name')],
            'contact_person': request.form['contact_person'],
            'email': request.form['email'],
            'phone': request.form['phone'],
            'status': request.form['status']
        }
        customers.append(new_customer)
        save_customers(customers)
        flash('Customer added successfully!', 'success')
        return redirect(url_for('customer_overview'))
    return render_template('add_customer.html')

@app.route('/edit_customer/<int:customer_id>', methods=['GET', 'POST'])
def edit_customer(customer_id):
    customers = load_customers()
    customer = next((c for c in customers if c['id'] == customer_id), None)
    if not customer:
        flash('Customer not found.', 'danger')
        return redirect(url_for('customer_overview'))
    if request.method == 'POST':
        customer['name'] = request.form['name']
        customer['contact_person'] = request.form['contact_person']
        customer['email'] = request.form['email']
        customer['phone'] = request.form['phone']
        customer['status'] = request.form['status']
        save_customers(customers)
        flash('Customer updated successfully!', 'success')
        return redirect(url_for('customer_overview'))
    return render_template('edit_customer.html', customer=customer)

@app.route('/delete_customer/<int:customer_id>', methods=['POST'])
def delete_customer(customer_id):
    customers = load_customers()
    customers = [c for c in customers if c['id'] != customer_id]
    save_customers(customers)
    flash('Customer deleted successfully!', 'success')
    return redirect(url_for('customer_overview'))

@app.route('/customer-overview')
def customer_overview():
    customers = load_customers()    
    print(type(customers[0])) 
    query = request.args.get('query', '')
    selected_status = request.args.get('status', '')
    filtered = [
        c for c in customers
        if (query.lower() in c.get('name', '').lower()) and
           (selected_status == '' or c.get('status', '') == selected_status)
    ]
    customer_summary = [
        {'label': 'Total Customers', 'value': len(customers)},
        {'label': 'Active Customers', 'value': sum(1 for c in customers if c['status'] == 'Active')},
        {'label': 'Inactive Customers', 'value': sum(1 for c in customers if c['status'] == 'Inactive')},
    
    ]
    customer_chart_data = {}  
    return render_template('customer-overview.html',
                           customers=filtered,
                           query=query,
                           selected_status=selected_status,
                           customer_summary=customer_summary,
                           customer_chart_data=customer_chart_data)

DISTRIBUTION_DATA_FILE = 'static/js/test_distribution_data.json'

def load_distribution_data():
    with open(DISTRIBUTION_DATA_FILE, 'r') as f:
        return json.load(f)

def save_distribution_data(data):
    with open(DISTRIBUTION_DATA_FILE, 'w') as f:
        json.dump(data, f, indent=2)

@app.route('/distribution-overview')
def distribution_overview():
    data = load_distribution_data()
    inventory = data.get('inventory', [])
    shipments = data.get('shipments', [])
    orders = data.get('orders', [])
    return render_template(
        'distribution-overview.html',
        inventory=inventory,
        shipments=shipments,
        orders=orders
    )

@app.route('/add_shipment', methods=['GET', 'POST'])
def add_shipment():
    if request.method == 'POST':
        data = load_distribution_data()
        shipments = data.get('shipments', [])
        new_id = max([int(s['shipment_id'].split('-')[-1]) for s in shipments], default=1000) + 1
        new_shipment = {
            'shipment_id': f"SHP-{new_id}",
            'date': request.form['date'],
            'carrier': request.form['carrier'],
            'destination': request.form['destination'],
            'status': request.form['status']
        }
        shipments.append(new_shipment)
        data['shipments'] = shipments
        save_distribution_data(data)
        flash('Shipment added!', 'success')
        return redirect(url_for('distribution_overview'))
    return render_template('add_shipment.html')

@app.route('/add_order', methods=['GET', 'POST'])
def add_order():
    if request.method == 'POST':
        data = load_distribution_data()
        orders = data.get('orders', [])
        new_id = max([int(o['order_id'].split('-')[-1]) for o in orders], default=5000) + 1
        new_order = {
            'order_id': f"ORD-{new_id}",
            'customer': request.form['customer'],
            'date': request.form['date'],
            'total': request.form['total'],
            'status': request.form['status']
        }
        orders.append(new_order)
        data['orders'] = orders
        save_distribution_data(data)
        flash('Order added!', 'success')
        return redirect(url_for('distribution_overview'))
    return render_template('add_order.html')

@app.route('/receive_inventory', methods=['GET', 'POST'])
def receive_inventory():
    if request.method == 'POST':
        data = load_distribution_data()
        inventory = data.get('inventory', [])
        new_item = {
            'product': request.form['product'],
            'sku': request.form['sku'],
            'warehouse': request.form['warehouse'],
            'quantity': request.form['quantity'],
            'reorder_level': request.form['reorder_level'],
            'status': request.form['status']
        }
        inventory.append(new_item)
        data['inventory'] = inventory
        save_distribution_data(data)
        flash('Inventory received!', 'success')
        return redirect(url_for('distribution_overview'))
    return render_template('receive_inventory.html')

FINANCE_DATA_FILE = 'static/js/test_finance_data.json'

def load_finance():
    with open(FINANCE_DATA_FILE, 'r') as f:
        return json.load(f)

def save_finance(data):
    with open(FINANCE_DATA_FILE, 'w') as f:
        json.dump(data, f, indent=2)

@app.route('/finance-overview')
def finance_overview():
    data = load_finance()
    # Extract summary, income_statement, cash_flow, outstanding_payments, finance_chart_data from data
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
                           finance_chart_data=finance_chart_data)

@app.route('/add_payment', methods=['GET', 'POST'])
def add_payment():
    if request.method == 'POST':
        data = load_finance()
        payments = data.get('outstanding_payments', [])
        new_id = max([int(p['payment_id']) for p in payments], default=0) + 1
        new_payment = {
            'payment_id': str(new_id),
            'party': request.form['party'],
            'due_date': request.form['due_date'],
            'amount': request.form['amount'],
            'status': request.form['status']
        }
        payments.append(new_payment)
        data['outstanding_payments'] = payments
        save_finance(data)
        flash('Payment added successfully!', 'success')
        return redirect(url_for('finance_overview'))
    return render_template('add_payment.html')

@app.route('/add_receipt', methods=['GET', 'POST'])
def add_receipt():
    if request.method == 'POST':
        data = load_finance()
        cash_flow = data.get('cash_flow', [])
        new_receipt = {
            'date': request.form['date'],
            'description': request.form['description'],
            'inflow': request.form['inflow'],
            'outflow': '',
            'balance': request.form['balance']
        }
        cash_flow.append(new_receipt)
        data['cash_flow'] = cash_flow
        save_finance(data)
        flash('Receipt added successfully!', 'success')
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


HR_DATA_FILE = 'static/js/test_hr_data.json'

def load_hr_data():
    with open(HR_DATA_FILE, 'r') as f:
        return json.load(f)

def save_hr_data(data):
    with open(HR_DATA_FILE, 'w') as f:
        json.dump(data, f, indent=2)

@app.route('/add_employee', methods=['GET', 'POST'])
def add_employee():
    if request.method == 'POST':
        data = load_hr_data()
        employees = data.get('employees', [])
        new_id = max([int(e['id']) for e in employees], default=0) + 1
        new_employee = {
            'id': str(new_id),
            'name': request.form['name'],
            'department': request.form['department'],
            'role': request.form['role'],
            'email': request.form['email'],
            'phone': request.form['phone'],
            'status': request.form['status']
        }
        employees.append(new_employee)
        data['employees'] = employees
        save_hr_data(data)
        flash('Employee added successfully!', 'success')
        return redirect(url_for('human_resources_overview'))
    return render_template('add_employee.html')

@app.route('/edit_employee/<employee_id>', methods=['GET', 'POST'])
def edit_employee(employee_id):
    data = load_hr_data()
    employees = data.get('employees', [])
    employee = next((e for e in employees if e['id'] == employee_id), None)
    if not employee:
        flash('Employee not found.', 'danger')
        return redirect(url_for('human_resources_overview'))
    if request.method == 'POST':
        employee['name'] = request.form['name']
        employee['department'] = request.form['department']
        employee['role'] = request.form['role']
        employee['email'] = request.form['email']
        employee['phone'] = request.form['phone']
        employee['status'] = request.form['status']
        save_hr_data(data)
        flash('Employee updated successfully!', 'success')
        return redirect(url_for('human_resources_overview'))
    return render_template('edit_employee.html', employee=employee)

@app.route('/delete_employee/<employee_id>', methods=['POST'])
def delete_employee(employee_id):
    data = load_hr_data()
    employees = data.get('employees', [])
    employees = [e for e in employees if e['id'] != employee_id]
    data['employees'] = employees
    save_hr_data(data)
    flash('Employee deleted successfully!', 'success')
    return redirect(url_for('human_resources_overview'))

@app.route('/departments_overview')
def departments_overview():
    data = load_hr_data()
    employees = data.get('employees', [])
    departments = sorted(set(e['department'] for e in employees))
    department_stats = [
        {'name': dept, 'employees': sum(1 for e in employees if e['department'] == dept)}
        for dept in departments
    ]
    return render_template('departments_overview.html', departments=department_stats)

@app.route('/attendance_overview')
def attendance_overview():
    data = load_hr_data()
    attendance_records = data.get('attendance', [])
    return render_template('attendance_overview.html', attendance_records=attendance_records)

@app.route('/payroll_overview')
def payroll_overview():
    data = load_hr_data()
    payroll = data.get('payroll', [])
    return render_template('payroll_overview.html', payroll=payroll)

@app.route('/leave_overview')
def leave_overview():
    data = load_hr_data()
    leave_requests = data.get('leave_requests', [])
    return render_template('leave_overview.html', leave_requests=leave_requests)

@app.route('/hr_reports')
def hr_reports():
    data = load_hr_data()
    reports = data.get('reports', [
        {"title": "Headcount Report", "date": "2025-10-01"},
        {"title": "Attendance Summary", "date": "2025-10-10"}
    ])
    return render_template('hr_reports.html', reports=reports)

@app.route('/human_resources_overview')
def human_resources_overview():
    data = load_hr_data()
    employees = data.get('employees', [])
    alerts = data.get('alerts', [
        "Employee contract expiring soon: John Banda.",
        "Pending leave approval for Alice Mwansa.",
        "New employee onboarding: Peter Zulu."
    ])
    recent_activity = data.get('recent_activity', [
        "Alice Mwansa requested leave for 5 days.",
        "Peter Zulu added to IT department.",
        "John Banda completed annual review."
    ])  
    hr_summary = [
        {'label': 'Total Employees', 'value': len(employees)},
        {'label': 'Active', 'value': sum(1 for e in employees if e['status'] == 'Active')},
        {'label': 'On Leave', 'value': sum(1 for e in employees if e['status'] == 'On Leave')},
        {'label': 'Inactive', 'value': sum(1 for e in employees if e['status'] == 'Inactive')}
    ]
    departments = sorted(set(e['department'] for e in employees))
    query = request.args.get('query', '')
    selected_department = request.args.get('department', '')
    selected_status = request.args.get('status', '')
    filtered_employees = [
        e for e in employees
        if (query.lower() in e['name'].lower())
        and (selected_department == '' or e['department'] == selected_department)
        and (selected_status == '' or e['status'] == selected_status)
    ]
    hr_chart_data = {
        "data": [
            {
                "labels": ["Active", "On Leave", "Inactive"],
                "values": [
                    sum(1 for e in employees if e['status'] == 'Active'),
                    sum(1 for e in employees if e['status'] == 'On Leave'),
                    sum(1 for e in employees if e['status'] == 'Inactive')
                ],
                "type": "pie",
                "name": "Employee Status"
            }
        ],
        "layout": {"title": "Employee Status Distribution"}
    }
    return render_template(
        'human-resources-overview.html',
        alerts=alerts,
        hr_summary=hr_summary,
        departments=departments,
        selected_department=selected_department,
        selected_status=selected_status,
        query=query,
        employees=filtered_employees,
        recent_activity=recent_activity,
        hr_chart_data=hr_chart_data
    )

PROCUREMENT_DATA_FILE = 'static/js/test_procurement_data.json'

def load_procurement_data():
    with open(PROCUREMENT_DATA_FILE, 'r') as f:
        return json.load(f)

def save_procurement_data(data):
    with open(PROCUREMENT_DATA_FILE, 'w') as f:
        json.dump(data, f, indent=2)

@app.route('/procurement-overview')
def procurement_overview():
    data = load_procurement_data()
    purchase_requests = data.get('purchase_requests', [])
    purchase_orders = data.get('purchase_orders', [])
    suppliers = data.get('suppliers', [])
    return render_template(
        'procurement-overview.html',
        purchase_requests=purchase_requests,
        purchase_orders=purchase_orders,
        suppliers=suppliers
    )

@app.route('/add_purchase_request', methods=['GET', 'POST'])
def add_purchase_request():
    if request.method == 'POST':
        data = load_procurement_data()
        requests = data.get('purchase_requests', [])
        new_id = max([int(r['request_id']) for r in requests], default=0) + 1
        new_request = {
            'request_id': str(new_id),
            'requested_by': request.form['requested_by'],
            'date': request.form['date'],
            'item': request.form['item'],
            'quantity': request.form['quantity'],
            'status': request.form['status']
        }
        requests.append(new_request)
        data['purchase_requests'] = requests
        save_procurement_data(data)
        flash('Purchase request added!', 'success')
        return redirect(url_for('procurement_overview'))
    return render_template('add_purchase_request.html')

@app.route('/add_purchase_order', methods=['GET', 'POST'])
def add_purchase_order():
    if request.method == 'POST':
        data = load_procurement_data()
        orders = data.get('purchase_orders', [])
        new_id = max([int(o['order_id']) for o in orders], default=0) + 1
        new_order = {
            'order_id': str(new_id),
            'vendor': request.form['vendor'],
            'date': request.form['date'],
            'item': request.form['item'],
            'quantity': request.form['quantity'],
            'amount': request.form['amount'],
            'status': request.form['status']
        }
        orders.append(new_order)
        data['purchase_orders'] = orders
        save_procurement_data(data)
        flash('Purchase order added!', 'success')
        return redirect(url_for('procurement_overview'))
    return render_template('add_purchase_order.html')

@app.route('/add_supplier', methods=['GET', 'POST'])
def add_supplier():
    if request.method == 'POST':
        data = load_procurement_data()
        suppliers = data.get('suppliers', [])
        new_supplier = {
            'name': request.form['name'],
            'contact_person': request.form['contact_person'],
            'email': request.form['email'],
            'phone': request.form['phone'],
            'status': request.form['status']
        }
        suppliers.append(new_supplier)
        data['suppliers'] = suppliers
        save_procurement_data(data)
        flash('Supplier added!', 'success')
        return redirect(url_for('procurement_overview'))
    return render_template('add_supplier.html')

PRODUCTION_DATA_FILE = 'static/js/test_production_data.json'

def load_production_data():
    with open(PRODUCTION_DATA_FILE, 'r') as f:
        return json.load(f)

def save_production_data(data):
    with open(PRODUCTION_DATA_FILE, 'w') as f:
        json.dump(data, f, indent=2)

@app.route('/production-overview')
def production_overview():
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

@app.route('/add_production_order', methods=['GET', 'POST'])
def add_production_order():
    if request.method == 'POST':
        data = load_production_data()
        orders = data.get('production_orders', [])
        new_id = max([int(o['order_id']) for o in orders], default=0) + 1
        new_order = {
            'order_id': str(new_id),
            'product': request.form['product'],
            'quantity': request.form['quantity'],
            'start_date': request.form['start_date'],
            'end_date': request.form['end_date'],
            'status': request.form['status']
        }
        orders.append(new_order)
        data['production_orders'] = orders
        save_production_data(data)
        flash('Production order added!', 'success')
        return redirect(url_for('production_overview'))
    return render_template('add_production_order.html')

@app.route('/add_bom', methods=['GET', 'POST'])
def add_bom():
    if request.method == 'POST':
        data = load_production_data()
        boms = data.get('bill_of_materials', [])
        new_bom = {
            'product': request.form['product'],
            'component': request.form['component'],
            'quantity_required': request.form['quantity_required'],
            'unit': request.form['unit']
        }
        boms.append(new_bom)
        data['bill_of_materials'] = boms
        save_production_data(data)
        flash('BOM added!', 'success')
        return redirect(url_for('production_overview'))
    return render_template('add_bom.html')

@app.route('/update_work_center', methods=['GET', 'POST'])
def update_work_center():
    data = load_production_data()
    work_centers = data.get('work_centers', [])
    if request.method == 'POST':
        wc_name = request.form['name']
        for wc in work_centers:
            if wc['name'] == wc_name:
                wc['current_task'] = request.form['current_task']
                wc['status'] = request.form['status']
                wc['operator'] = request.form['operator']
        data['work_centers'] = work_centers
        save_production_data(data)
        flash('Work center updated!', 'success')
        return redirect(url_for('production_overview'))
    return render_template('update_work_center.html', work_centers=work_centers)

@app.route('/sales-overview')
async def sales_overview():

    sales_forecast_graph = await analytics.generate_sales_forecast()
    
    return render_template('sales_overview.html',
                           sales_forecast_graph = sales_forecast_graph, 
                           sales_trend_graph = sales_trend_graph, 
                           goods_performance_pie_chart = goods_performance_pie_chart,
                           customer_expenditure_pie_chart = customer_expenditure_pie_chart)
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

        print(partitioned_data_file.head())
        # Uncomment the line below to write the partitioned data to HDFS
        # partitioned_data_file.write.partitionBy('SalesYear').mode('append').parquet('hdfs://localhost:19000/data/sales_data_files')

        os.remove(temp_local_path)

        return 'File successfully uploaded and processed'
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

# replace roles list so "user" becomes "pending_user"
ROLES = ["pending_user", "admin", "manager", "finance", "hR", "iT", "logistics"]

@app.route("/admin/users")
@login_required
def admin_users():
    # allow only admin (role == 'admin' or group_id == 0)
    model = getattr(current_user, "model", None)
    is_admin = bool(model and (getattr(model, "role", None) == "admin" or getattr(model, "group_id", None) == 0))
    if not is_admin:
        return render_template("403.html"), 403

    users = db.session.query(User).order_by(User.id).all()
    return render_template("admin-users.html", users=users, groups=GROUPS, roles=ROLES)

@app.route("/admin/users/<int:user_id>/update", methods=["POST"])
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
    except Exception as e:
        db.session.rollback()
        app.logger.exception("Failed to update user")
        flash("Failed to update user.", "error")
        flash(str(e))

    return redirect(url_for("admin_users"))

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