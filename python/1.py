import numpy as np
import statistics
data=[50,20,30,40,10,20]
print(f"mean {statistics.mean(data)}")
print(f"mode : {statistics.mode(data)}")
print(f"median: {statistics.median(data)}")
print(f"variance : { statistics.variance(data)}")
print(f"standard deviation : { statistics.stdev(data)}")print(f"quartile range: { np.percentile(data,25)-np.percentile(data,50)}")
