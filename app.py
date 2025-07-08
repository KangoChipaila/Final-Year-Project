from flask import Flask, render_template
import analytics

app = Flask(__name__)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/sales-overview')
def sales_overview():
    sales_trend_graph = analytics.generate_sales_trend()
    return render_template('sales_overview.html', sales_trend_graph = sales_trend_graph)

if __name__ == '__main__':
    app.run(debug=True)
