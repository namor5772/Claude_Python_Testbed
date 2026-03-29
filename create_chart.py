import matplotlib.pyplot as plt
import numpy as np

# Data
countries = ['India', 'China', 'United States', 'Indonesia', 'Pakistan']
populations = [1476625576, 1412914089, 349035494, 287886782, 259299791]

# Create the bar chart
plt.figure(figsize=(10, 6))
plt.bar(countries, populations, color=['skyblue', 'lightcoral', 'lightgreen', 'gold', 'mediumpurple'])
plt.xlabel('Countries')
plt.ylabel('Population')
plt.title('Population of the 5 Largest Countries')
plt.xticks(rotation=45, ha='right')
plt.tight_layout()

# Save the chart to a file
plt.savefig('population_chart.png')

print("Bar chart saved as population_chart.png")
