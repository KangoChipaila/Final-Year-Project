from flask import Flask, render_template, jsonify, Response, send_file, request, redirect, url_for, json, flash
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

app = Flask(__name__)
app.secret_key = "supersecretkey"  # Replace with a real secret key

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

@app.route('/')
def index():
    return render_template('index.html', sales_trend_graph=sales_trend_graph, customers=customer_expenditure_pie_chart)

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

@app.route('/customer-overview')
def customer_overview():
    return render_template('customer-overview.html')

@app.route('/distribution-overview')
def distribution_overview():
    return render_template('distribution-overview.html')

@app.route('/finance-overview')
def finance_overview():
    return render_template('finance-overview.html')

@app.route('/human-resources-overview')
def human_resources_overview():
    return render_template('human-resources-overview.html')

@app.route('/procurement-overview')
def procurement_overview():
    return render_template('procurement-overview.html')

@app.route('/production-overview')
def production_overview():
    return render_template('production-overview.html')

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
