from flask import Flask, render_template, jsonify
import analytics

app = Flask(__name__)

sales_trend_graph = analytics.generate_sales_trend()
goods_performance_pie_chart = analytics.generate_goods_performance_pchart()
customer_expenditure_pie_chart = analytics.generate_customer_expenditure_distribution_pchart()

"""async def load_sales_forecast():
    forecast = await analytics.generate_sales_forecast()
    return forecast"""

#Add await logic for load_sales_forecast

@app.route('/')
def index():
    return render_template('index.html', sales_trend_graph=sales_trend_graph, customers=customer_expenditure_pie_chart)

@app.route('/accounting-overview')
def accounting_overview():
    return render_template('accounting-overview.html')

@app.route('/assets-overview')
def assets_overview():
    return render_template('assets-overview.html')

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

if __name__ == '__main__':
    app.run(debug=True)
