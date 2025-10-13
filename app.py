from flask import Flask, render_template, jsonify, Response, send_file, request, redirect, url_for, json, flash, make_response
from pyspark.sql import SparkSession
from pyspark.sql.functions import col
from werkzeug.utils import secure_filename
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

app = Flask(__name__)
app.secret_key = "supersecretkey"  # Replace with a real secret key

config = pdfkit.configuration(wkhtmltopdf=r'C:\Progra~1\wkhtmltopdf\bin\wkhtmltopdf.exe')

# ------------------- Login Manager Setup -------------------
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"

# ------------------- Mock Database -------------------
USERS = {
    "admin": {"password": "admin123"},
    "kango": {"password": "erp2025"}
}

class User(UserMixin):
    def __init__(self, id, username):
        self.id = id
        self.username = username

@login_manager.user_loader
def load_user(user_id):
    # In a real app, query your users table here
    for username in USERS:
        if username == user_id:
            return User(user_id, username)
    return None

# ------------------- Context Processor (for base.html) -------------------
@app.context_processor
def inject_globals():
    return {
        "current_user": current_user,
        "current_year": datetime.now().year,
        "system_name": "Data-Driven ERP System",
        "version": "1.0.0"
    }

# ------------------- Login Route -------------------
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")

        user = USERS.get(username)
        if user and user["password"] == password:
            login_user(User(id=username, username=username))
            return redirect(url_for("index"))
        else:
            return render_template("login.html", error="Invalid username or password")

    return render_template("login.html")

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

spark = SparkSession.builder.appName("CSVUpload").getOrCreate()

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
def index():
    data = load_dashboard_data()
    kpi = data.get('kpi', {
        'total_sales': 0,
        'total_customers': 0,
        'inventory_value': 0,
        'outstanding_orders': 0
    })
    alerts = data.get('alerts', [])
    recent_activity = data.get('recent_activity', [])
    top_customers = data.get('top_customers', [])
    recent_orders = data.get('recent_orders', [])
    sales_trend_graph = data.get('sales_trend_graph', {'data': [], 'layout': {}})
    customers = data.get('customers_graph', {'data': [], 'layout': {}})
    current_user = data.get('current_user', {'username': 'USERNAME'})
    return render_template(
        'index.html',
        kpi=kpi,
        alerts=alerts,
        recent_activity=recent_activity,
        top_customers=top_customers,
        recent_orders=recent_orders,
        sales_trend_graph=sales_trend_graph,
        customers=customers,
        current_user=current_user
    )

# Example mock data (replace with database queries)
def get_accounting_summary():
    return [
        {"label": "Total Balance", "value": "$250,000"},
        {"label": "Receivables", "value": "$85,000"},
        {"label": "Payables", "value": "$40,000"},
        {"label": "Net Income (YTD)", "value": "$195,000"}
    ]

def get_cashflow_data():
    # Example data for Plotly
    return {
        "data": [
            {
                "x": ["Jan", "Feb", "Mar", "Apr", "May", "Jun"],
                "y": [12000, 15000, 13000, 18000, 20000, 22000],
                "type": "bar",
                "name": "Cash Inflow"
            },
            {
                "x": ["Jan", "Feb", "Mar", "Apr", "May", "Jun"],
                "y": [8000, 9000, 10000, 9500, 12000, 11000],
                "type": "bar",
                "name": "Cash Outflow"
            }
        ],
        "layout": {
            "title": "Monthly Cash Flow",
            "barmode": "group"
        }
    }

