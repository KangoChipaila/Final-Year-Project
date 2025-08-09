import plotly.graph_objects as go
import pandas as pd
import matplotlib.pyplot as plt
from prophet import Prophet
from prophet.plot import plot_plotly

extracted_data = pd.read_csv("./spreadsheet_datasets/sales_data_sample.csv", encoding="cp1252")

def generate_sales_trend():

    extracted_data["ORDERDATE"] = pd.to_datetime(extracted_data["ORDERDATE"], format = "%m/%d/%Y %H:%M", errors = "coerce")

    extracted_data["Day"] = extracted_data["ORDERDATE"].dt.to_period("D")
    extracted_data["Month"] = extracted_data["ORDERDATE"].dt.to_period("M")
    extracted_data["Year"] = extracted_data["ORDERDATE"].dt.to_period("Y")

    daily_sales = extracted_data.groupby("Day")["SALES"].sum().reset_index()
    monthly_sales = extracted_data.groupby("Month")["SALES"].sum().reset_index()
    yearly_sales = extracted_data.groupby("Year")["SALES"].sum().reset_index()

    fig = go.Figure()

    fig.add_trace(go.Scatter(x = daily_sales["Day"].astype(str).to_list(), 
                             y = (daily_sales["SALES"]/1000).to_list(), 
                             mode = 'lines + markers'))
    
    fig.add_trace(go.Scatter(x = monthly_sales["Month"].astype(str).to_list(), 
                             y = (monthly_sales["SALES"]/1000).to_list(), 
                             mode = 'lines + markers',
                             visible = False))
    
    fig.add_trace(go.Scatter(x = yearly_sales["Year"].astype(str).to_list(), 
                             y = (yearly_sales["SALES"]/1000).to_list(), 
                             mode = 'lines + markers',
                             visible = False))
    
    fig.update_layout(
        updatemenus=[
            dict(
                active=0,
                buttons=list([
                    dict(label="Daily",
                        method="update",
                            args=[{"visible": [True, False, False]}, 
                                  {"title": "Daily Sales Trend"}]),
                    dict(label = "Monthly",
                         method = "update",
                         args = [{"visible": [False, True, False]},
                                 {"title": "Monthly Sales Trend"}]),
                    dict(label="Yearly",
                        method="update",
                            args=[{"visible": [False, False, True]}, 
                                  {"title": "Yearly Sales Trend"}])
                        ])
                    )
                ])


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

    fig.update_layout(title = "Top 10 Customers By Expenditure")

    return fig.to_plotly_json()

async def generate_sales_forecast():

    extracted_data["ORDERDATE"] = pd.to_datetime(extracted_data["ORDERDATE"], errors = "coerce")

    extracted_data["Year"] = extracted_data["ORDERDATE"].dt.to_period("M")

    # Grouping data by Year-Month and calculating total sales
    monthly_sales = extracted_data.groupby("Year")["SALES"].sum().reset_index()

    monthly_sales.columns = ["ds", "y"]
    monthly_sales["ds"] = monthly_sales["ds"].to_list()
    monthly_sales["y"] = monthly_sales["y"].to_list()


    monthly_sales["ds"] = monthly_sales["ds"].dt.to_timestamp()

    model = Prophet(
        interval_width = 0.95,
        growth = 'linear',
        daily_seasonality = True,
        weekly_seasonality = True,
        yearly_seasonality = True,
        seasonality_mode = 'multiplicative'
    )

    model.fit(monthly_sales)

    future_pd = model.make_future_dataframe(
        periods = 7,
        freq ='ME',
        include_history = True
    )

    # predict over the dataset
    forecast_pd = model.predict(future_pd)

    #fig = model.plot(forecast_pd, xlabel='date', ylabel='sales')
    
    fig = plot_plotly(model, forecast_pd)

    return fig.to_plotly_json()