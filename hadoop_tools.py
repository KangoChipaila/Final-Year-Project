from pyspark.sql import SparkSession
from functools import reduce
import glob

spark = SparkSession.builder.appName("HadoopFunctions").getOrCreate()

spreadsheets_path = './spreadsheet_datasets'
csv_files = glob.glob(spreadsheets_path + '/*.csv')

dataframes = [spark.read.csv (csv_file, header = True, inferSchema = True, encoding = "cp1252") for csv_file in csv_files]

extracted_data = reduce(lambda df1, df2: df1.union(df2), dataframes)

extracted_data.show()