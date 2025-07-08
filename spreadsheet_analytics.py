import pandas as pd
import matplotlib.pyplot as plt

# Extracting data from a CSV file
extracted_data = pd.read_csv("./spreadsheet_datasets/sales_data_sample.csv", encoding = "cp1252")

extracted_data["ORDERDATE"] = pd.to_datetime(extracted_data["ORDERDATE"], format = "%m/%d/%Y %H:%M", errors = "coerce")

extracted_data["Year-Month"] = extracted_data["ORDERDATE"].dt.to_period("M")

# Grouping data by Year-Month and calculating total sales
monthly_sales = extracted_data.groupby("Year-Month")["SALES"].sum().reset_index()

# Plotting the monthly sales data
plt.figure(figsize=(12, 6))
plt.plot(monthly_sales["Year-Month"].astype(str), monthly_sales["SALES"], marker='o')
plt.title("Monthly Sales Over Time")
plt.xlabel("Year-Month")
plt.ylabel("Total Sales")
plt.xticks(rotation=45 )
plt.grid()

plt.show()

