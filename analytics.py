import plotly.graph_objects as go
import pandas as pd

extracted_data = pd.read_csv("./spreadsheet_datasets/sales_data_sample.csv", encoding="cp1252")

def generate_sales_trend():

    extracted_data["ORDERDATE"] = pd.to_datetime(extracted_data["ORDERDATE"], format = "%m/%d/%Y %H:%M", errors = "coerce")

    extracted_data["Month-Year"] = extracted_data["ORDERDATE"].dt.to_period("M")

    monthly_sales = extracted_data.groupby("Month-Year")["SALES"].sum().reset_index()

    fig = go.Figure()

    fig.add_trace(go.Scatter(x = monthly_sales["Month-Year"].astype(str).to_list(), 
                             y = (monthly_sales["SALES"]/1000).to_list(), 
                             mode = 'lines + markers'))

    fig.update_layout(title = 'Monthly Sales Trend Over the Years', 
                    xaxis_title = 'Month-Year',
                    yaxis_title = 'Sales (1 = 1000 Kwacha)')
    
    return fig.to_plotly_json()

def generate_goods_performance_pchart():

    goods_performance = extracted_data.groupby("PRODUCTLINE")["QUANTITYORDERED"].sum().reset_index()

    fig = go.Figure(data = [go.Pie(labels = (goods_performance["PRODUCTLINE"].astype(str)).to_list(), 
                                   values = goods_performance["QUANTITYORDERED"].to_list())])
    
    fig.update_layout(title = "Goods Performance")

    return fig.to_plotly_json()

def generate_customer_expenditure_distribution_pchart():

    customer_expediture = extracted_data.groupby("CUSTOMERNAME")["SALES"].sum().reset_index()

    top_10_customers = customer_expediture.head(10)

    fig = go.Figure(data = [go.Pie(labels = (top_10_customers["CUSTOMERNAME"].astype(str)).to_list(), 
                                   values = top_10_customers["SALES"].to_list())])

    fig.update_layout(title = "Customer By Expenditure")

    return fig.to_plotly_json()