import pandas as pd
import matplotlib.pyplot as plt
from prophet import Prophet
from prophet.plot import plot_plotly

# Extracting data from a CSV file
extracted_data = pd.read_csv("./spreadsheet_datasets/sales_data_sample.csv", encoding = "cp1252")

extracted_data["ORDERDATE"] = pd.to_datetime(extracted_data["ORDERDATE"], errors = "coerce")

extracted_data["Year"] = extracted_data["ORDERDATE"].dt.to_period("M")

# Grouping data by Year-Month and calculating total sales
monthly_sales = extracted_data.groupby("Year")["SALES"].sum().reset_index()

#monthly_sales = monthly_sales[["SALES"] > ]

monthly_sales.columns = ["ds", "y"]

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

#predict_fig = model.plot(forecast_pd, xlabel='date', ylabel='sales')
fig = plot_plotly(model, forecast_pd)
fig.to_dict()

fig.show()
#display(predict_fig)

"""
# Plotting the monthly sales data
plt.figure(figsize=(12, 6))
plt.plot(monthly_sales["Year"].astype(str), monthly_sales["SALES"]/1000, marker='o')
plt.title("Monthly Sales Over Time")
plt.xlabel("Year")
plt.ylabel("Total Sales")
plt.xticks(rotation=90, fontsize = 5)
plt.grid()

plt.show()
"""


async def generate_sales_forecast():

    extracted_data["ORDERDATE"] = pd.to_datetime(extracted_data["ORDERDATE"], errors = "coerce")

    extracted_data["Year"] = extracted_data["ORDERDATE"].dt.to_period("D")

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
        periods = 90,
        freq ='D',
        include_history = True
    )

    # predict over the dataset
    forecast_pd = model.predict(future_pd)

    #fig = model.plot(forecast_pd, xlabel='date', ylabel='sales')
    
    fig = plot_plotly(model, forecast_pd)

    return fig.to_html(full_html = False)

async def generate_sales_forecast():

    import asyncio

    # Ensure datetime and drop bad rows
    extracted_data["ORDERDATE"] = pd.to_datetime(extracted_data["ORDERDATE"], errors="coerce")
    df_clean = extracted_data.dropna(subset=["ORDERDATE", "SALES"]).copy()

    # Aggregate by week
    df_clean["Week"] = df_clean["ORDERDATE"].dt.to_period("W")
    weekly_sales = df_clean.groupby("Week")["SALES"].sum().reset_index()

    # Prepare Prophet columns
    weekly_sales = weekly_sales.rename(columns={"Week": "ds", "SALES": "y"})
    weekly_sales["ds"] = weekly_sales["ds"].dt.to_timestamp()   # convert Period -> Timestamp
    weekly_sales = weekly_sales.sort_values("ds").reset_index(drop=True)
    weekly_sales["y"] = weekly_sales["y"].astype(float)

    model = Prophet(
        interval_width=0.95,
        daily_seasonality=False,
        weekly_seasonality=False,
        yearly_seasonality=True
    )

    # Fit in executor to avoid blocking the event loop
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, lambda: model.fit(weekly_sales[['ds', 'y']]))

    # Build future and predict in executor
    future_pd = model.make_future_dataframe(periods=30, freq='W', include_history=True)
    forecast_pd = await loop.run_in_executor(None, lambda: model.predict(future_pd))

    # Compute in-sample RMSE (compare historical y to fitted yhat)
    hist = forecast_pd[['ds', 'yhat']].merge(weekly_sales[['ds', 'y']], on='ds', how='inner')
    if not hist.empty:
        rmse = float(np.sqrt(mean_squared_error(hist['y'], hist['yhat'])))
    else:
        rmse = float('nan')

    # Plot and include RMSE in the title
    fig = plot_plotly(model, forecast_pd)
    fig.update_layout(title=f"Weekly Sales Forecast — RMSE: {rmse:.2f}")

    return fig.to_html(full_html=False)