@app.route("/accounting-overview")
def accounting_overview():
    summary = get_accounting_summary()
    cashflow_data = json.dumps(get_cashflow_data())  # serialize for Plotly
    return render_template(
        "accounting-overview.html",
        summary=summary,
        cashflow_data=cashflow_data
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
    """
    Deletes an asset from the in-memory list (temporary storage).
    In production, this would delete from the database.
    """
    global assets_data

    # Find asset by ID
    asset = next((a for a in assets_data if a["id"] == asset_id), None)

    if not asset:
        flash("Asset not found.", "error")
        return redirect(url_for("asset_overview"))

    # Remove the asset
    assets_data = [a for a in assets_data if a["id"] != asset_id]

    flash(f"Asset '{asset['name']}' deleted successfully!", "success")
    return redirect(url_for("asset_overview"))

@app.route("/assets/add", methods=["GET", "POST"])
@login_required
def add_asset():
    """
    Displays and handles the Add Asset form.
    Currently stores data in memory (replace with DB insert later).
    """
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
    query = request.args.get("query", "").lower()
    category = request.args.get("category", "")
    status = request.args.get("status", "")

    # Filter the assets based on user input
    filtered_assets = assets_data

    if query:
        filtered_assets = [a for a in filtered_assets if query in a["name"].lower()]

    if category:
        filtered_assets = [a for a in filtered_assets if a["category"] == category]

    if status:
        filtered_assets = [a for a in filtered_assets if a["status"] == status]

    # Build unique category list for the dropdown filter
    categories = sorted(list(set([a["category"] for a in assets_data])))

    return render_template(
        "assets-overview.html",
        assets=filtered_assets,
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

DATA_FILE = 'static/js/test_customer_data.json'

def load_customers():
    with open(DATA_FILE, 'r') as f:
        return json.load(f)

def save_customers(customers):
    with open(DATA_FILE, 'w') as f:
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
    print(type(customers), customers[:2])  # See what you get
    query = request.args.get('query', '')
    selected_status = request.args.get('status', '')
    filtered = [
        c for c in customers
        if (query.lower() in c['name'].lower()) and
           (selected_status == '' or c['status'] == selected_status)
    ]
    customer_summary = [
        {'label': 'Total Customers', 'value': len(customers)},
        {'label': 'Active Customers', 'value': sum(1 for c in customers if c['status'] == 'Active')},
        {'label': 'Inactive Customers', 'value': sum(1 for c in customers if c['status'] == 'Inactive')},
        # Add more stats as needed
    ]
    customer_chart_data = {}  # Generate chart data as needed
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

DATA_FILE = 'static/js/test_finance_data.json'

def load_finance():
    with open(DATA_FILE, 'r') as f:
        return json.load(f)

def save_finance(data):
    with open(DATA_FILE, 'w') as f:
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

@app.route('/human_resources_overview')
def human_resources_overview():
    data = load_hr_data()
    employees = data.get('employees', [])
    attendance = data.get('attendance', [])
    leave_requests = data.get('leave_requests', [])
    payroll = data.get('payroll', [])
    hr_summary = [
        {'label': 'Total Employees', 'value': len(employees)},
        {'label': 'Active', 'value': sum(1 for e in employees if e['status'] == 'Active')},
        {'label': 'On Leave', 'value': sum(1 for e in employees if e['status'] == 'On Leave')},
        {'label': 'Pending Payroll', 'value': sum(1 for p in payroll if p['status'] == 'Pending')}
    ]
    departments = sorted(set(e['department'] for e in employees))
    query = request.args.get('query', '')
    selected_department = request.args.get('department', '')
    filtered_employees = [
        e for e in employees
        if (query.lower() in e['name'].lower()) and
           (selected_department == '' or e['department'] == selected_department)
    ]
    hr_chart_data = {}  # Add chart data as needed
    return render_template(
        'human-resources-overview.html',
        employees=filtered_employees,
        attendance=attendance,
        leave_requests=leave_requests,
        payroll=payroll,
        hr_summary=hr_summary,
        departments=departments,
        query=query,
        selected_department=selected_department,
        hr_chart_data=hr_chart_data
    )

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

@app.route('/record_attendance', methods=['GET', 'POST'])
def record_attendance():
    if request.method == 'POST':
        data = load_hr_data()
        attendance = data.get('attendance', [])
        new_record = {
            'date': request.form['date'],
            'employee': request.form['employee'],
            'check_in': request.form['check_in'],
            'check_out': request.form['check_out'],
            'status': request.form['status']
        }
        attendance.append(new_record)
        data['attendance'] = attendance
        save_hr_data(data)
        flash('Attendance recorded!', 'success')
        return redirect(url_for('human_resources_overview'))
    data = load_hr_data()
    employees = data.get('employees', [])
    return render_template('record_attendance.html', employees=employees)

@app.route('/process_payroll', methods=['GET', 'POST'])
def process_payroll():
    if request.method == 'POST':
        data = load_hr_data()
        payroll = data.get('payroll', [])
        new_payroll = {
            'employee': request.form['employee'],
            'month': request.form['month'],
            'gross_pay': request.form['gross_pay'],
            'deductions': request.form['deductions'],
            'net_pay': request.form['net_pay'],
            'status': request.form['status']
        }
        payroll.append(new_payroll)
        data['payroll'] = payroll
        save_hr_data(data)
        flash('Payroll processed!', 'success')
        return redirect(url_for('human_resources_overview'))
    data = load_hr_data()
    employees = data.get('employees', [])
    return render_template('process_payroll.html', employees=employees)

# Add similar routes for leave requests if needed

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
def sales_overview():

    return render_template('sales_overview.html', 
                           sales_trend_graph = sales_trend_graph, 
                           goods_performance_pie_chart = goods_performance_pie_chart,
                           customer_expenditure_pie_chart = customer_expenditure_pie_chart)

@app.route('/detailed-sales-analytics')
async def detailed_sales_analytics():

    sales_forecast = await analytics.generate_sales_forecast()
    return render_template('detailed-sales-analytics.html', sales_forecast = sales_forecast)

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
    
@app.route('/sales-data-upload-to-hadoop', methods=['GET', 'POST']) 
def upload_to_hadoop():

    if 'sales-data-file' not in request.files:
        return "No file found for upload"
    
    sales_data_file = request.files['sales-data-file']

    if sales_data_file.filename == '' or not sales_data_file.filename.endswith('.csv'):
        return "Invalid data type selected. Please select a valid file"

    if sales_data_file:
        filename = secure_filename(sales_data_file.filename)
        
        # Use os.path.join correctly to create a valid local path for the OS
        temp_dir = os.path.abspath(os.sep) + 'tmp' 
        if not os.path.exists(temp_dir):
            os.makedirs(temp_dir)

        temp_local_path = os.path.join(temp_dir, filename)
        sales_data_file.save(temp_local_path)

        hdfs_upload_dir = 'hdfs://localhost:19000/data/raw_uploads'
        hdfs_upload_path = os.path.join(hdfs_upload_dir, filename).replace('\\', '/')

        # Format the local path for the hadoop command, replacing backslashes with forward slashes
        posix_local_path = temp_local_path.replace(os.sep, '/')
        
        """try:
            subprocess.run([hadoop_bin_path, 'fs', '-mkdir', '-p', hdfs_upload_dir], check=True)
            subprocess.run([hadoop_bin_path, 'fs', '-put', '-f', posix_local_path, hdfs_upload_path], check=True)

        except subprocess.CalledProcessError as e:
            return f"Failed to upload file to HDFS: {e}"
        except FileNotFoundError:
            return f"Hadoop executable not found at '{hadoop_bin_path}'. Please check your path."""
        
        sales_dataframe = spark.read.csv(hdfs_upload_path, header=True, inferSchema=True, encoding='cp1252')

        partitioned_data_file = sales_dataframe.withColumn('SalesYear', col('OrderDate').substr(7,10))

        print(partitioned_data_file.head())
        # Uncomment the line below to write the partitioned data to HDFS
        # partitioned_data_file.write.partitionBy('SalesYear').mode('append').parquet('hdfs://localhost:19000/data/sales_data_files')

        os.remove(temp_local_path)

        return 'File successfully uploaded and processed'

if __name__ == '__main__':
    app.run(debug=True)
