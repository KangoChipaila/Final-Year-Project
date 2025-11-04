import plotly.graph_objects as go
import pandas as pd
import glob
import matplotlib.pyplot as plt
from prophet import Prophet
from prophet.plot import plot_plotly

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

"""async def generate_sales_forecast():

    import numpy as np
    from sklearn.metrics import mean_squared_error

    # ensure ORDERDATE is datetime
    extracted_data["ORDERDATE"] = pd.to_datetime(extracted_data["ORDERDATE"], errors="coerce")

    # aggregate to daily sales (change 'D' to 'M' if you want monthly)
    daily_sales = (
        extracted_data
        .set_index("ORDERDATE")
        .resample("D")["SALES"]
        .sum()
        .reset_index()
        .rename(columns={"ORDERDATE": "ds", "SALES": "y"})
    )

    # drop rows with NaT or NaN
    daily_sales = daily_sales.dropna(subset=["ds", "y"])

    model = Prophet(
        interval_width=0.95,
        growth="linear",
        daily_seasonality=True,
        weekly_seasonality=True,
        yearly_seasonality=True,
        seasonality_mode="multiplicative",
    )

    model.fit(daily_sales)

    future_pd = model.make_future_dataframe(periods=90, freq="D", include_history=True)

    forecast_pd = model.predict(future_pd)

    # compute RMSE on the historical period where we have observed y
    # merge forecast (yhat) with observed y on ds
    hist = forecast_pd[forecast_pd["ds"] <= daily_sales["ds"].max()][["ds", "yhat"]].copy()
    obs = daily_sales[["ds", "y"]].copy()
    merged = pd.merge(obs, hist, on="ds", how="inner")

    if not merged.empty:
        rmse = float(np.sqrt(mean_squared_error(merged["y"], merged["yhat"])))
    else:
        rmse = float("nan")

    # compute mean of the historical series
    mean_y = float(daily_sales["y"].mean()) if not daily_sales.empty else float("nan")

    # --- additional metrics ---
    import numpy as np
    if not merged.empty and not np.isnan(mean_y) and mean_y != 0:
        nrmse = rmse / mean_y
    else:
        nrmse = float("nan")

    if not merged.empty:
        y_true = merged["y"].to_numpy(dtype=float)
        y_pred = merged["yhat"].to_numpy(dtype=float)
        # baseline: predict historical mean
        baseline_pred = np.full_like(y_true, mean_y, dtype=float)
        baseline_rmse = float(np.sqrt(mean_squared_error(y_true, baseline_pred)))
        # MAPE (ignore zero-true days)
        with np.errstate(divide="ignore", invalid="ignore"):
            mape_vals = np.abs((y_true - y_pred) / np.where(y_true == 0, np.nan, y_true))
            mape = float(np.nanmean(mape_vals) * 100)
    else:
        baseline_rmse = float("nan")
        mape = float("nan")
    # --- end additional metrics ---

    # build figure and annotate RMSE + extra metrics
    fig = plot_plotly(model, forecast_pd)

    # determine x-range for the mean line (use forecast range so line spans whole chart)
    x0 = forecast_pd["ds"].min()
    x1 = forecast_pd["ds"].max()

    # add a dashed horizontal line as a trace (shows in legend) and a shape for crisp styling
    fig.add_trace(go.Scatter(
        x=[x0, x1],
        y=[mean_y, mean_y],
        mode="lines",
        line=dict(color="firebrick", width=2, dash="dash"),
        name="Mean (history)"
    ))

    # optional shape (helps the line render under/over other traces predictably)
    fig.add_shape(
        type="line",
        xref="x", x0=x0, x1=x1,
        yref="y", y0=mean_y, y1=mean_y,
        line=dict(color="firebrick", width=2, dash="dash"),
        layer="above"
    )

    # RMSE annotation (as before)
    fig.add_annotation(
        xref="paper",
        yref="paper",
        x=0.99,
        y=0.01,
        xanchor="right",
        yanchor="bottom",
        text=f"RMSE (history): {rmse:.2f}",
        showarrow=False,
        bgcolor="rgba(255,255,255,0.85)",
        bordercolor="rgba(0,0,0,0.08)",
        borderwidth=1,
        font=dict(size=12),
    )

    # annotation for mean value near the right end of the mean line
    fig.add_annotation(
        x=x1,
        y=mean_y,
        xref="x",
        yref="y",
        text=f"Mean: {mean_y:,.2f}",
        showarrow=True,
        arrowhead=2,
        ax=-40,
        ay=-20,
        bgcolor="rgba(255,255,255,0.85)",
        font=dict(size=11)
    )

    # update/add annotation with the extra metrics (paper coords)
    fig.add_annotation(
        xref="paper",
        yref="paper",
        x=0.99,
        y=0.01,
        xanchor="right",
        yanchor="bottom",
        text=(f"RMSE: {rmse:,.2f} · NRMSE: {nrmse:.2f} ({nrmse*100:.0f}%)"
              f" · Baseline RMSE: {baseline_rmse:,.2f} · MAPE: {mape:.1f}%"),
        showarrow=False,
        bgcolor="rgba(255,255,255,0.9)",
        bordercolor="rgba(0,0,0,0.08)",
        borderwidth=1,
        font=dict(size=11),
    )

    return fig.to_html(full_html=False)
"""

