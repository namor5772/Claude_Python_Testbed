"""CSV Editor — Tkinter GUI for loading, editing, and saving CSV files."""

import csv
import json
import os
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, messagebox, ttk

# Per-user state (geometry, last file, filters, sort) lives OUTSIDE the
# repo — same convention as MyAgent's ~/.config/myagent-* dirs — so the
# project directory accumulates no runtime droppings. The pre-2026-06-11
# location was csv_editor_state.json in the repo root; _load_state migrates
# an existing file there silently on first run.
STATE_DIR = os.path.join(os.path.expanduser("~"), ".config", "csveditor")
STATE_FILE = os.path.join(STATE_DIR, "state.json")
LEGACY_STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "csv_editor_state.json")

CSV_DELIMITERS = ",;\t|"


def detect_csv_dialect(path):
    """Detect (delimiter, quote_all) for a CSV file.

    csv.Sniffer picks the delimiter from the candidate set; when it can't
    (single-column files, unusual content) fall back to whichever candidate
    occurs most in the header line, defaulting to a comma.

    quote_all is a header-line heuristic: if every header field is wrapped
    in double quotes, the file uses the quote-everything style (e.g.
    SpecifyingList.csv / APICostLog-style ;-files) and saves preserve it.
    A quoted header field that itself contains the delimiter defeats the
    naive split and the file just saves as QUOTE_MINIMAL — still valid CSV.
    """
    with open(path, newline="", encoding="utf-8-sig") as f:
        sample = f.read(64 * 1024)
    header = sample.splitlines()[0] if sample.splitlines() else ""
    try:
        delimiter = csv.Sniffer().sniff(sample, delimiters=CSV_DELIMITERS).delimiter
    except csv.Error:
        counts = {d: header.count(d) for d in CSV_DELIMITERS}
        delimiter = max(counts, key=counts.get) if any(counts.values()) else ","
    fields = [p.strip() for p in header.split(delimiter)]
    quote_all = bool(header) and all(
        len(p) >= 2 and p.startswith('"') and p.endswith('"') for p in fields
    )
    return delimiter, quote_all


