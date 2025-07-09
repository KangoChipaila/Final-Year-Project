from flask import Flask, render_template
import analytics

app = Flask(__name__)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/sales-overview')
def sales_overview():

    sales_trend_graph = analytics.generate_sales_trend()
    goods_performance_pie_chart = analytics.generate_goods_performance_pchart()
    customer_expenditure_pie_chart = analytics.generate_customer_expenditure_distribution_pchart()

    return render_template('sales_overview.html', 
                           sales_trend_graph = sales_trend_graph, 
                           goods_performance_pie_chart = goods_performance_pie_chart,
                           customer_expenditure_pie_chart = customer_expenditure_pie_chart)

if __name__ == '__main__':
    app.run(debug=True)
