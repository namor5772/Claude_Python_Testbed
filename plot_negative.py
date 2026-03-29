import numpy as np
import matplotlib.pyplot as plt

# Define the range for x
x = np.linspace(-100, -0.01, 10000)

# Calculate y = x^(1/x) for complex numbers
y = x.astype(np.complex128)**(1/x.astype(np.complex128))

# Get the real part of y
y_real = y.real

# Create the plot
plt.figure(figsize=(10, 6))
plt.plot(x, y_real)
plt.title("Plot of the Real Part of y = x^(1/x) for x < 0")
plt.xlabel("x")
plt.ylabel("Re(y)")
plt.grid(True)

# Save the plot to a file
plt.savefig("plot_negative.png")
