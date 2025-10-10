from flask import Flask, render_template, jsonify, Response, send_file, request, redirect, url_for
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

app = Flask(__name__)

UPLOAD_FOLDER = 'uploads'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16 MB limit
hadoop_bin_path = r'C:\hadoop\hadoop-3.3.5\bin\hadoop.cmd'
os.environ['HADOOP_USER_NAME'] = 'Kango Chipaila'

sales_trend_graph = analytics.generate_sales_trend()
goods_performance_pie_chart = analytics.generate_goods_performance_pchart()
customer_expenditure_pie_chart = analytics.generate_customer_expenditure_distribution_pchart()

spark = SparkSession.builder.appName("CSVUpload").getOrCreate()

@app.route('/')
def index():
    return render_template('index.html', sales_trend_graph=sales_trend_graph, customers=customer_expenditure_pie_chart)

@app.route('/accounting-overview')
def accounting_overview():
    return render_template('accounting-overview.html')

@app.route('/assets-overview')
def assets_overview():

    return render_template('assets-overview.html')

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
        
        try:
            subprocess.run([hadoop_bin_path, 'fs', '-mkdir', '-p', hdfs_upload_dir], check=True)
            subprocess.run([hadoop_bin_path, 'fs', '-put', '-f', posix_local_path, hdfs_upload_path], check=True)

        except subprocess.CalledProcessError as e:
            return f"Failed to upload file to HDFS: {e}"
        except FileNotFoundError:
            return f"Hadoop executable not found at '{hadoop_bin_path}'. Please check your path."
        
        sales_dataframe = spark.read.csv(hdfs_upload_path, header=True, inferSchema=True, encoding='cp1252')

        partitioned_data_file = sales_dataframe.withColumn('SalesYear', col('OrderDate').substr(7,10))

        print(partitioned_data_file.head())
        # Uncomment the line below to write the partitioned data to HDFS
        # partitioned_data_file.write.partitionBy('SalesYear').mode('append').parquet('hdfs://localhost:19000/data/sales_data_files')

        os.remove(temp_local_path)

        return 'File successfully uploaded and processed'


if __name__ == '__main__':
    app.run(debug=True)
