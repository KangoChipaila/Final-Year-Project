import plotly.graph_objects as go
import pandas as pd
from statsmodels.tsa.stattools import adfuller
from statsmodels.tsa.arima.model import ARIMA
import matplotlib.pyplot as plt
import pmdarima as pm
from statsmodels.graphics.tsaplots import plot_acf, plot_pacf

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
    
    
    
    """
    extracted_data["SALES"] = pd.to_numeric(extracted_data["SALES"], errors="coerce")

    sales_data = extracted_data["SALES"]

    sales_data = sales_data.dropna()

    #sales_data = sales_data[(sales_data.index < len(sales_data) - 2000)]
    
    msk = (sales_data.index < len(sales_data) - 200)
    train_set = sales_data[msk].copy()
    test_set = sales_data[~msk].copy()

    sales_series = train_set.diff().dropna()

    plot_pacf(train_set)
    plt.show()

    #sales_series = train_set.diff().dropna()
    
    if len(sales_series) > 0 and sales_series.nunique() > 1:
        adf_test = adfuller(sales_series)
        print(f'p-value: {adf_test[1]}')

        if adf_test[1] > 0.05:
            print("The data is not stationary")
        else:
            print("The data is stationary")

    else:
        print("ADF test cannot run: not enough data or series is constant
    
    model = ARIMA(train_set, order=[0, 1, 5])
    model_fit = model.fit()

    
    residuals = model_fit.resid[1:]
    fig, ax = plt.subplots(1,2)
    residuals.plot(title = "Residuals", ax=ax[0])
    residuals.plot(title = "Density", kind = "kde", ax=ax[1])
    plt.show

    auto_arima = pm.auto_arima(train_set, stepwise='false', seasonal='false')
    print(auto_arima

    forecast_test = model_fit.forecast(len(test_set))
    #sales_data["forecast_manual"] = [None] * len(train_set) + list(forecast_test)
    
    plot_df = pd.DataFrame({
        "actual": sales_data,
        "forecast_manual": [None] * len(train_set) + list(forecast_test)
    })

    plot_df.plot()
    plt.show()
    """

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

generate_sales_trend()