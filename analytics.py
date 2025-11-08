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

async def generate_sales_forecast(forecast_periods: int = 50, freq: str = "D", train_size: int = 200):
    """
    Better, safer Prophet forecasting + metrics and interactive Plotly output.
    Returns HTML string (plotly) containing:
      - actuals, fitted (in-sample) and future forecast
      - test points overlay
      - vertical line marking training end
      - annotation with Mean / RMSE / NRMSE
    """
    # Prepare data
    extracted_data["ORDERDATE"] = pd.to_datetime(extracted_data["ORDERDATE"], errors="coerce")
    df_clean = extracted_data.dropna(subset=["ORDERDATE", "SALES"]).copy()

    # Make SALES numeric (strip currency/commas then to numeric)
    df_clean["SALES"] = (
        df_clean["SALES"].astype(str)
        .str.replace(r"[^\d\.-]", "", regex=True)   # remove non-numeric chars
        .replace("", "0")
    )
    df_clean["SALES"] = pd.to_numeric(df_clean["SALES"], errors="coerce").fillna(0.0)

    # Aggregate only dates that actually have transactions (avoid filling entire date range with zeros)
    daily = (
        df_clean
        .assign(ds=df_clean["ORDERDATE"].dt.normalize())   # midnight timestamp for the day
        .groupby("ds", as_index=False)["SALES"]
        .sum()
        .rename(columns={"SALES": "y"})
    )
    daily = daily.sort_values("ds").reset_index(drop=True)
    daily["y"] = daily["y"].astype(float)

    # Optionally remove days with zero sales (if zeros represent no activity and you don't want them modeled)
    # daily = daily[daily["y"] != 0].reset_index(drop=True)

    if daily.empty:
        raise ValueError("No valid daily sales rows after preprocessing.")

    # Train/test split (time order)
    train_end = min(train_size, len(daily) - 1)
    df_train = daily.iloc[:train_end].copy()
    df_test = daily.iloc[train_end:].copy()

    # Configure model (adjust seasonality depending on history length)
    enable_yearly = len(daily) >= 365 * 2  # only enable yearly if ~2+ years of data
    model = Prophet(
        interval_width=0.95,
        daily_seasonality=False,     # for daily-aggregated series, set False
        weekly_seasonality=True,
        yearly_seasonality=enable_yearly,
        seasonality_mode="multiplicative",
        changepoint_prior_scale=0.05
    )

    # Fit on training data
    model.fit(df_train[["ds", "y"]])

    # Forecast (include history so we get fitted yhat for train)
    future = model.make_future_dataframe(periods=forecast_periods, freq=freq, include_history=True)
    forecast = model.predict(future)

    # Compute in-sample fitted RMSE (compare model yhat for training dates)
    fitted = forecast[forecast["ds"].isin(df_train["ds"])][["ds", "yhat"]]
    merged = pd.merge(df_train[["ds", "y"]], fitted, on="ds", how="inner")
    if not merged.empty:
        rmse = float(np.sqrt(mean_squared_error(merged["y"], merged["yhat"])))
    else:
        rmse = float("nan")

    mean_sales = float(df_train["y"].mean()) if not df_train["y"].empty else float("nan")
    nrmse_pct = (100.0 * rmse / mean_sales) if (mean_sales and not np.isnan(mean_sales)) else float("nan")

    # Build interactive Plotly figure (Plotly graph from Prophet)
    fig = plot_plotly(model, forecast)

    # Add vertical line at end of training set
    last_train_date = df_train["ds"].max()
    fig.update_layout(
        shapes=[
            dict(type="line", x0=last_train_date, x1=last_train_date, xref="x",
                 y0=0, y1=1, yref="paper", line=dict(color="red", dash="dash"))
        ]
    )

    # Overlay true test points (red markers) for the period after training end
    if not df_test.empty:
        fig.add_trace(go.Scatter(
            x=df_test["ds"],
            y=df_test["y"],
            mode="markers",
            marker=dict(color="red", size=6),
            name="True test points"
        ))

    # Add metrics annotation
    metrics_text = f"Mean: {mean_sales:.2f}<br>RMSE: {rmse:.2f}<br>NRMSE: {nrmse_pct:.2f}%"
    fig.add_annotation(
        x=0.01, y=0.99, xref="paper", yref="paper",
        text=metrics_text, showarrow=False, align="left",
        bordercolor="black", borderwidth=1, bgcolor="white"
    )

    # Tidy layout
    fig.update_layout(title="Daily Sales Forecast (Prophet)", xaxis_title="Date", yaxis_title="Sales",
                      legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1))

    # Return HTML so this works in scripts, web servers, notebooks
    return fig.to_html(full_html=False)
