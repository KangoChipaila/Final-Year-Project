import json
import math
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.utils import PlotlyJSONEncoder
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error
import glob
import matplotlib.pyplot as plt
from prophet import Prophet
from prophet.plot import plot_plotly

sales_data = pd.read_csv("./data/train.csv", encoding="cp1252")
#sales_test_data = pd.read_csv("./data/test.csv", encoding="cp1252")

sales_data = sales_data[["date","sales"]]
sales_data.dropna()
sales_data["date"] = pd.to_datetime(sales_data["date"], errors="coerce")

sales_data.columns=["ds", "y"]

sales_data = sales_data.groupby("ds")["y"].sum().reset_index()

#sales_data["ds"] = sales_data["ds"].dt.to_period("W")

sales_data["ds"] = sales_data["ds"].to_list()
sales_data["y"] = sales_data["y"].to_list()


train_data = sales_data.iloc[:1000]
test_data = sales_data.iloc[1000:]

#print(len(sales_data))

def generate_sales_forecast():

    model = Prophet(
        changepoint_prior_scale=0.02,
        seasonality_prior_scale=3.0,
        interval_width = 0.95,
        growth = 'linear',
        daily_seasonality = True,
        weekly_seasonality = True,
        yearly_seasonality = True,
        seasonality_mode = 'additive'
    )

    model.fit(train_data)

    future_pd = model.make_future_dataframe(
        periods = len(test_data),
        include_history = True
    )

    # predict over the dataset
    forecast_pd = model.predict(future_pd)
    
    fig = plot_plotly(model, forecast_pd)

    print(len(test_data))
    
    forecast_test = forecast_pd.iloc[-len(test_data["y"]):]

    rmse = np.sqrt(mean_squared_error(test_data['y'], forecast_test['yhat']))
    mean = test_data['y'].mean()
    nrmse = (rmse/mean)*100

    """mae = mean_absolute_error(test_data['y'], forecast_test['yhat'])"""
    #print(df_cv.head())

    return fig.to_html(full_html = False), mean, rmse, nrmse