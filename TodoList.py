"""Todo List — Tkinter GUI for managing tasks with priorities, due dates, and categories."""

import json
import os
import tkinter as tk
from datetime import datetime
from tkinter import messagebox, ttk

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "todo_state.json")
DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "todos.json")

PRIORITIES = ["High", "Medium", "Low"]
DEFAULT_CATEGORIES = ["General", "Work", "Personal", "Errands"]


class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Todo List")
        self.root.geometry("900x550")
        self.root.minsize(700, 400)

        self.todos = []  # list of dicts: {text, priority, category, due, done, created}
        self.categories = list(DEFAULT_CATEGORIES)
        self._filter_done = "all"  # "all", "active", "completed"
        self._filter_priority = "All"
        self._filter_category = "All"
        self._sort_col = None
        self._sort_reverse = False

        self._build_ui()
        self._load_data()
        self._load_state()
        self._refresh_tree()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.mainloop()

    # ── UI ────────────────────────────────────────────────────────────

    def _build_ui(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Todo.Treeview", background="#D6EBFF", fieldbackground="#D6EBFF",
                         rowheight=24)
        style.configure("Todo.Treeview.Heading", background="#FFFFB3", foreground="black")
        style.map("Todo.Treeview.Heading", background=[("active", "#FFFF88")])
        style.configure("Done.Todo.Treeview", foreground="#888888")

        # ── Add task bar ──
        add_frame = tk.LabelFrame(self.root, text="New Task", padx=6, pady=4)
        add_frame.pack(side=tk.TOP, fill=tk.X, padx=6, pady=(6, 2))

        tk.Label(add_frame, text="Task:").pack(side=tk.LEFT)
        self._entry_text = tk.Entry(add_frame, width=35)
        self._entry_text.pack(side=tk.LEFT, padx=4)
        self._entry_text.bind("<Return>", lambda e: self._add_todo())

        tk.Label(add_frame, text="Priority:").pack(side=tk.LEFT, padx=(8, 0))
        self._entry_priority = ttk.Combobox(add_frame, values=PRIORITIES,
                                             state="readonly", width=8)
        self._entry_priority.set("Medium")
        self._entry_priority.pack(side=tk.LEFT, padx=2)

        tk.Label(add_frame, text="Category:").pack(side=tk.LEFT, padx=(8, 0))
        self._entry_category = ttk.Combobox(add_frame, values=self.categories, width=10)
        self._entry_category.set("General")
        self._entry_category.pack(side=tk.LEFT, padx=2)

        tk.Label(add_frame, text="Due:").pack(side=tk.LEFT, padx=(8, 0))
        self._entry_due = tk.Entry(add_frame, width=10)
        self._entry_due.pack(side=tk.LEFT, padx=2)
        self._entry_due.insert(0, "")
        self._due_hint = tk.Label(add_frame, text="(DD/MM/YYYY)", fg="grey")
        self._due_hint.pack(side=tk.LEFT)

        tk.Button(add_frame, text="Add", command=self._add_todo).pack(side=tk.LEFT, padx=(10, 0))

        # ── Filter / action bar ──
        filter_frame = tk.Frame(self.root)
        filter_frame.pack(side=tk.TOP, fill=tk.X, padx=6, pady=2)

        tk.Label(filter_frame, text="Show:").pack(side=tk.LEFT)
        self._filter_done_var = tk.StringVar(value="all")
        for text, val in [("All", "all"), ("Active", "active"), ("Done", "completed")]:
            tk.Radiobutton(filter_frame, text=text, variable=self._filter_done_var,
                           value=val, command=self._on_filter_changed).pack(side=tk.LEFT)

        ttk.Separator(filter_frame, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y,
                                                               padx=8, pady=2)

        tk.Label(filter_frame, text="Priority:").pack(side=tk.LEFT)
        self._filter_pri_var = tk.StringVar(value="All")
        self._filter_pri_combo = ttk.Combobox(filter_frame, textvariable=self._filter_pri_var,
                                               values=["All", *PRIORITIES], state="readonly",
                                               width=8)
        self._filter_pri_combo.pack(side=tk.LEFT, padx=2)
        self._filter_pri_combo.bind("<<ComboboxSelected>>", lambda e: self._on_filter_changed())

        tk.Label(filter_frame, text="Category:").pack(side=tk.LEFT, padx=(8, 0))
        self._filter_cat_var = tk.StringVar(value="All")
        self._filter_cat_combo = ttk.Combobox(filter_frame, textvariable=self._filter_cat_var,
                                               values=["All", *self.categories], state="readonly",
                                               width=10)
        self._filter_cat_combo.pack(side=tk.LEFT, padx=2)
        self._filter_cat_combo.bind("<<ComboboxSelected>>", lambda e: self._on_filter_changed())

        ttk.Separator(filter_frame, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y,
                                                               padx=8, pady=2)

        tk.Button(filter_frame, text="Toggle Done", command=self._toggle_done).pack(
            side=tk.LEFT, padx=2)
        tk.Button(filter_frame, text="Edit", command=self._edit_todo).pack(side=tk.LEFT, padx=2)
        tk.Button(filter_frame, text="Delete", command=self._delete_todo).pack(side=tk.LEFT, padx=2)
        tk.Button(filter_frame, text="Move Up", command=self._move_up).pack(side=tk.LEFT, padx=2)
        tk.Button(filter_frame, text="Move Down", command=self._move_down).pack(side=tk.LEFT, padx=2)

        self._status_var = tk.StringVar()
        tk.Label(filter_frame, textvariable=self._status_var, anchor=tk.E).pack(
            side=tk.RIGHT, padx=4)

        # ── Treeview ──
        columns = ("done", "text", "priority", "category", "due", "created")
        container = tk.Frame(self.root)
        container.pack(fill=tk.BOTH, expand=True, padx=6, pady=(2, 6))

        self.tree = ttk.Treeview(container, columns=columns, show="headings",
                                  selectmode="browse", style="Todo.Treeview")

        headings = {"done": ("Done", 50), "text": ("Task", 300), "priority": ("Priority", 70),
                     "category": ("Category", 90), "due": ("Due Date", 90),
                     "created": ("Created", 90)}
        for col, (label, width) in headings.items():
            self.tree.heading(col, text=label, anchor=tk.W,
                              command=lambda c=col: self._on_heading_click(c))
            self.tree.column(col, width=width, minwidth=40, anchor=tk.W)

        vsb = ttk.Scrollbar(container, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        container.grid_rowconfigure(0, weight=1)
        container.grid_columnconfigure(0, weight=1)

        self.tree.bind("<Double-1>", lambda e: self._toggle_done())
        self.tree.tag_configure("done", foreground="#888888")
        self.tree.tag_configure("high", foreground="#CC0000")
        self.tree.tag_configure("overdue", background="#FFE0E0")

    # ── Data operations ───────────────────────────────────────────────

    def _add_todo(self):
        text = self._entry_text.get().strip()
        if not text:
            return

        category = self._entry_category.get().strip() or "General"
        if category not in self.categories:
            self.categories.append(category)
            self._update_category_combos()

        due = self._entry_due.get().strip()
        if due and not self._parse_date(due):
            messagebox.showwarning("Invalid date", "Use DD/MM/YYYY format for due date.")
            return

        todo = {
            "text": text,
            "priority": self._entry_priority.get(),
            "category": category,
            "due": due,
            "done": False,
            "created": datetime.now().strftime("%d/%m/%Y"),
        }
        self.todos.append(todo)
        self._entry_text.delete(0, tk.END)
        self._entry_due.delete(0, tk.END)
        self._refresh_tree()
        self._save_data()
        # Select the newly added item
        children = self.tree.get_children()
        if children:
            self.tree.selection_set(children[-1])
            self.tree.see(children[-1])

    def _toggle_done(self):
        idx = self._selected_real_index()
        if idx is None:
            return
        self.todos[idx]["done"] = not self.todos[idx]["done"]
        self._refresh_tree()
        self._save_data()

    def _delete_todo(self):
        idx = self._selected_real_index(require=True)
        if idx is None:
            return
        self.todos.pop(idx)
        self._refresh_tree()
        self._save_data()

    def _edit_todo(self):
        idx = self._selected_real_index(require=True)
        if idx is None:
            return
        todo = self.todos[idx]

        dlg = tk.Toplevel(self.root)
        dlg.title("Edit Task")
        dlg.geometry("420x220")
        dlg.transient(self.root)
        dlg.grab_set()

        tk.Label(dlg, text="Task:").grid(row=0, column=0, sticky="w", padx=8, pady=4)
        e_text = tk.Entry(dlg, width=40)
        e_text.grid(row=0, column=1, padx=8, pady=4)
        e_text.insert(0, todo["text"])

        tk.Label(dlg, text="Priority:").grid(row=1, column=0, sticky="w", padx=8, pady=4)
        e_pri = ttk.Combobox(dlg, values=PRIORITIES, state="readonly", width=10)
        e_pri.grid(row=1, column=1, sticky="w", padx=8, pady=4)
        e_pri.set(todo["priority"])

        tk.Label(dlg, text="Category:").grid(row=2, column=0, sticky="w", padx=8, pady=4)
        e_cat = ttk.Combobox(dlg, values=self.categories, width=15)
        e_cat.grid(row=2, column=1, sticky="w", padx=8, pady=4)
        e_cat.set(todo["category"])

        tk.Label(dlg, text="Due (DD/MM/YYYY):").grid(row=3, column=0, sticky="w", padx=8, pady=4)
        e_due = tk.Entry(dlg, width=12)
        e_due.grid(row=3, column=1, sticky="w", padx=8, pady=4)
        e_due.insert(0, todo.get("due", ""))

        def save():
            new_text = e_text.get().strip()
            if not new_text:
                messagebox.showwarning("Empty", "Task text cannot be empty.", parent=dlg)
                return
            new_due = e_due.get().strip()
            if new_due and not self._parse_date(new_due):
                messagebox.showwarning("Invalid date", "Use DD/MM/YYYY format.", parent=dlg)
                return
            new_cat = e_cat.get().strip() or "General"
            if new_cat not in self.categories:
                self.categories.append(new_cat)
                self._update_category_combos()
            todo["text"] = new_text
            todo["priority"] = e_pri.get()
            todo["category"] = new_cat
            todo["due"] = new_due
            dlg.destroy()
            self._refresh_tree()
            self._save_data()

        tk.Button(dlg, text="Save", command=save, width=10).grid(
            row=4, column=0, columnspan=2, pady=12)

    def _move_up(self):
        idx = self._selected_real_index()
        if idx is None or idx == 0:
            return
        self.todos[idx], self.todos[idx - 1] = self.todos[idx - 1], self.todos[idx]
        self._refresh_tree()
        self._save_data()
        self._select_real_index(idx - 1)

    def _move_down(self):
        idx = self._selected_real_index()
        if idx is None or idx >= len(self.todos) - 1:
            return
        self.todos[idx], self.todos[idx + 1] = self.todos[idx + 1], self.todos[idx]
        self._refresh_tree()
        self._save_data()
        self._select_real_index(idx + 1)

    # ── Tree display ──────────────────────────────────────────────────

    def _refresh_tree(self):
        self.tree.delete(*self.tree.get_children())
        self._visible_map = []  # maps tree position -> index in self.todos

        filtered = []
        for i, todo in enumerate(self.todos):
            # Status filter
            if self._filter_done == "active" and todo["done"]:
                continue
            if self._filter_done == "completed" and not todo["done"]:
                continue
            # Priority filter
            if self._filter_priority != "All" and todo["priority"] != self._filter_priority:
                continue
            # Category filter
            if self._filter_category != "All" and todo["category"] != self._filter_category:
                continue
            filtered.append((i, todo))

        # Sort if a heading was clicked
        if self._sort_col:
            filtered.sort(key=lambda pair: self._sort_key(pair[1], self._sort_col),
                          reverse=self._sort_reverse)

        today = datetime.now().date()
        for real_idx, todo in filtered:
            check = "\u2713" if todo["done"] else ""
            values = (check, todo["text"], todo["priority"], todo["category"],
                      todo.get("due", ""), todo.get("created", ""))
            tags = []
            if todo["done"]:
                tags.append("done")
            elif todo["priority"] == "High":
                tags.append("high")
            # Overdue check
            if not todo["done"] and todo.get("due"):
                d = self._parse_date(todo["due"])
                if d and d.date() < today:
                    tags.append("overdue")
            self.tree.insert("", tk.END, values=values, tags=tuple(tags))
            self._visible_map.append(real_idx)

        # Update status
        total = len(self.todos)
        done = sum(1 for t in self.todos if t["done"])
        active = total - done
        self._status_var.set(f"{active} active, {done} done, {total} total")

    def _sort_key(self, todo, col):
        if col == "done":
            return (0 if todo["done"] else 1,)
        if col == "priority":
            order = {"High": 0, "Medium": 1, "Low": 2}
            return (order.get(todo["priority"], 9),)
        if col in ("due", "created"):
            d = self._parse_date(todo.get(col, ""))
            return (d or datetime.max,)
        return (todo.get(col, "").lower(),)

    def _on_heading_click(self, col):
        if self._sort_col == col:
            self._sort_reverse = not self._sort_reverse
        else:
            self._sort_col = col
            self._sort_reverse = False
        self._refresh_tree()

    # ── Selection helpers ─────────────────────────────────────────────

    def _selected_real_index(self, require=False):
        """Real index in self.todos for the selected tree item; with
        require=True, pop a "select a task" info box when there is none."""
        idx = None
        sel = self.tree.selection()
        if sel:
            tree_idx = self.tree.index(sel[0])
            if tree_idx < len(self._visible_map):
                idx = self._visible_map[tree_idx]
        if idx is None and require:
            messagebox.showinfo("No selection", "Select a task first.")
        return idx

    def _select_real_index(self, real_idx):
        if real_idx in self._visible_map:
            tree_idx = self._visible_map.index(real_idx)
            children = self.tree.get_children()
            if 0 <= tree_idx < len(children):
                self.tree.selection_set(children[tree_idx])
                self.tree.see(children[tree_idx])

    # ── Filter ────────────────────────────────────────────────────────

    def _on_filter_changed(self):
        self._filter_done = self._filter_done_var.get()
        self._filter_priority = self._filter_pri_var.get()
        self._filter_category = self._filter_cat_var.get()
        self._refresh_tree()

    def _update_category_combos(self):
        self._entry_category["values"] = self.categories
        self._filter_cat_combo["values"] = ["All", *self.categories]

    # ── Date parsing ──────────────────────────────────────────────────

    @staticmethod
    def _parse_date(s):
        for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%m/%d/%Y"):
            try:
                return datetime.strptime(s.strip(), fmt)
            except (ValueError, TypeError):
                continue
        return None

    # ── Persistence ───────────────────────────────────────────────────

    def _load_data(self):
        try:
            with open(DATA_FILE, encoding="utf-8") as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return
        self.todos = data.get("todos", [])
        saved_cats = data.get("categories", [])
        if saved_cats:
            self.categories = saved_cats
            self._update_category_combos()

    def _save_data(self):
        data = {"todos": self.todos, "categories": self.categories}
        try:
            with open(DATA_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass

    def _load_state(self):
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                state = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return
        geo = state.get("geometry")
        if geo:
            self.root.geometry(geo)
        self._filter_done = state.get("filter_done", "all")
        self._filter_done_var.set(self._filter_done)
        self._filter_priority = state.get("filter_priority", "All")
        self._filter_pri_var.set(self._filter_priority)
        self._filter_category = state.get("filter_category", "All")
        self._filter_cat_var.set(self._filter_category)
        self._sort_col = state.get("sort_col")
        self._sort_reverse = state.get("sort_reverse", False)

    def _save_state(self):
        state = {
            "geometry": self.root.geometry(),
            "filter_done": self._filter_done,
            "filter_priority": self._filter_priority,
            "filter_category": self._filter_category,
            "sort_col": self._sort_col,
            "sort_reverse": self._sort_reverse,
        }
        try:
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)
        except Exception:
            pass

    # ── Close ─────────────────────────────────────────────────────────

    def _on_close(self):
        self._save_data()
        self._save_state()
        self.root.destroy()


if __name__ == "__main__":
    App()