async def generate_sales_forecast():

    # prepare dates / monthly aggregation (keeps your existing logic)
    extracted_data["ORDERDATE"] = pd.to_datetime(extracted_data["ORDERDATE"], errors="coerce")
    extracted_data["Year"] = extracted_data["ORDERDATE"].dt.to_period("D")
    monthly_sales = extracted_data.groupby("Year")["SALES"].sum().reset_index()
    monthly_sales.columns = ["ds", "y"]
    monthly_sales["ds"] = monthly_sales["ds"].dt.to_timestamp()

    # fit Prophet
    model = Prophet(
        interval_width=0.95,
        growth='linear',
        daily_seasonality=True,
        weekly_seasonality=True,
        yearly_seasonality=True,
        seasonality_mode='multiplicative'
    )
    model.fit(monthly_sales)

    # forecast
    future_pd = model.make_future_dataframe(periods=90, freq='D', include_history=True)
    forecast_pd = model.predict(future_pd)

    # compute rmse on the historical period by merging observed monthly y with forecast yhat
    from sklearn.metrics import mean_squared_error
    import numpy as np

    hist = forecast_pd[forecast_pd["ds"] <= monthly_sales["ds"].max()][["ds", "yhat"]].copy()
    obs = monthly_sales[["ds", "y"]].copy()
    merged = pd.merge(obs, hist, on="ds", how="inner")

    if not merged.empty:
        rmse = float(np.sqrt(mean_squared_error(merged["y"], merged["yhat"])))
        mean_y = float(monthly_sales["y"].mean()) if not monthly_sales.empty else float("nan")
        nrmse = rmse / mean_y if (mean_y and not np.isnan(mean_y)) else float("nan")
        # baseline = historical mean
        y_true = merged["y"].to_numpy(dtype=float)
        baseline_pred = np.full_like(y_true, mean_y, dtype=float)
        baseline_rmse = float(np.sqrt(mean_squared_error(y_true, baseline_pred)))
        with np.errstate(divide="ignore", invalid="ignore"):
            mape_vals = np.abs((y_true - merged["yhat"].to_numpy(dtype=float)) / np.where(y_true == 0, np.nan, y_true))
            mape = float(np.nanmean(mape_vals) * 100)
    else:
        rmse = nrmse = baseline_rmse = mape = mean_y = float("nan")

    # build plotly figure and annotate
    fig = plot_plotly(model, forecast_pd)

    # mean line spanning the forecast x-range
    x0 = forecast_pd["ds"].min()
    x1 = forecast_pd["ds"].max()
    fig.add_trace(go.Scatter(
        x=[x0, x1],
        y=[mean_y, mean_y],
        mode="lines",
        line=dict(color="firebrick", width=2, dash="dash"),
        name="Mean (history)"
    ))
    fig.add_shape(
        type="line",
        xref="x", x0=x0, x1=x1,
        yref="y", y0=mean_y, y1=mean_y,
        line=dict(color="firebrick", width=2, dash="dash"),
        layer="above"
    )

    # annotation with metrics (paper coordinates)
    metrics_text = (f"RMSE: {rmse:,.2f} · NRMSE: {nrmse:.2f} · Baseline RMSE: {baseline_rmse:,.2f} · MAPE: {mape:.1f}%")
    fig.add_annotation(
        xref="paper", yref="paper", x=0.99, y=0.01, xanchor="right", yanchor="bottom",
        text=metrics_text, showarrow=False,
        bgcolor="rgba(255,255,255,0.9)", bordercolor="rgba(0,0,0,0.08)", borderwidth=1, font=dict(size=11)
    )

    # small arrow/label at the right end of the mean line
    fig.add_annotation(
        x=x1, y=mean_y, xref="x", yref="y",
        text=f"Mean: {mean_y:,.2f}", showarrow=True, arrowhead=2, ax=-40, ay=-20,
        bgcolor="rgba(255,255,255,0.85)", font=dict(size=11)
    )

    return fig.to_html(full_html=False)
