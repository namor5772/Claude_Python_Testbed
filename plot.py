import numpy as np
import matplotlib.pyplot as plt
import os

# Generate x values from a small positive number to 100 to avoid division by zero
x = np.linspace(0.01, 100, 1000)

# Calculate y values
y = x**(1/x)

# Create the plot
plt.figure(figsize=(10, 6))
plt.plot(x, y)
plt.title('Plot of y = x^(1/x)')
plt.xlabel('x')
plt.ylabel('y')
plt.grid(True)

# Get the desktop path and save the plot
desktop_path = os.path.expanduser('~/Desktop')
file_path = os.path.join(desktop_path, 'plot.png')
plt.savefig(file_path)

print(file_path)
