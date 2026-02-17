import tkinter as tk
import math

class DoublePendulumAnimation:
    def __init__(self, root):
        self.root = root
        self.root.title("Double Pendulum Animation")
        
        # Canvas setup
        self.canvas = tk.Canvas(root, width=800, height=700, bg='white')
        self.canvas.pack()
        
        # Double pendulum parameters
        self.length1 = 150  # length of first rod in pixels
        self.length2 = 150  # length of second rod in pixels
        self.mass1 = 10
        self.mass2 = 10
        self.angle1 = math.pi / 2  # initial angle of first pendulum (90 degrees)
        self.angle2 = math.pi / 2  # initial angle of second pendulum (90 degrees)
        self.angular_velocity1 = 0.0
        self.angular_velocity2 = 0.0
        self.gravity = 1.0
        
        # Pivot point
        self.pivot_x = 400
        self.pivot_y = 150
        
        # Trail for chaotic motion
        self.trail = []
        self.max_trail_length = 500
        
        # Draw pivot
        self.canvas.create_oval(self.pivot_x - 6, self.pivot_y - 6,
                                self.pivot_x + 6, self.pivot_y + 6,
                                fill='black')
        
        # Create pendulum components
        self.rod1 = self.canvas.create_line(0, 0, 0, 0, width=3, fill='blue')
        self.rod2 = self.canvas.create_line(0, 0, 0, 0, width=3, fill='green')
        self.bob1 = self.canvas.create_oval(0, 0, 0, 0, fill='red', outline='darkred', width=2)
        self.bob2 = self.canvas.create_oval(0, 0, 0, 0, fill='orange', outline='darkorange', width=2)
        
        # Info text
        self.info_text = self.canvas.create_text(400, 30, text="", font=('Arial', 11))
        self.canvas.create_text(400, 50, text="Watch the chaotic motion!", font=('Arial', 10), fill='gray')
        
        # Start animation
        self.animate()
    
    def animate(self):
        # Calculate positions
        x1 = self.pivot_x + self.length1 * math.sin(self.angle1)
        y1 = self.pivot_y + self.length1 * math.cos(self.angle1)
        
        x2 = x1 + self.length2 * math.sin(self.angle2)
        y2 = y1 + self.length2 * math.cos(self.angle2)
        
        # Physics calculations (using equations of motion for double pendulum)
        m1 = self.mass1
        m2 = self.mass2
        L1 = self.length1
        L2 = self.length2
        g = self.gravity
        a1 = self.angle1
        a2 = self.angle2
        v1 = self.angular_velocity1
        v2 = self.angular_velocity2
        
        # Angular accelerations
        num1 = -g * (2 * m1 + m2) * math.sin(a1)
        num2 = -m2 * g * math.sin(a1 - 2 * a2)
        num3 = -2 * math.sin(a1 - a2) * m2
        num4 = v2 * v2 * L2 + v1 * v1 * L1 * math.cos(a1 - a2)
        den = L1 * (2 * m1 + m2 - m2 * math.cos(2 * a1 - 2 * a2))
        angular_acceleration1 = (num1 + num2 + num3 * num4) / den
        
        num1 = 2 * math.sin(a1 - a2)
        num2 = v1 * v1 * L1 * (m1 + m2)
        num3 = g * (m1 + m2) * math.cos(a1)
        num4 = v2 * v2 * L2 * m2 * math.cos(a1 - a2)
        den = L2 * (2 * m1 + m2 - m2 * math.cos(2 * a1 - 2 * a2))
        angular_acceleration2 = (num1 * (num2 + num3 + num4)) / den
        
        # Update velocities and angles
        self.angular_velocity1 += angular_acceleration1 * 0.1
        self.angular_velocity2 += angular_acceleration2 * 0.1
        self.angle1 += self.angular_velocity1 * 0.1
        self.angle2 += self.angular_velocity2 * 0.1
        
        # Update rods
        self.canvas.coords(self.rod1, self.pivot_x, self.pivot_y, x1, y1)
        self.canvas.coords(self.rod2, x1, y1, x2, y2)
        
        # Update bobs
        bob_radius1 = 15
        bob_radius2 = 15
        self.canvas.coords(self.bob1,
                          x1 - bob_radius1, y1 - bob_radius1,
                          x1 + bob_radius1, y1 + bob_radius1)
        self.canvas.coords(self.bob2,
                          x2 - bob_radius2, y2 - bob_radius2,
                          x2 + bob_radius2, y2 + bob_radius2)
        
        # Add trail point for second bob
        self.trail.append((x2, y2))
        if len(self.trail) > self.max_trail_length:
            self.trail.pop(0)
        
        # Draw trail
        if len(self.trail) > 1:
            for i in range(len(self.trail) - 1):
                x1_trail, y1_trail = self.trail[i]
                x2_trail, y2_trail = self.trail[i + 1]
                # Fade effect
                alpha = i / len(self.trail)
                color_intensity = int(alpha * 200)
                color = f'#{color_intensity:02x}{color_intensity:02x}{255:02x}'
                self.canvas.create_line(x1_trail, y1_trail, x2_trail, y2_trail,
                                      fill=color, width=1, tags='trail')
        
        # Clean up old trail lines (keep canvas from getting too cluttered)
        trail_items = self.canvas.find_withtag('trail')
        if len(trail_items) > 500:
            for item in trail_items[:100]:
                self.canvas.delete(item)
        
        # Update info text
        info = f"Angle 1: {math.degrees(self.angle1):.1f}°  |  Angle 2: {math.degrees(self.angle2):.1f}°"
        self.canvas.itemconfig(self.info_text, text=info)
        
        # Schedule next frame
        self.root.after(16, self.animate)

# Create and run the application
root = tk.Tk()
app = DoublePendulumAnimation(root)
root.mainloop()
