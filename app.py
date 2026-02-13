import tkinter as tk


class App:
    def __init__(self, root):
        self.root = root
        self.root.title("Python Testbed")
        self.root.geometry("600x400")

        label = tk.Label(root, text="Hello, World!!", font=("Arial", 18))
        label.pack(expand=True)


if __name__ == "__main__":
    root = tk.Tk()
    app = App(root)
    root.mainloop()
