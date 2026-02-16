import tkinter as tk

def show_animation():
    # Create a small sub-window (Toplevel)
    anim_window = tk.Toplevel(root)
    anim_window.title("Animation")
    
    # Position it in the top right corner of the main window
    root.update_idletasks()
    x = root.winfo_x() + root.winfo_width() - 220
    y = root.winfo_y()
    anim_window.geometry("200x250+{}+{}".format(x, y))
    
    # Create a canvas for drawing
    canvas = tk.Canvas(anim_window, width=200, height=250, bg='white')
    canvas.pack()
    
    # Animation state
    frame = [0]
    max_frames = 30
    
    def animate():
        if not anim_window.winfo_exists():
            return
            
        canvas.delete("all")
        
        # Calculate animation offset
        offset = frame[0] % 20
        arm_swing = offset - 10
        leg_swing = offset - 10
        
        # Draw stick figure
        # Head
        canvas.create_oval(80, 30, 120, 70, outline='black', width=2)
        
        # Body
        canvas.create_line(100, 70, 100, 140, width=2)
        
        # Arms - animate with swing
        canvas.create_line(100, 90, 70 + arm_swing, 110, width=2)
        canvas.create_line(100, 90, 130 - arm_swing, 110, width=2)
        
        # Legs - animate with swing
        canvas.create_line(100, 140, 80 - leg_swing, 200, width=2)
        canvas.create_line(100, 140, 120 + leg_swing, 200, width=2)
        
        frame[0] += 1
        
        if frame[0] < max_frames:
            canvas.after(100, animate)
    
    animate()

# Create the main window
root = tk.Tk()
root.title("Hello World")
root.geometry("300x200")

# Create a label with "Hello World!"
label = tk.Label(root, text="Hello World!", font=("Arial", 24))
label.pack(expand=True, pady=10)

# Create a button titled "PRESS" with the animation function
button = tk.Button(root, text="PRESS", font=("Arial", 14), command=show_animation)
button.pack(pady=10)

# Run the application
root.mainloop()
