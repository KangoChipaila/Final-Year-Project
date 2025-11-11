import json
import math
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.utils import PlotlyJSONEncoder
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_squared_error
import glob
import matplotlib.pyplot as plt

train_data = pd.read_csv("./data/train.csv", encoding="cp1252")
test_data = pd.read_csv("./data/test.csv", encoding="cp1252")

directory = "./spreadsheet_datasets"

files = glob.glob(directory + "/*.csv")

df = [pd.read_csv(filename, encoding = "cp1252") for filename in files]

extracted_data = pd.concat(df, ignore_index = True)

def generate_sales_trend():

    extracted_data["ORDERDATE"] = pd.to_datetime(extracted_data["ORDERDATE"], format = "%m/%d/%Y %H:%M")

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

def generate_sales_forecast(extracted_data=None, forecast_periods: int = 12, min_history: int = 40):
    """
    Adaptive sales forecast.
    - If daily coverage < 0.6, switch to weekly aggregation (forecast_periods then means weeks).
    - Optional interpolation for missing daily gaps before aggregation.
    """
    if extracted_data is None:
        extracted_data = globals().get("extracted_data")
        if extracted_data is None:
            fig = go.Figure()
            fig.update_layout(title="Sales Forecast (no data)")
            return json.loads(json.dumps(fig, cls=PlotlyJSONEncoder))

    df_raw = extracted_data.copy()
    if "ORDERDATE" not in df_raw.columns or "SALES" not in df_raw.columns:
        fig = go.Figure()
        fig.update_layout(title="Sales Forecast (missing columns)")
        return json.loads(json.dumps(fig, cls=PlotlyJSONEncoder))

    df_raw["ORDERDATE"] = pd.to_datetime(df_raw["ORDERDATE"])
    df_raw = df_raw.dropna(subset=["ORDERDATE"])
    sales_clean = (
        df_raw["SALES"].astype(str)
        .str.replace(r"[^\d\.-]", "", regex=True)
        .replace("", "0")
    )
    df_raw["SALES"] = pd.to_numeric(sales_clean).fillna(0.0)

    # Daily aggregate
    df_daily = (df_raw
                .assign(ds=df_raw["ORDERDATE"].dt.normalize())
                .groupby("ds", as_index=False)["SALES"].sum()
                .rename(columns={"SALES": "y"})
                .sort_values("ds"))
    if df_daily.empty:
        fig = go.Figure()
        fig.update_layout(title="Sales Forecast (no rows)")
        return json.loads(json.dumps(fig, cls=PlotlyJSONEncoder))

    full_range = pd.date_range(df_daily["ds"].min(), df_daily["ds"].max(), freq="D")
    coverage = len(df_daily) / len(full_range)

    use_weekly = coverage < 0.60  # threshold
    granularity = "Weekly" if use_weekly else "Daily"

    if use_weekly:
        # Reindex, interpolate missing days before weekly aggregation
        df_daily = df_daily.set_index("ds").reindex(full_range)
        df_daily.index.name = "ds"
        # Linear interpolation then fill remaining NaNs with 0
        df_daily["y"] = df_daily["y"].interpolate(limit_direction="both").fillna(0.0)
        df_weekly = (df_daily
                     .resample("W-MON")["y"]
                     .sum()
                     .to_frame()
                     .reset_index())
        series_df = df_weekly.rename(columns={"ds": "date", "y": "value"})
    else:
        # Daily continuous
        df_daily = df_daily.set_index("ds").reindex(full_range)
        df_daily.index.name = "ds"
        df_daily["y"] = df_daily["y"].fillna(0.0)
        series_df = df_daily.reset_index().rename(columns={"ds": "date", "y": "value"})

    if len(series_df) < min_history:
        fig = go.Figure()
        fig.update_layout(title=f"Sales Forecast ({granularity} insufficient history)")
        return json.loads(json.dumps(fig, cls=PlotlyJSONEncoder))

    # Outlier capping
    q1, q3 = series_df["value"].quantile([0.25, 0.75])
    iqr = q3 - q1
    upper_cap = q3 + 1.5 * iqr
    series_df["value"] = np.clip(series_df["value"], 0, upper_cap)

    # Features
    n_lags = 8 if use_weekly else 14
    data = series_df.copy()
    for lag in range(1, n_lags + 1):
        data[f"lag_{lag}"] = data["value"].shift(lag)

    # Rolling stats
    roll_short = 4 if use_weekly else 7
    roll_long = 8 if use_weekly else 14
    data["roll_mean_short"] = data["value"].rolling(roll_short).mean()
    data["roll_mean_long"] = data["value"].rolling(roll_long).mean()

    # Cyclical encodings
    if use_weekly:
        # week number cyclical
        weeknum = data["date"].dt.isocalendar().week.astype(int)
        data["week_sin"] = np.sin(2 * np.pi * weeknum / 52)
        data["week_cos"] = np.cos(2 * np.pi * weeknum / 52)
        time_features = ["week_sin", "week_cos"]
    else:
        dow = data["date"].dt.dayofweek
        month = data["date"].dt.month
        data["dow_sin"] = np.sin(2 * np.pi * dow / 7)
        data["dow_cos"] = np.cos(2 * np.pi * dow / 7)
        data["month_sin"] = np.sin(2 * np.pi * month / 12)
        data["month_cos"] = np.cos(2 * np.pi * month / 12)
        time_features = ["dow_sin", "dow_cos", "month_sin", "month_cos"]

    data = data.dropna().copy()
    if len(data) < min_history:
        fig = go.Figure()
        fig.update_layout(title=f"Sales Forecast ({granularity} post-feature insufficient)")
        return json.loads(json.dumps(fig, cls=PlotlyJSONEncoder))

    feature_cols = [f"lag_{l}" for l in range(1, n_lags + 1)] + ["roll_mean_short", "roll_mean_long"] + time_features

    y_all = np.log1p(data["value"].values)
    X_all = data[feature_cols].values
    dates_all = data["date"].to_list()

    # Walk-forward evaluation
    start_test_idx = int(len(data) * 0.8)
    start_test_idx = max(start_test_idx, n_lags + 3)

    preds_log = []
    actual_log = []
    dates_test = []
    residuals_log = []

    try:
        import lightgbm as lgb
        GBModel = lgb.LGBMRegressor
        gb_params = dict(n_estimators=600, learning_rate=0.03, subsample=0.9, colsample_bytree=0.8, random_state=42)
        model_name = "LightGBM"
    except ImportError:
        from sklearn.ensemble import HistGradientBoostingRegressor
        GBModel = HistGradientBoostingRegressor
        gb_params = dict(max_depth=None, learning_rate=0.05, max_iter=400, random_state=42)
        model_name = "HistGB"

    for i in range(start_test_idx, len(data)):
        X_train = X_all[:i]
        y_train = y_all[:i]
        X_curr = X_all[i].reshape(1, -1)
        y_curr = y_all[i]
        m = GBModel(**gb_params)
        m.fit(X_train, y_train)
        pred_log = float(m.predict(X_curr)[0])
        preds_log.append(pred_log)
        actual_log.append(float(y_curr))
        dates_test.append(dates_all[i])
        residuals_log.append(y_curr - pred_log)

    if preds_log:
        preds = np.expm1(preds_log)
        actual = np.expm1(actual_log)
        rmse = math.sqrt(mean_squared_error(actual, preds))
        mean_actual = float(actual.mean())
        nrmse = rmse / mean_actual if mean_actual > 0 else None
        resid_std_log = float(np.std(residuals_log, ddof=1)) if len(residuals_log) > 1 else 0.0
    else:
        rmse = None
        mean_actual = None
        nrmse = None
        resid_std_log = 0.0

    # Final model
    final_model = GBModel(**gb_params)
    final_model.fit(X_all, y_all)

    # Future forecasts
    last_date = dates_all[-1]
    future_dates = []
    future_vals = []
    synthetic = list(np.expm1(y_all))  # original scale

    for step in range(1, forecast_periods + 1):
        next_date = last_date + (pd.Timedelta(weeks=step) if use_weekly else pd.Timedelta(days=step))
        future_dates.append(next_date)

        # Build feature row
        recent_log = np.log1p(synthetic)
        feat = []
        for lag in range(1, n_lags + 1):
            feat.append(recent_log[-lag] if len(recent_log) >= lag else recent_log[-1])

        roll_s = np.mean(synthetic[-roll_short:]) if len(synthetic) >= roll_short else np.mean(synthetic)
        roll_l = np.mean(synthetic[-roll_long:]) if len(synthetic) >= roll_long else roll_s
        feat.extend([np.log1p(roll_s), np.log1p(roll_l)])

        if use_weekly:
            weeknum = next_date.isocalendar().week
            feat.extend([
                math.sin(2 * math.pi * weeknum / 52),
                math.cos(2 * math.pi * weeknum / 52)
            ])
        else:
            dow_f = next_date.dayofweek
            month_f = next_date.month
            feat.extend([
                math.sin(2 * math.pi * dow_f / 7),
                math.cos(2 * math.pi * dow_f / 7),
                math.sin(2 * math.pi * month_f / 12),
                math.cos(2 * math.pi * month_f / 12)
            ])

        pred_next_log = float(final_model.predict(np.array(feat).reshape(1, -1))[0])
        pred_next = float(np.expm1(pred_next_log))
        future_vals.append(pred_next)
        synthetic.append(pred_next)

    resid_std = float(np.expm1(resid_std_log)) if resid_std_log > 0 else 0.0
    upper = [v + 1.96 * resid_std for v in future_vals]
    lower = [max(0.0, v - 1.96 * resid_std) for v in future_vals]

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=[d.strftime("%Y-%m-%d") for d in dates_all],
        y=[float(v) for v in np.expm1(y_all)],
        mode="lines",
        name=f"Historical ({granularity})",
        line=dict(color="#222")
    ))
    if preds_log:
        fig.add_trace(go.Scatter(
            x=[d.strftime("%Y-%m-%d") for d in dates_test],
            y=[float(v) for v in preds],
            mode="lines+markers",
            name="Validation (walk-forward)",
            line=dict(color="orange", dash="dot"),
            marker=dict(symbol="x", size=6)
        ))
    fig.add_trace(go.Scatter(
        x=[d.strftime("%Y-%m-%d") for d in future_dates],
        y=[float(v) for v in future_vals],
        mode="lines+markers",
        name="Forecast",
        line=dict(color="blue", dash="dash")
    ))
    if resid_std > 0:
        fd = [d.strftime("%Y-%m-%d") for d in future_dates]
        fig.add_trace(go.Scatter(
            x=fd + fd[::-1],
            y=upper + lower[::-1],
            fill="toself",
            fillcolor="rgba(0,116,217,0.15)",
            line=dict(color="rgba(255,255,255,0)"),
            hoverinfo="skip",
            name="Approx. 95% band"
        ))

    metrics = [
        f"Granularity: {granularity}",
        f"Model: {model_name}",
        f"Coverage (daily): {coverage:.2f}",
        f"RMSE: {rmse:.2f}" if rmse else "RMSE: N/A",
        f"Mean(test): {mean_actual:.2f}" if mean_actual else "Mean(test): N/A",
        f"NRMSE: {nrmse:.3f}" if nrmse else "NRMSE: N/A"
    ]

    fig.update_layout(
        title=f"Sales Forecast ({granularity}, adaptive) — next {forecast_periods} {('weeks' if use_weekly else 'days')}",
        xaxis_title="Date",
        yaxis_title="Sales",
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        annotations=[dict(
            xref="paper", yref="paper", x=1.02, y=0.98,
            xanchor="left", yanchor="top",
            text="<br>".join(metrics),
            showarrow=False,
            align="left",
            bordercolor="rgba(0,0,0,0.15)",
            borderwidth=1,
            bgcolor="rgba(255,255,255,0.92)",
            font=dict(size=11)
        )],
        margin=dict(r=180)
    )

    return json.loads(json.dumps(fig, cls=PlotlyJSONEncoder))

