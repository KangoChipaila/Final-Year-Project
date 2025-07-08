import plotly.graph_objects as go
import pandas as pd

def generate_sales_trend():
    extracted_data = pd.read_csv("./spreadsheet_datasets/sales_data_sample.csv", encoding="cp1252")

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