class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("CSV Editor")
        self.root.geometry("1000x600")
        self.root.minsize(600, 400)

        self.filepath = None
        self.headers = []
        self.rows = []  # list of lists
        self.delimiter = ","   # detected per-file on open, preserved on save
        self.quote_all = False  # detected per-file: quote every field on save
        self.modified = False
        self._visible_indices = []  # maps tree position -> index in self.rows
        # 3 independent filters: each is (col_index_or_None, value_or_None)
        self._filters = [(None, None), (None, None), (None, None)]
        # User-dragged column widths, keyed by header text so they survive
        # tree rebuilds, file switches, and app restarts (persisted in state)
        self._col_widths = {}
        self._date_sort_enabled = False
        self._date_sort_col = None  # column index of "Date" column

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

        # Filter rows (3 independent filters)
        self._filter_col_vars = []
        self._filter_col_combos = []
        self._filter_val_vars = []
        self._filter_val_combos = []

        filter_container = tk.Frame(self.root)
        filter_container.pack(side=tk.TOP, fill=tk.X, padx=4, pady=(0, 4))

        # One grid for all three rows (rather than per-row pack) so the
        # Filter and Value comboboxes line up vertically — the "Filter N:"
        # labels render at slightly different pixel widths in the
        # proportional UI font, which staggered pack-based rows.
        grid_frame = tk.Frame(filter_container)
        grid_frame.pack(side=tk.TOP, fill=tk.X)

        for i in range(3):
            tk.Label(grid_frame, text=f"Filter {i+1}:").grid(
                row=i, column=0, sticky=tk.W, padx=2, pady=1)
            col_var = tk.StringVar()
            col_combo = ttk.Combobox(grid_frame, textvariable=col_var,
                                      state="readonly", width=20)
            col_combo.grid(row=i, column=1, sticky=tk.W, padx=2, pady=1)
            col_combo.bind("<<ComboboxSelected>>",
                           lambda e, idx=i: self._on_filter_col_changed(idx))

            tk.Label(grid_frame, text="Value:").grid(
                row=i, column=2, sticky=tk.W, padx=2, pady=1)
            val_var = tk.StringVar()
            val_combo = ttk.Combobox(grid_frame, textvariable=val_var,
                                      state="readonly", width=30)
            val_combo.grid(row=i, column=3, sticky=tk.W, padx=2, pady=1)
            val_combo.bind("<<ComboboxSelected>>",
                           lambda e, idx=i: self._on_filter_val_changed(idx))

            self._filter_col_vars.append(col_var)
            self._filter_col_combos.append(col_combo)
            self._filter_val_vars.append(val_var)
            self._filter_val_combos.append(val_combo)

        # Controls row: Show All + Sort by Date + filter status
        ctrl_frame = tk.Frame(filter_container)
        ctrl_frame.pack(side=tk.TOP, fill=tk.X, pady=(2, 0))

        tk.Button(ctrl_frame, text="Show All", command=self._clear_filter).pack(side=tk.LEFT, padx=2)

        self._sort_date_btn = tk.Button(ctrl_frame, text="Sort by Date: OFF",
                                         command=self._toggle_date_sort)
        self._sort_date_btn.pack(side=tk.LEFT, padx=6)

        self._filter_status_var = tk.StringVar()
        tk.Label(ctrl_frame, textvariable=self._filter_status_var, foreground="blue").pack(side=tk.LEFT, padx=4)

        # Treeview style — use 'clam' theme so heading background is respected
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("CSV.Treeview", background="#D6EBFF", fieldbackground="#D6EBFF")
        style.configure("CSV.Treeview.Heading", background="#FFFFB3", foreground="black")
        style.map("CSV.Treeview.Heading", background=[("active", "#FFFF88")])

        # Treeview (spreadsheet)
        container = tk.Frame(self.root)
        container.pack(fill=tk.BOTH, expand=True, padx=4, pady=(0, 4))

        self.tree = ttk.Treeview(container, show="headings", selectmode="browse",
                                  style="CSV.Treeview")
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
            delimiter, quote_all = detect_csv_dialect(path)
            with open(path, newline="", encoding="utf-8-sig") as f:
                reader = csv.reader(f, delimiter=delimiter)
                lines = list(reader)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to read file:\n{e}")
            return

        if not lines:
            messagebox.showwarning("Empty file", "The CSV file is empty.")
            return

        self.filepath = path
        self.delimiter = delimiter
        self.quote_all = quote_all
        self.headers = lines[0]
        self.rows = lines[1:]
        self.modified = False
        self._reset_filters()
        for i in range(3):
            self._filter_col_combos[i]["values"] = self.headers
        # Detect Date column
        self._date_sort_col = None
        for idx, h in enumerate(self.headers):
            if h.strip().lower() == "date":
                self._date_sort_col = idx
                break
        self._date_sort_enabled = False
        self._sort_date_btn.config(text="Sort by Date: OFF",
                                    state=tk.NORMAL if self._date_sort_col is not None else tk.DISABLED)
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
                writer = csv.writer(
                    f, delimiter=self.delimiter,
                    quoting=csv.QUOTE_ALL if self.quote_all else csv.QUOTE_MINIMAL)
                writer.writerow(self.headers)
                writer.writerows(self.rows)
            self.modified = False
            self._update_status()
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save file:\n{e}")

    # ── Tree display ──────────────────────────────────────────────────

    def _capture_col_widths(self):
        """Harvest the live column widths (including user drags) into
        _col_widths. Reassigning tree["columns"] in _refresh_tree resets all
        column config, so widths must be captured before every rebuild."""
        for cid in self.tree["columns"] or ():
            hdr = self.tree.heading(cid, "text")
            if hdr:
                self._col_widths[hdr] = int(self.tree.column(cid, "width"))

    def _refresh_tree(self):
        self._capture_col_widths()
        self.tree.delete(*self.tree.get_children())
        self._visible_indices = []

        col_ids = [f"c{i}" for i in range(len(self.headers))]
        self.tree["columns"] = col_ids

        for cid, hdr in zip(col_ids, self.headers):
            self.tree.heading(cid, text=hdr, anchor=tk.W)
            self.tree.column(cid, width=self._col_widths.get(hdr, 120),
                             minwidth=60, anchor=tk.W)

        # Build list of (real_idx, row) passing all active filters
        filtered = []
        for real_idx, row in enumerate(self.rows):
            show = True
            for f_col, f_val in self._filters:
                if f_col is not None and f_val is not None:
                    cell = row[f_col] if f_col < len(row) else ""
                    if cell != f_val:
                        show = False
                        break
            if show:
                filtered.append((real_idx, row))

        # Sort by Date if enabled
        if self._date_sort_enabled and self._date_sort_col is not None:
            filtered = self._sort_by_date(filtered)

        for real_idx, row in filtered:
            padded = row + [""] * (len(self.headers) - len(row))
            self.tree.insert("", tk.END, values=padded[:len(self.headers)])
            self._visible_indices.append(real_idx)

    def _update_status(self):
        name = os.path.basename(self.filepath) if self.filepath else "No file"
        mod = " *" if self.modified else ""
        dialect = ""
        if self.filepath and self.delimiter != ",":
            shown = {"\t": "tab"}.get(self.delimiter, f"'{self.delimiter}'")
            dialect = f"  |  {shown} delimited"
        self._status_var.set(
            f"{name}{mod}  |  {len(self.rows)} rows, {len(self.headers)} columns{dialect}")
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

    def _selected_index(self, require=False):
        """Returns the real index in self.rows for the selected tree item."""
        sel = self.tree.selection()
        if not sel:
            if require:
                messagebox.showinfo("No selection", "Select a row first.")
            return None
        tree_idx = self.tree.index(sel[0])
        return self._visible_indices[tree_idx] if tree_idx < len(self._visible_indices) else tree_idx

    def _commit_row_edit(self, select_idx):
        """Mark modified, rebuild the tree, and reselect the affected row."""
        self.modified = True
        self._refresh_tree()
        self._select_row(select_idx)
        self._update_status()

    def _insert_row_above(self):
        if not self.headers:
            return
        idx = self._selected_index()
        if idx is None:
            idx = 0  # insert at top if no selection
        self.rows.insert(idx, [""] * len(self.headers))
        self._commit_row_edit(idx)

    def _insert_row_below(self):
        if not self.headers:
            return
        idx = self._selected_index()
        if idx is None:
            idx = len(self.rows) - 1  # insert at end if no selection
        self.rows.insert(idx + 1, [""] * len(self.headers))
        self._commit_row_edit(idx + 1)

    def _copy_row(self):
        idx = self._selected_index(require=True)
        if idx is None:
            return
        self.rows.insert(idx + 1, list(self.rows[idx]))
        self._commit_row_edit(idx + 1)

    def _delete_row(self):
        real_idx = self._selected_index(require=True)
        if real_idx is None:
            return
        tree_idx = self.tree.index(self.tree.selection()[0])
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

    def _on_filter_col_changed(self, idx):
        col_name = self._filter_col_vars[idx].get()
        if col_name not in self.headers:
            return
        col_idx = self.headers.index(col_name)
        # Gather unique values in this column (respecting other filters)
        values = sorted({
            row[col_idx] if col_idx < len(row) else "" for row in self.rows
        })
        self._filter_val_combos[idx]["values"] = values
        self._filter_val_vars[idx].set("")
        self._filters[idx] = (None, None)
        self._update_filter_status()
        self._refresh_tree()
        self._update_status()

    def _on_filter_val_changed(self, idx):
        col_name = self._filter_col_vars[idx].get()
        val = self._filter_val_vars[idx].get()
        if col_name not in self.headers:
            return
        self._filters[idx] = (self.headers.index(col_name), val)
        self._refresh_tree()
        self._update_status()
        self._update_filter_status()

    def _update_filter_status(self):
        active = sum(1 for c, v in self._filters if c is not None and v is not None)
        if active:
            self._filter_status_var.set(
                f"Showing {len(self._visible_indices)} of {len(self.rows)} rows ({active} filter{'s' if active > 1 else ''} active)")
        else:
            self._filter_status_var.set("")

    def _reset_filters(self):
        """Clear all three filters and their combobox selections."""
        self._filters = [(None, None), (None, None), (None, None)]
        for i in range(3):
            self._filter_col_vars[i].set("")
            self._filter_val_vars[i].set("")
            self._filter_val_combos[i]["values"] = []
        self._filter_status_var.set("")

    def _clear_filter(self):
        self._reset_filters()
        self._refresh_tree()
        self._update_status()

    def _toggle_date_sort(self):
        self._date_sort_enabled = not self._date_sort_enabled
        self._sort_date_btn.config(
            text=f"Sort by Date: {'ON' if self._date_sort_enabled else 'OFF'}")
        self._refresh_tree()
        self._update_filter_status()

    def _sort_by_date(self, filtered):
        """Sort list of (real_idx, row) by the Date column, trying common date formats."""
        col = self._date_sort_col
        formats = ["%d/%m/%Y", "%Y-%m-%d", "%m/%d/%Y", "%d-%m-%Y", "%d %b %Y", "%d %B %Y"]

        def parse_date(row):
            val = row[col].strip() if col < len(row) else ""
            for fmt in formats:
                try:
                    return datetime.strptime(val, fmt)
                except (ValueError, TypeError):
                    continue
            return datetime.max  # unparseable goes to end

        return sorted(filtered, key=lambda pair: parse_date(pair[1]))

    # ── State persistence ─────────────────────────────────────────────

    def _load_state(self):
        path = STATE_FILE if os.path.exists(STATE_FILE) else LEGACY_STATE_FILE
        try:
            with open(path, encoding="utf-8") as f:
                state = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return

        if path == LEGACY_STATE_FILE:
            # One-time migration out of the repo directory.
            try:
                os.makedirs(STATE_DIR, exist_ok=True)
                with open(STATE_FILE, "w", encoding="utf-8") as f:
                    json.dump(state, f, indent=2)
                os.remove(LEGACY_STATE_FILE)
            except Exception:
                pass

        # Restore geometry
        geo = state.get("geometry")
        if geo:
            self.root.geometry(geo)

        # Restore saved column widths (must precede _load_csv, whose
        # _refresh_tree applies them when building the columns)
        saved_widths = state.get("col_widths")
        if isinstance(saved_widths, dict):
            self._col_widths = saved_widths

        # Restore last opened CSV
        path = state.get("filepath")
        if path and os.path.isfile(path):
            self._load_csv(path)

            # Restore filters (supports both old single-filter and new 3-filter state)
            saved_filters = state.get("filters")
            if saved_filters:
                for i, filt in enumerate(saved_filters[:3]):
                    fc = filt.get("col", "")
                    fv = filt.get("val", "")
                    if fc and fc in self.headers:
                        self._filter_col_vars[i].set(fc)
                        self._on_filter_col_changed(i)
                        if fv:
                            self._filter_val_vars[i].set(fv)
                            self._on_filter_val_changed(i)
            else:
                # Legacy single-filter state
                fc = state.get("filter_col", "")
                fv = state.get("filter_val", "")
                if fc and fc in self.headers:
                    self._filter_col_vars[0].set(fc)
                    self._on_filter_col_changed(0)
                    if fv:
                        self._filter_val_vars[0].set(fv)
                        self._on_filter_val_changed(0)

            # Restore date sort
            if state.get("date_sort_enabled") and self._date_sort_col is not None:
                self._date_sort_enabled = True
                self._sort_date_btn.config(text="Sort by Date: ON")
                self._refresh_tree()
                self._update_filter_status()

    def _save_state(self):
        self._capture_col_widths()
        state = {
            "geometry": self.root.geometry(),
            "filepath": self.filepath,
            "filters": [
                {"col": self._filter_col_vars[i].get(), "val": self._filter_val_vars[i].get()}
                for i in range(3)
            ],
            "date_sort_enabled": self._date_sort_enabled,
            "col_widths": self._col_widths,
        }
        try:
            os.makedirs(STATE_DIR, exist_ok=True)
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
