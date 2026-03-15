"""CSV Editor — Tkinter GUI for loading, editing, and saving CSV files."""

import csv
import json
import os
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "csv_editor_state.json")


class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("CSV Editor")
        self.root.geometry("1000x600")
        self.root.minsize(600, 400)

        self.filepath = None
        self.headers = []
        self.rows = []  # list of lists
        self.modified = False
        self._visible_indices = []  # maps tree position -> index in self.rows
        self._filter_col = None     # column index being filtered, or None
        self._filter_val = None     # value being filtered on, or None

        self._build_ui()
        self._load_state()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.mainloop()

    # ── UI ────────────────────────────────────────────────────────────

    def _build_ui(self):
        # Toolbar
        toolbar = tk.Frame(self.root)
        toolbar.pack(side=tk.TOP, fill=tk.X, padx=4, pady=4)

        tk.Button(toolbar, text="Open CSV", command=self._open_file).pack(side=tk.LEFT, padx=2)
        tk.Button(toolbar, text="Save", command=self._save_file).pack(side=tk.LEFT, padx=2)
        tk.Button(toolbar, text="Save As…", command=self._save_as).pack(side=tk.LEFT, padx=2)
        ttk.Separator(toolbar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=6)
        tk.Button(toolbar, text="Insert Row Above", command=self._insert_row_above).pack(side=tk.LEFT, padx=2)
        tk.Button(toolbar, text="Insert Row Below", command=self._insert_row_below).pack(side=tk.LEFT, padx=2)
        tk.Button(toolbar, text="Copy Row", command=self._copy_row).pack(side=tk.LEFT, padx=2)
        tk.Button(toolbar, text="Delete Row", command=self._delete_row).pack(side=tk.LEFT, padx=2)

        self._status_var = tk.StringVar(value="No file loaded")
        tk.Label(toolbar, textvariable=self._status_var, anchor=tk.E).pack(side=tk.RIGHT, padx=4)

        # Filter bar
        filter_bar = tk.Frame(self.root)
        filter_bar.pack(side=tk.TOP, fill=tk.X, padx=4, pady=(0, 4))

        tk.Label(filter_bar, text="Filter by:").pack(side=tk.LEFT, padx=2)
        self._filter_col_var = tk.StringVar()
        self._filter_col_combo = ttk.Combobox(filter_bar, textvariable=self._filter_col_var,
                                               state="readonly", width=20)
        self._filter_col_combo.pack(side=tk.LEFT, padx=2)
        self._filter_col_combo.bind("<<ComboboxSelected>>", self._on_filter_col_changed)

        tk.Label(filter_bar, text="Value:").pack(side=tk.LEFT, padx=2)
        self._filter_val_var = tk.StringVar()
        self._filter_val_combo = ttk.Combobox(filter_bar, textvariable=self._filter_val_var,
                                               state="readonly", width=30)
        self._filter_val_combo.pack(side=tk.LEFT, padx=2)
        self._filter_val_combo.bind("<<ComboboxSelected>>", self._on_filter_val_changed)

        tk.Button(filter_bar, text="Show All", command=self._clear_filter).pack(side=tk.LEFT, padx=6)

        self._filter_status_var = tk.StringVar()
        tk.Label(filter_bar, textvariable=self._filter_status_var, foreground="blue").pack(side=tk.LEFT, padx=4)

        # Treeview (spreadsheet)
        container = tk.Frame(self.root)
        container.pack(fill=tk.BOTH, expand=True, padx=4, pady=(0, 4))

        self.tree = ttk.Treeview(container, show="headings", selectmode="browse")
        vsb = ttk.Scrollbar(container, orient=tk.VERTICAL, command=self.tree.yview)
        hsb = ttk.Scrollbar(container, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        container.grid_rowconfigure(0, weight=1)
        container.grid_columnconfigure(0, weight=1)

        self.tree.bind("<Double-1>", self._on_double_click)

    # ── File operations ───────────────────────────────────────────────

    def _open_file(self):
        if self.modified:
            ans = messagebox.askyesnocancel("Unsaved changes", "Save current changes before opening a new file?")
            if ans is None:
                return
            if ans:
                self._save_file()

        path = filedialog.askopenfilename(
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")]
        )
        if not path:
            return
        self._load_csv(path)

    def _load_csv(self, path):
        try:
            with open(path, newline="", encoding="utf-8-sig") as f:
                reader = csv.reader(f)
                lines = list(reader)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to read file:\n{e}")
            return

        if not lines:
            messagebox.showwarning("Empty file", "The CSV file is empty.")
            return

        self.filepath = path
        self.headers = lines[0]
        self.rows = lines[1:]
        self.modified = False
        self._filter_col = None
        self._filter_val = None
        self._filter_col_var.set("")
        self._filter_val_var.set("")
        self._filter_val_combo["values"] = []
        self._filter_col_combo["values"] = self.headers
        self._filter_status_var.set("")
        self._refresh_tree()
        self._update_status()

    def _save_file(self):
        if not self.filepath:
            self._save_as()
            return
        self._write_csv(self.filepath)

    def _save_as(self):
        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            initialfile=os.path.basename(self.filepath) if self.filepath else "output.csv",
        )
        if not path:
            return
        self.filepath = path
        self._write_csv(path)

    def _write_csv(self, path):
        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(self.headers)
                writer.writerows(self.rows)
            self.modified = False
            self._update_status()
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save file:\n{e}")

    # ── Tree display ──────────────────────────────────────────────────

    def _refresh_tree(self):
        self.tree.delete(*self.tree.get_children())
        self._visible_indices = []

        col_ids = [f"c{i}" for i in range(len(self.headers))]
        self.tree["columns"] = col_ids

        for cid, hdr in zip(col_ids, self.headers):
            self.tree.heading(cid, text=hdr, anchor=tk.W)
            self.tree.column(cid, width=120, minwidth=60, anchor=tk.W)

        for real_idx, row in enumerate(self.rows):
            # Apply filter
            if self._filter_col is not None and self._filter_val is not None:
                cell = row[self._filter_col] if self._filter_col < len(row) else ""
                if cell != self._filter_val:
                    continue
            padded = row + [""] * (len(self.headers) - len(row))
            self.tree.insert("", tk.END, values=padded[:len(self.headers)])
            self._visible_indices.append(real_idx)

    def _update_status(self):
        name = os.path.basename(self.filepath) if self.filepath else "No file"
        mod = " *" if self.modified else ""
        self._status_var.set(f"{name}{mod}  |  {len(self.rows)} rows, {len(self.headers)} columns")
        title = f"CSV Editor — {name}{mod}" if self.filepath else "CSV Editor"
        self.root.title(title)

    # ── Inline editing ────────────────────────────────────────────────

    def _on_double_click(self, event):
        item = self.tree.identify_row(event.y)
        col = self.tree.identify_column(event.x)
        if not item or not col:
            return

        col_idx = int(col.replace("#", "")) - 1
        tree_idx = self.tree.index(item)
        row_idx = self._visible_indices[tree_idx] if tree_idx < len(self._visible_indices) else tree_idx
        current_val = self.rows[row_idx][col_idx] if col_idx < len(self.rows[row_idx]) else ""

        # Get cell bounding box
        bbox = self.tree.bbox(item, col)
        if not bbox:
            return

        x, y, w, h = bbox
        entry = tk.Entry(self.tree, font=("TkDefaultFont",))
        entry.place(x=x, y=y, width=w, height=h)
        entry.insert(0, current_val)
        entry.select_range(0, tk.END)
        entry.focus_set()

        def commit(e=None):
            new_val = entry.get()
            # pad row if needed
            while len(self.rows[row_idx]) <= col_idx:
                self.rows[row_idx].append("")
            if self.rows[row_idx][col_idx] != new_val:
                self.rows[row_idx][col_idx] = new_val
                self.tree.set(item, col, new_val)
                self.modified = True
                self._update_status()
            entry.destroy()

        def cancel(e=None):
            entry.destroy()

        entry.bind("<Return>", commit)
        entry.bind("<FocusOut>", commit)
        entry.bind("<Escape>", cancel)

    # ── Row operations ────────────────────────────────────────────────

    def _selected_index(self):
        """Returns the real index in self.rows for the selected tree item."""
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("No selection", "Select a row first.")
            return None
        tree_idx = self.tree.index(sel[0])
        return self._visible_indices[tree_idx] if tree_idx < len(self._visible_indices) else tree_idx

    def _insert_row_above(self):
        idx = self._selected_index()
        if idx is None:
            return
        empty = [""] * len(self.headers)
        self.rows.insert(idx, empty)
        self.modified = True
        self._refresh_tree()
        self._select_row(idx)
        self._update_status()

    def _insert_row_below(self):
        idx = self._selected_index()
        if idx is None:
            return
        empty = [""] * len(self.headers)
        self.rows.insert(idx + 1, empty)
        self.modified = True
        self._refresh_tree()
        self._select_row(idx + 1)
        self._update_status()

    def _copy_row(self):
        idx = self._selected_index()
        if idx is None:
            return
        copy = list(self.rows[idx])
        self.rows.insert(idx + 1, copy)
        self.modified = True
        self._refresh_tree()
        self._select_row(idx + 1)
        self._update_status()

    def _delete_row(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("No selection", "Select a row first.")
            return
        tree_idx = self.tree.index(sel[0])
        real_idx = self._visible_indices[tree_idx] if tree_idx < len(self._visible_indices) else tree_idx
        self.rows.pop(real_idx)
        self.modified = True
        self._refresh_tree()
        # Select nearest visible row
        children = self.tree.get_children()
        if children:
            pick = min(tree_idx, len(children) - 1)
            self.tree.selection_set(children[pick])
            self.tree.see(children[pick])
        self._update_status()

    def _select_row(self, real_idx):
        """Select the tree item corresponding to real row index in self.rows."""
        if real_idx in self._visible_indices:
            tree_idx = self._visible_indices.index(real_idx)
            children = self.tree.get_children()
            if 0 <= tree_idx < len(children):
                self.tree.selection_set(children[tree_idx])
                self.tree.see(children[tree_idx])

    # ── Filter ─────────────────────────────────────────────────────────

    def _on_filter_col_changed(self, event=None):
        col_name = self._filter_col_var.get()
        if col_name not in self.headers:
            return
        col_idx = self.headers.index(col_name)
        # Gather unique values in this column
        values = sorted(set(
            row[col_idx] if col_idx < len(row) else "" for row in self.rows
        ))
        self._filter_val_combo["values"] = values
        self._filter_val_var.set("")
        self._filter_col = None
        self._filter_val = None
        self._filter_status_var.set("")
        self._refresh_tree()
        self._update_status()

    def _on_filter_val_changed(self, event=None):
        col_name = self._filter_col_var.get()
        val = self._filter_val_var.get()
        if col_name not in self.headers:
            return
        self._filter_col = self.headers.index(col_name)
        self._filter_val = val
        self._refresh_tree()
        self._update_status()
        self._filter_status_var.set(f"Showing {len(self._visible_indices)} of {len(self.rows)} rows")

    def _clear_filter(self):
        self._filter_col = None
        self._filter_val = None
        self._filter_col_var.set("")
        self._filter_val_var.set("")
        self._filter_val_combo["values"] = []
        self._filter_status_var.set("")
        self._refresh_tree()
        self._update_status()

    # ── State persistence ─────────────────────────────────────────────

    def _load_state(self):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                state = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return

        # Restore geometry
        geo = state.get("geometry")
        if geo:
            self.root.geometry(geo)

        # Restore last opened CSV
        path = state.get("filepath")
        if path and os.path.isfile(path):
            self._load_csv(path)

            # Restore filter column and value (only if file loaded successfully)
            filter_col = state.get("filter_col", "")
            filter_val = state.get("filter_val", "")
            if filter_col and filter_col in self.headers:
                self._filter_col_var.set(filter_col)
                self._on_filter_col_changed()  # populates value dropdown
                if filter_val:
                    self._filter_val_var.set(filter_val)
                    self._on_filter_val_changed()  # applies the filter

    def _save_state(self):
        state = {
            "geometry": self.root.geometry(),
            "filepath": self.filepath,
            "filter_col": self._filter_col_var.get(),
            "filter_val": self._filter_val_var.get(),
        }
        try:
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)
        except Exception:
            pass

    # ── Close ─────────────────────────────────────────────────────────

    def _on_close(self):
        if self.modified:
            ans = messagebox.askyesnocancel("Unsaved changes", "Save before closing?")
            if ans is None:
                return
            if ans:
                self._save_file()
        self._save_state()
        self.root.destroy()


if __name__ == "__main__":
    App()
