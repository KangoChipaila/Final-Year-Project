from pyspark.sql import SparkSession

spark = SparkSession.builder.appName("PySparkTest").getOrCreate()

data = [("Alice", 1), ("Bob", 2), ("Charlie", 3)]
columns = ["Name", "ID"]
df = spark.createDataFrame(data, columns)

df.show()

spark.stop()