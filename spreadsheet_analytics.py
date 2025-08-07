import pandas as pd
import matplotlib.pyplot as plt
from prophet import Prophet

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

predict_fig = model.plot(forecast_pd, xlabel='date', ylabel='sales')

plt.show()
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