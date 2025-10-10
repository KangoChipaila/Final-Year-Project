@echo starting Hadoop

runas /user:"Kango Chipaila" "start cmd /k %HADOOP_HOME%\bin\hdfs.cmd namenode"
runas /user:"Kango Chipaila" "start cmd /k %HADOOP_HOME%\bin\hdfs.cmd datanode"
runas /user:"Kango Chipaila" "start cmd /k %HADOOP_HOME%\bin\yarn.cmd resourcemanager"
runas /user:"Kango Chipaila" "start cmd /k %HADOOP_HOME%\bin\yarn.cmd nodemanager"