import plotly.graph_objects as go
import pandas as pd
import glob
import matplotlib.pyplot as plt
from prophet import Prophet
from prophet.plot import plot_plotly
from sklearn.metrics import mean_squared_error
import numpy as np

#extracted_data = pd.read_csv("./spreadsheet_datasets/sales_data_sample.csv", encoding="cp1252")

directory = "./spreadsheet_datasets"

files = glob.glob(directory + "/*.csv")

df = [pd.read_csv(filename, encoding = "cp1252") for filename in files]

extracted_data = pd.concat(df, ignore_index = True)

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
        title = "Daily Sales Trend",
        yaxis_title = "1 = K1000"
    )
    
    fig.update_layout(
        updatemenus=[
            dict(
                active=0,
                yanchor = "top",
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

def generate_sales_forecast():

    import asyncio

    # Ensure datetime and drop bad rows
    extracted_data["ORDERDATE"] = pd.to_datetime(extracted_data["ORDERDATE"], errors="coerce")
    df_clean = extracted_data.dropna(subset=["ORDERDATE", "SALES"]).copy()

    # Aggregate by week
    df_clean["Day"] = df_clean["ORDERDATE"].dt.to_period("D")
    daily_sales = df_clean.groupby("Day")["SALES"].sum().reset_index()

    # Prepare Prophet columns
    daily_sales = daily_sales.rename(columns={"Day": "ds", "SALES": "y"})
    daily_sales["ds"] = daily_sales["ds"].dt.to_timestamp()   # convert Period -> Timestamp
    daily_sales = daily_sales.sort_values("ds").reset_index(drop=True)
    daily_sales["y"] = daily_sales["y"].astype(float)

    df_train_data = daily_sales.iloc[:200]
    df_test_data = daily_sales.iloc[200:]

    print(len(df_train_data))

    """model = Prophet(
        changepoint_prior_scale=0.05,
        interval_width=0.95,
        daily_seasonality=False,
        weekly_seasonality=True,
        yearly_seasonality=False
    )"""

    model = Prophet(
        growth='linear',
        daily_seasonality=True,
        weekly_seasonality=True,
        yearly_seasonality=True,
        seasonality_mode='multiplicative'
    )

    model.fit(df_train_data)

    df_future = model.make_future_dataframe(periods=12, freq='D')

    forecast_prophet = model.predict(df_future)

    forecast_prophet[['ds', 'yhat', 'yhat_lower', 'yhat_upper']].round().tail()

    # plot the time series 
    forecast_plot = model.plot(forecast_prophet)

    # add a vertical line at the end of the training period
    axes = forecast_plot.gca()
    last_training_date = forecast_prophet['ds'].max()
    axes.axvline(x=last_training_date, color='red', linestyle='--', label='Training End')

    # plot true test data for the period after the red line
    #df_test_data['Month'] = pd.to_datetime(df_test_data['Month'])
    plt.plot(df_test_data['ds'], df_test_data['y'],'ro', markersize=3, label='True Test Data')

    # show the legend to distinguish between the lines
    plt.legend()
    plt.show()

    from prophet.diagnostics import cross_validation, performance_metrics

    df_cv = cross_validation(model, initial="365 days", period="90 days", horizon="90 days")
    df_p = performance_metrics(df_cv)
    print(df_p[['horizon','rmse','mae','mape']])
    
    """
    # Fit in executor to avoid blocking the event loop
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, lambda: model.fit(weekly_sales[['ds', 'y']]))
    
    from prophet.diagnostics import cross_validation, performance_metrics

    df_cv = cross_validation(model, initial="365 days", period="90 days", horizon="90 days")
    df_p = performance_metrics(df_cv)
    print(df_p[['horizon','rmse','mae','mape']])"""
    """
    # Build future and predict in executor
    future_pd = model.make_future_dataframe(periods=30, freq='W', include_history=True)
    forecast_pd = await loop.run_in_executor(None, lambda: model.predict(future_pd))

    # Compute in-sample RMSE (compare historical y to fitted yhat)
    hist = forecast_pd[['ds', 'yhat']].merge(weekly_sales[['ds', 'y']], on='ds', how='inner')
    if not hist.empty:
        rmse = float(np.sqrt(mean_squared_error(hist['y'], hist['yhat'])))
    else:
        rmse = float('nan')

    # Compute mean and NRMSE (normalized RMSE as RMSE / mean -> shown as percentage)
    mean_sales = float(weekly_sales['y'].mean()) if not weekly_sales['y'].empty else float('nan')
    if mean_sales and not np.isnan(mean_sales) and mean_sales != 0:
        nrmse_pct = 100.0 * rmse / mean_sales
    else:
        nrmse_pct = float('nan')

    # Plot and include metrics in an annotation on the figure
    fig = plot_plotly(model, forecast_pd)
    metrics_text = f"Mean: {mean_sales:.2f}<br>RMSE: {rmse:.2f}<br>NRMSE: {nrmse_pct:.2f}%"
    fig.update_layout(title="Weekly Sales Forecast")
    fig.add_annotation(
        x=0.01, y=0.99, xref="paper", yref="paper",
        text=metrics_text,
        showarrow=False, align="left",
        bordercolor="black", borderwidth=1, bgcolor="white"
    )

    return fig.to_html(full_html=False)
"""
generate_sales_forecast()