def quick_sales_data_audit(df):
    """Return basic data quality stats for ORDERDATE/SALES."""
    out = {}
    if df is None or df.empty:
        return {"error": "empty dataframe"}

    n_total = len(df)
    d = df.copy()

    # Date parsing
    d["ORDERDATE_parsed"] = pd.to_datetime(d["ORDERDATE"], infer_datetime_format=True)
    n_valid_dates = d["ORDERDATE_parsed"].notna().sum()
    out["rows_total"] = int(n_total)
    out["rows_with_valid_dates"] = int(n_valid_dates)
    out["date_parse_drop_rate"] = float((n_total - n_valid_dates) / max(1, n_total))

    # SALES numeric coercion
    s_clean = (
        d["SALES"].astype(str)
        .str.replace(r"[^\d\.\-\,]", "", regex=True)
        .str.replace(",", "", regex=False)
    )
    sales_num = pd.to_numeric(s_clean)
    n_sales_invalid = sales_num.isna().sum()
    out["sales_invalid_rate"] = float(n_sales_invalid / max(1, n_total))

    # Daily aggregation and coverage
    daily = (
        pd.DataFrame({"ds": d["ORDERDATE_parsed"], "y": sales_num})
        .dropna(subset=["ds"])
        .assign(ds=lambda x: x["ds"].dt.normalize())
        .groupby("ds", as_index=False)["y"].sum()
        .sort_values("ds")
    )
    if daily.empty:
        out["error"] = "no daily data after parsing"
        return out

    span_days = int((daily["ds"].max() - daily["ds"].min()).days) + 1
    observed_days = int(daily.shape[0])
    coverage = observed_days / max(1, span_days)

    out["daily_span_days"] = span_days
    out["observed_days"] = observed_days
    out["day_coverage"] = float(coverage)

    # Zero share and outliers
    y = daily["y"].clip(lower=0)
    zero_share = float((y == 0).mean())
    q1, q3 = y.quantile([0.25, 0.75])
    iqr = q3 - q1
    upper_cap = q3 + 1.5 * iqr if pd.notna(iqr) else y.max()
    outlier_share = float((y > upper_cap).mean())

    out["zero_day_share"] = zero_share
    out["outlier_day_share"] = outlier_share
    out["mean_daily_sales"] = float(y.mean())

    return out

