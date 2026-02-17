import tkinter as tk
import math

class PendulumAnimation:
    def __init__(self, root):
        self.root = root
        self.root.title("Simple Pendulum Animation")
        
        # Canvas setup
        self.canvas = tk.Canvas(root, width=600, height=500, bg='white')
        self.canvas.pack()
        
        # Pendulum parameters
        self.length = 200  # length in pixels
        self.angle = math.pi / 3  # initial angle (60 degrees)
        self.angular_velocity = 0.0
        self.angular_acceleration = 0.0
        self.gravity = 0.5
        self.damping = 0.999  # slight damping
        
        # Pivot point
        self.pivot_x = 300
        self.pivot_y = 100
        
        # Draw pivot
        self.canvas.create_oval(self.pivot_x - 5, self.pivot_y - 5,
                                self.pivot_x + 5, self.pivot_y + 5,
                                fill='black')
        
        # Create pendulum components
        self.rod = self.canvas.create_line(0, 0, 0, 0, width=3, fill='blue')
        self.bob = self.canvas.create_oval(0, 0, 0, 0, fill='red', outline='darkred', width=2)
        
        # Info text
        self.info_text = self.canvas.create_text(300, 30, text="", font=('Arial', 12))
        
        # Start animation
        self.animate()
    
    def animate(self):
        # Calculate angular acceleration
        self.angular_acceleration = (-self.gravity / self.length) * math.sin(self.angle) * 100
        
        # Update angular velocity and angle
        self.angular_velocity += self.angular_acceleration
        self.angular_velocity *= self.damping  # apply damping
        self.angle += self.angular_velocity
        
        # Calculate bob position
        bob_x = self.pivot_x + self.length * math.sin(self.angle)
        bob_y = self.pivot_y + self.length * math.cos(self.angle)
        
        # Update rod
        self.canvas.coords(self.rod, self.pivot_x, self.pivot_y, bob_x, bob_y)
        
        # Update bob (circle with radius 20)
        bob_radius = 20
        self.canvas.coords(self.bob,
                          bob_x - bob_radius, bob_y - bob_radius,
                          bob_x + bob_radius, bob_y + bob_radius)
        
        # Update info text
        angle_degrees = math.degrees(self.angle)
        info = f"Angle: {angle_degrees:.1f}°  |  Angular Velocity: {self.angular_velocity:.3f}"
        self.canvas.itemconfig(self.info_text, text=info)
        
        # Schedule next frame (approximately 60 FPS)
        self.root.after(16, self.animate)

# Create and run the application
root = tk.Tk()
app = PendulumAnimation(root)
root.mainloop()
