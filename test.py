import glob
import pandas as pd

directory = "./spreadsheet_datasets"

files = glob.glob(directory + "/*.csv")

df = [pd.read_csv(filename, encoding = "cp1252") for filename in files]

aggregate_df = pd.concat(df, ignore_index = True)

print(aggregate_df.head())