"""def generate_sales_forecast(extracted_data=None, forecast_periods: int = 12, min_history: int = 30, top_segments: int = 6):
   
    Per PRODUCTLINE hierarchical forecast aggregated to total.
    - Select top N segments by total sales.
    - Forecast each (weekly if sparse, else daily).
    - Sum future predictions; compute global walk-forward error.
    Returns Plotly figure (dict).
    
    if extracted_data is None:
        extracted_data = globals().get("extracted_data")
        if extracted_data is None:
            fig = go.Figure()
            fig.update_layout(title="Segment Forecast (no data)")
            return json.loads(json.dumps(fig, cls=PlotlyJSONEncoder))

    if "ORDERDATE" not in extracted_data.columns or "SALES" not in extracted_data.columns or "PRODUCTLINE" not in extracted_data.columns:
        fig = go.Figure()
        fig.update_layout(title="Segment Forecast (missing columns)")
        return json.loads(json.dumps(fig, cls=PlotlyJSONEncoder))

    df = extracted_data.copy()
    df["ORDERDATE"] = pd.to_datetime(df["ORDERDATE"], errors="coerce")
    df = df.dropna(subset=["ORDERDATE"])
    df["SALES"] = (
        df["SALES"].astype(str)
        .str.replace(r"[^\d\.-]", "", regex=True)
        .replace("", "0")
    )
    df["SALES"] = pd.to_numeric(df["SALES"], errors="coerce").fillna(0.0)

    seg_totals = (
        df.groupby("PRODUCTLINE")["SALES"]
        .sum()
        .sort_values(ascending=False)
        .head(top_segments)
    )
    segments = seg_totals.index.tolist()
    if not segments:
        fig = go.Figure()
        fig.update_layout(title="Segment Forecast (no segments)")
        return json.loads(json.dumps(fig, cls=PlotlyJSONEncoder))

    try:
        import lightgbm as lgb
        GB = lgb.LGBMRegressor
        gb_params = dict(n_estimators=500, learning_rate=0.05, subsample=0.8, colsample_bytree=0.8, random_state=42)
        model_label = "LightGBM"
    except ImportError:
        from sklearn.ensemble import HistGradientBoostingRegressor
        GB = HistGradientBoostingRegressor
        gb_params = dict(max_depth=None, learning_rate=0.05, max_iter=400, random_state=42)
        model_label = "HistGB"

    total_future_sum = np.zeros(forecast_periods)
    segment_traces = []
    metrics_rows = []
    global_actual = []
    global_pred = []

    for seg in segments:
        seg_df = df[df["PRODUCTLINE"] == seg].copy()
        daily = (
            seg_df.assign(ds=seg_df["ORDERDATE"].dt.normalize())
            .groupby("ds", as_index=False)["SALES"]
            .sum()
            .rename(columns={"SALES": "y"})
            .sort_values("ds")
        )
        if daily.empty or len(daily) < min_history:
            continue

        full_range = pd.date_range(daily["ds"].min(), daily["ds"].max(), freq="D")
        coverage = len(daily) / len(full_range)
        use_weekly = coverage < 0.6

        daily = daily.set_index("ds").reindex(full_range)
        daily.index.name = "ds"
        daily["y"] = daily["y"].fillna(0.0)

        # Outlier cap
        q1, q3 = daily["y"].quantile([0.25, 0.75])
        iqr = q3 - q1
        upper_cap = q3 + 1.5 * iqr
        daily["y"] = np.clip(daily["y"], 0, upper_cap)

        if use_weekly:
            freq_label = "Weekly"
            series = (
                daily.resample("W-MON")["y"]
                .sum()
                .to_frame()
                .reset_index()
                .rename(columns={"ds": "date", "y": "value"})
            )
        else:
            freq_label = "Daily"
            series = daily.reset_index().rename(columns={"ds": "date", "y": "value"})

        if len(series) < min_history:
            continue

        # Features (lags selection)
        lags = [1,2,3,7] if not use_weekly else [1,2,3]
        for lag in lags:
            series[f"lag_{lag}"] = series["value"].shift(lag)

        series["roll_mean_short"] = series["value"].rolling(7 if not use_weekly else 2).mean()
        series["roll_mean_long"]  = series["value"].rolling(14 if not use_weekly else 4).mean()

        if use_weekly:
            weeknum = series["date"].dt.isocalendar().week.astype(int)
            series["week_sin"] = np.sin(2 * np.pi * weeknum / 52)
            series["week_cos"] = np.cos(2 * np.pi * weeknum / 52)
            time_feats = ["week_sin", "week_cos"]
        else:
            dow = series["date"].dt.dayofweek
            month = series["date"].dt.month
            series["dow_sin"] = np.sin(2 * np.pi * dow / 7)
            series["dow_cos"] = np.cos(2 * np.pi * dow / 7)
            series["month_sin"] = np.sin(2 * np.pi * month / 12)
            series["month_cos"] = np.cos(2 * np.pi * month / 12)
            time_feats = ["dow_sin","dow_cos","month_sin","month_cos"]

        series["time_idx"] = np.arange(len(series))/max(1,len(series))

        series = series.dropna().copy()
        feat_cols = [f"lag_{l}" for l in lags] + ["roll_mean_short","roll_mean_long","time_idx"] + time_feats
        if len(series) < min_history or any(c not in series.columns for c in feat_cols):
            continue

        y_log = np.log1p(series["value"].values)
        X = series[feat_cols].values
        dates = series["date"].tolist()

        split = int(len(series)*0.8)
        split = max(split, max(lags)+2)
        preds_log = []
        actual_log = []
        test_dates = []
        residuals_log = []

        for i in range(split, len(series)):
            m = GB(**gb_params)
            m.fit(X[:i], y_log[:i])
            p = float(m.predict(X[i].reshape(1,-1))[0])
            preds_log.append(p)
            actual_log.append(float(y_log[i]))
            test_dates.append(dates[i])
            residuals_log.append(y_log[i]-p)

        if preds_log:
            seg_preds = np.expm1(np.array(preds_log))
            seg_actual = np.expm1(np.array(actual_log))
            seg_rmse = math.sqrt(mean_squared_error(seg_actual, seg_preds))
            seg_mean = float(seg_actual.mean())
            seg_nrmse = seg_rmse/seg_mean if seg_mean>0 else None
            resid_std_log = np.std(residuals_log, ddof=1) if len(residuals_log)>1 else 0.0
        else:
            seg_rmse = None
            seg_mean = None
            seg_nrmse = None
            resid_std_log = 0.0

        final_model = GB(**gb_params)
        final_model.fit(X, y_log)

        last_date = dates[-1]
        synthetic = list(np.expm1(y_log))
        future_dates = []
        future_vals = []
        for step in range(1, forecast_periods+1):
            next_date = last_date + (pd.Timedelta(weeks=step) if use_weekly else pd.Timedelta(days=step))
            future_dates.append(next_date)
            recent_log = np.log1p(synthetic)
            feat = []
            for lag in lags:
                feat.append(recent_log[-lag] if len(recent_log)>=lag else recent_log[-1])
            rs = np.mean(synthetic[-(7 if not use_weekly else 2):])
            rl = np.mean(synthetic[-(14 if not use_weekly else 4):]) if len(synthetic)> (14 if not use_weekly else 4) else rs
            feat.extend([np.log1p(rs), np.log1p(rl)])
            feat.append((len(series)+step-1)/max(1,len(series)))
            if use_weekly:
                wn = next_date.isocalendar().week
                feat.extend([math.sin(2*math.pi*wn/52), math.cos(2*math.pi*wn/52)])
            else:
                dow_f = next_date.dayofweek
                month_f = next_date.month
                feat.extend([
                    math.sin(2*math.pi*dow_f/7),
                    math.cos(2*math.pi*dow_f/7),
                    math.sin(2*math.pi*month_f/12),
                    math.cos(2*math.pi*month_f/12)
                ])
            pred_log_future = float(final_model.predict(np.array(feat).reshape(1,-1))[0])
            pred_future = float(np.expm1(pred_log_future))
            future_vals.append(pred_future)
            synthetic.append(pred_future)

        total_future_sum += np.array(future_vals)

        # Collect global test metrics contribution
        if preds_log:
            global_actual.extend(seg_actual)
            global_pred.extend(seg_preds)

        segment_traces.append(dict(
            name=f"{seg} ({freq_label})",
            dates_hist=[d.strftime("%Y-%m-%d") for d in dates],
            values_hist=[float(v) for v in np.expm1(y_log)],
            dates_future=[d.strftime("%Y-%m-%d") for d in future_dates],
            values_future=[float(v) for v in future_vals],
            rmse=seg_rmse,
            nrmse=seg_nrmse
        ))

        # Safely format metric strings (seg_nrmse may be None)
        if seg_rmse is not None:
            seg_rmse_str = f"{seg_rmse:.2f}"
            seg_nrmse_str = f"{seg_nrmse:.3f}" if seg_nrmse is not None else "N/A"
            metrics_rows.append(f"{seg}: RMSE={seg_rmse_str} NRMSE={seg_nrmse_str}")
        else:
            metrics_rows.append(f"{seg}: RMSE=N/A NRMSE=N/A")

    if global_pred and global_actual:
        agg_rmse = math.sqrt(mean_squared_error(global_actual, global_pred))
        agg_mean = float(np.mean(global_actual))
        agg_nrmse = agg_rmse/agg_mean if agg_mean>0 else None
    else:
        agg_rmse = None
        agg_nrmse = None

    fig = go.Figure()
    # Plot aggregated future forecast
    if future_dates:
        fig.add_trace(go.Scatter(
            x=[d.strftime("%Y-%m-%d") for d in future_dates],
            y=[float(v) for v in total_future_sum],
            mode="lines+markers",
            name="Total Forecast",
            line=dict(color="blue", dash="dash")
        ))

    # Add each segment future (faded)
    for t in segment_traces:
        fig.add_trace(go.Scatter(
            x=t["dates_future"],
            y=t["values_future"],
            mode="lines",
            name=t["name"] + " forecast",
            line=dict(width=1),
            opacity=0.5
        ))

    fig.update_layout(
        title="Hierarchical Sales Forecast (PRODUCTLINE)",
        xaxis_title="Date",
        yaxis_title="Sales",
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        annotations=[dict(
            xref="paper", yref="paper", x=1.02, y=0.98,
            xanchor="left", yanchor="top",
            text="<br>".join(
                ["Model: "+model_label,
                 f"Segments: {len(segment_traces)}",
                 f"Aggregate RMSE: {agg_rmse:.2f}" if agg_rmse else "Aggregate RMSE: N/A",
                 f"Aggregate NRMSE: {agg_nrmse:.3f}" if agg_nrmse else "Aggregate NRMSE: N/A"] + metrics_rows[:10]
            ),
            showarrow=False,
            align="left",
            bordercolor="rgba(0,0,0,0.15)",
            borderwidth=1,
            bgcolor="rgba(255,255,255,0.95)",
            font=dict(size=11)
        )],
        margin=dict(r=250)
    )

    return json.loads(json.dumps(fig, cls=PlotlyJSONEncoder))
"""
print(quick_sales_data_audit(extracted_data))
