"""
EdgeTX Toolbox
==============
Unified launcher for all EdgeTX tools.

Tools included:
  1. Log Viewer          — plot telemetry sensor data from CSV flight logs
  2. Model Backup        — back up models + RADIO folder  (single / multi-TX)
  3. Model Compare       — side-by-side diff of two .yml model files
  4. Switch Remapper     — remap switches in a single or batch of .yml files
  5. Screen Reorder      — reorder screen layouts in a .yml model file

Requirements:  Python 3.8+  |  pip install matplotlib pandas
Run:           python edgetx_toolbox.py
"""

import ctypes
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)  # Sharp text on high-DPI screens
except Exception:
    pass

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
import os, sys, re, shutil, json
from datetime import datetime

# ── Try importing optional deps (only needed for Log Viewer) ─────────────────
try:
    import pandas as pd
    import matplotlib
    matplotlib.use("TkAgg")
    import matplotlib.pyplot as plt
    import matplotlib.ticker as ticker
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
    from matplotlib.lines import Line2D
    HAS_MPL = True
except ImportError:
    HAS_MPL = False

# ═══════════════════════════════════════════════════════════════════════════════
#  SHARED CONSTANTS & HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

# When running as a PyInstaller exe, __file__ points to a temp folder.
# Use sys.executable so settings always save next to the exe/script.
if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(sys.executable)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = os.path.join(APP_DIR, "edgetx_toolbox_settings.json")

BG        = "#f0f2f5"
SURFACE   = "#ffffff"
SURF2     = "#f1f3f5"
GRID      = "#dee2e6"
TEXT      = "#212529"
MUTED     = "#868e96"
ACCENT    = "#1971c2"
ACCENT2   = "#0f766e"
DANGER    = "#e03131"
NAV_BG    = "#1a3a5c"
NAV_FG    = "#ffffff"
NAV_SEL   = "#2563eb"
NAV_HOV   = "#1e4a7a"

SWITCHES = ["SA", "SB", "SC", "SD", "SE", "SF", "SG", "SH"]


def load_app_settings():
    defaults = {
        "log_viewer": {
            "profiles": {}, "last_profile": "",
            "threshold": 400, "show_threshold": False,
            "thresh_sensor": "", "window_geometry": ""
        },
        "backup": {
            "tx": [
                {"name": "Transmitter 1", "source": "", "destination": ""},
                {"name": "Transmitter 2", "source": "", "destination": ""}
            ],
            "active": 0
        },
        "sw_rulesets": {},   # {name: {"rules": [["SA","SB"],...], "notes": "..."}}
    }
    try:
        if os.path.exists(SETTINGS_FILE):
            with open(SETTINGS_FILE, "r") as f:
                saved = json.load(f)
            for k, v in saved.items():
                if k in defaults and isinstance(v, dict):
                    defaults[k].update(v)
                else:
                    defaults[k] = v
    except Exception:
        pass
    return defaults


def save_app_settings(data):
    try:
        with open(SETTINGS_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"Warning: could not save settings: {e}")


def norm_yaml(c):
    return c.replace('\r\n', '\n').replace('\r', '\n')


def hdr_val(c, key):
    hm = re.search(r'^header:\s*\n(.*?)(?=^\w)', c, re.M | re.S)
    if hm:
        m = re.search(rf'^\s+{key}:\s*"?([^"\n]+)"?', hm.group(1), re.M)
        if m:
            return m.group(1).strip()
    m = re.search(rf'^\s*{key}:\s*"?([^"\n]+)"?', c, re.M)
    return m.group(1).strip() if m else "N/A"


# ═══════════════════════════════════════════════════════════════════════════════
#  SCROLLABLE FRAME
# ═══════════════════════════════════════════════════════════════════════════════

class ScrollFrame(tk.Frame):
    def __init__(self, parent, **kwargs):
        super().__init__(parent, **kwargs)
        self.canvas    = tk.Canvas(self, bg=SURFACE, highlightthickness=0, bd=0)
        self.scrollbar = tk.Scrollbar(self, orient="vertical", command=self.canvas.yview,
                                      width=14, troughcolor=GRID, bg=SURF2,
                                      activebackground=MUTED, relief="flat")
        self.inner     = tk.Frame(self.canvas, bg=SURFACE)
        self.inner.bind("<Configure>", lambda _: self.canvas.configure(
            scrollregion=self.canvas.bbox("all")))
        self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        # Scrollbar always on right, always visible
        self.scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.canvas.bind("<Enter>", lambda _: self.canvas.bind_all("<MouseWheel>", self._scroll))
        self.canvas.bind("<Leave>", lambda _: self.canvas.unbind_all("<MouseWheel>"))

    def _scroll(self, event):
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")


# ═══════════════════════════════════════════════════════════════════════════════
#  MAIN APPLICATION
# ═══════════════════════════════════════════════════════════════════════════════

class EdgeTXToolbox(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title("EdgeTX Toolbox")
        self.configure(bg=BG)
        self.minsize(1100, 680)

        self.settings  = load_app_settings()
        self._pages    = {}
        self._nav_btns = {}
        self._active   = None

        self._build_shell()
        self._build_all_pages()
        self._show_page("Home")

        if self.settings["log_viewer"].get("window_geometry"):
            try:
                self.geometry(self.settings["log_viewer"]["window_geometry"])
            except Exception:
                pass

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── Shell ─────────────────────────────────────────────────────────────────

    def _build_shell(self):
        hdr = tk.Frame(self, bg=NAV_BG, height=48)
        hdr.pack(side=tk.TOP, fill=tk.X)
        hdr.pack_propagate(False)
        tk.Label(hdr, text="  EdgeTX Toolbox", font=("Segoe UI", 14, "bold"),
                 bg=NAV_BG, fg=NAV_FG).pack(side=tk.LEFT, padx=8)

        gh_link = tk.Label(hdr, text="github.com/Onewaytohell/EdgeTx-Toolbox",
                 font=("Segoe UI", 9, "underline"),
                 bg=NAV_BG, fg="#7aa7d0", cursor="hand2")
        gh_link.pack(side=tk.RIGHT, padx=16)
        gh_link.bind("<Button-1>", lambda e: __import__("webbrowser").open(
            "https://github.com/Onewaytohell/EdgeTx-Toolbox"))
        gh_link.bind("<Enter>", lambda e: gh_link.config(fg="white"))
        gh_link.bind("<Leave>", lambda e: gh_link.config(fg="#7aa7d0"))

        tk.Label(hdr, text="v1.0", font=("Segoe UI", 9),
                 bg=NAV_BG, fg="#7aa7d0").pack(side=tk.RIGHT, padx=4)

        self.nav = tk.Frame(self, bg=NAV_BG, width=180)
        self.nav.pack(side=tk.LEFT, fill=tk.Y)
        self.nav.pack_propagate(False)

        self.container = tk.Frame(self, bg=BG)
        self.container.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

    def _add_nav_btn(self, label, icon, page_name):
        btn = tk.Button(
            self.nav, text=f"  {icon}  {label}", anchor="w",
            font=("Segoe UI", 10), bg=NAV_BG, fg=NAV_FG,
            activebackground=NAV_HOV, activeforeground=NAV_FG,
            relief="flat", padx=12, pady=10, cursor="hand2", bd=0,
            command=lambda n=page_name: self._show_page(n)
        )
        btn.pack(fill=tk.X)
        self._nav_btns[page_name] = btn

    def _show_page(self, name):
        if self._active:
            self._nav_btns[self._active].config(bg=NAV_BG)
            self._pages[self._active].pack_forget()
        self._active = name
        self._nav_btns[name].config(bg=NAV_SEL)
        self._pages[name].pack(fill=tk.BOTH, expand=True)

    def _register_page(self, name):
        frame = tk.Frame(self.container, bg=BG)
        self._pages[name] = frame
        return frame

    def _build_all_pages(self):
        tk.Label(self.nav, text="  TOOLS", font=("Segoe UI", 8, "bold"),
                 bg=NAV_BG, fg="#5a8ab0", anchor="w").pack(fill=tk.X, pady=(16, 2))
        self._add_nav_btn("Home",           "🏠", "Home")
        self._add_nav_btn("Model Backup",   "💾", "Backup")
        self._add_nav_btn("Switch Remap",   "🔀", "SwitchRemap")
        self._add_nav_btn("Screen Reorder", "📋", "ScreenReorder")
        self._add_nav_btn("Model Compare",  "🔍", "Compare")
        self._add_nav_btn("Log Viewer",     "📈", "LogViewer")

        self._build_home()
        self._build_log_viewer()
        self._build_backup()
        self._build_compare()
        self._build_switch_remap()
        self._build_screen_reorder()

    # ── Common helpers ────────────────────────────────────────────────────────

    def _card(self, parent, title=None, pady=6, hdr_bg=None, hdr_fg=None):
        hbg = hdr_bg or SURF2
        hfg = hdr_fg or MUTED
        border = hdr_bg or GRID
        outer = tk.Frame(parent, bg=SURFACE, bd=1, relief="flat",
                         highlightbackground=border, highlightthickness=1)
        outer.pack(fill=tk.X, padx=16, pady=(pady, 0))
        if title:
            th = tk.Frame(outer, bg=hbg)
            th.pack(fill=tk.X)
            tk.Label(th, text=title, font=("Segoe UI", 9, "bold"),
                     bg=hbg, fg=hfg, anchor="w", padx=10, pady=4).pack(fill=tk.X)
        inner = tk.Frame(outer, bg=SURFACE)
        inner.pack(fill=tk.X, padx=10, pady=8)
        return inner

    def _btn(self, parent, text, cmd, bg=SURF2, fg=TEXT, bold=False, width=None):
        kw = {"width": width} if width else {}
        return tk.Button(parent, text=text, command=cmd, bg=bg, fg=fg,
                         activebackground=GRID, activeforeground=TEXT,
                         font=("Segoe UI", 10, "bold") if bold else ("Segoe UI", 9),
                         relief="flat", padx=10, pady=5, cursor="hand2", bd=1, **kw)

    def _blue_btn(self, parent, text, cmd, width=None):
        return self._btn(parent, text, cmd, bg="#1971c2", fg="white", bold=True, width=width)

    def _sep_label(self, parent, text):
        f = tk.Frame(parent, bg=SURFACE)
        f.pack(fill=tk.X, pady=(10, 3))
        tk.Label(f, text=text, bg=SURFACE, fg=MUTED,
                 font=("Segoe UI", 8, "bold")).pack(side=tk.LEFT)
        tk.Frame(f, bg=GRID, height=1).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 0), pady=5)

    # =========================================================================
    #  HOME
    # =========================================================================

    def _build_home(self):
        page = self._register_page("Home")

        hero = tk.Frame(page, bg=NAV_BG, height=110)
        hero.pack(fill=tk.X)
        hero.pack_propagate(False)
        tk.Label(hero, text="EdgeTX Toolbox", font=("Segoe UI", 22, "bold"),
                 bg=NAV_BG, fg="white").pack(pady=(22, 2))
        tk.Label(hero, text="All your EdgeTX tools in one place",
                 font=("Segoe UI", 11), bg=NAV_BG, fg="#7aa7d0").pack()

        grid = tk.Frame(page, bg=BG)
        grid.pack(fill=tk.BOTH, expand=True, padx=24, pady=20)

        tools = [
            ("💾", "Model Backup",
             "Back up models + RADIO folder.\nRenames files to model names.\nSupports 2 TX profiles.",
             "Backup", ACCENT2),
            ("🔀", "Switch Remap",
             "Remap switches in .yml files.\nUseful when moving models between radios.\nUses placeholders to avoid double-remapping.\nSingle file or batch mode.",
             "SwitchRemap", "#c2410c"),
            ("📋", "Screen Reorder",
             "Reorder screen layouts in .yml files.\nUse Up/Down buttons then save.\nNo manual editing required.",
             "ScreenReorder", "#b45309"),
            ("🔍", "Model Compare",
             "Side-by-side diff of two .yml files.\nCompares switches, mixes, expo, screens & more.",
             "Compare", "#7c3aed"),
            ("📈", "Log Viewer",
             "Plot telemetry from EdgeTX CSV logs.\nCompare multiple flights side by side.\nSave sensor profiles per heli.",
             "LogViewer", ACCENT),
        ]

        cols = 3
        for i, (icon, name, desc, pname, color) in enumerate(tools):
            row, col = divmod(i, cols)
            card = tk.Frame(grid, bg=SURFACE, bd=1, relief="flat",
                            highlightbackground="#93c5fd",
                            highlightthickness=1, cursor="hand2")
            card.grid(row=row, column=col, padx=8, pady=8, sticky="nsew")
            grid.columnconfigure(col, weight=1)
            grid.rowconfigure(row, weight=1)

            tk.Label(card, text=icon, font=("Segoe UI", 26), bg=SURFACE).pack(pady=(14, 4))
            tk.Label(card, text=name, font=("Segoe UI", 12, "bold"),
                     bg=SURFACE, fg=color).pack()
            tk.Label(card, text=desc, font=("Segoe UI", 10), bg=SURFACE, fg=MUTED,
                     wraplength=220, justify=tk.LEFT).pack(pady=(4, 14), padx=14)

            card.bind("<Button-1>", lambda e, n=pname: self._show_page(n))
            for child in card.winfo_children():
                child.bind("<Button-1>", lambda e, n=pname: self._show_page(n))

    # =========================================================================
    #  LOG VIEWER
    # =========================================================================

    class RulesList(list):
        """A list that can hold an attached notes widget reference."""
        def __init__(self):
            super().__init__()
            self._notes_widget = None

    SKIP_COLS       = {"Date", "Time", "ANT"}
    DEFAULT_ON_KW   = ["vibs", "thr", "rpm", "rqly", "curr", "rxbt", "txbat"]
    RIGHT_AXIS_KW   = ["rpm", "erpm"]
    FLIGHT_PALETTES = [
        ["#c2410c","#0369a1","#7c3aed","#047857","#be185d","#b45309"],
        ["#1d4ed8","#0f766e","#6d28d9","#15803d","#9333ea","#0e7490"],
        ["#be185d","#b91c1c","#c2410c","#92400e","#a16207","#9f1239"],
        ["#166534","#065f46","#0f766e","#1e40af","#3730a3","#0e7490"],
        ["#6b21a8","#4f46e5","#1d4ed8","#7c3aed","#0369a1","#0f766e"],
    ]
    # Single colour list — colour is per sensor name, style per flight
    SENSOR_COLOURS = [
        "#c2410c","#1d4ed8","#7c3aed","#047857","#be185d",
        "#b45309","#0369a1","#9333ea","#0f766e","#e11d48",
        "#0284c7","#16a34a","#d97706","#7c3aed","#0e7490",
        "#b91c1c","#6d28d9","#15803d","#92400e","#1e40af",
    ]
    # Line styles per flight: solid, dashed, dotted, dash-dot
    FLIGHT_LINESTYLES = [
        [],          # F1 solid
        [6, 3],      # F2 dashed
        [2, 2],      # F3 dotted
        [8, 3, 2, 3],# F4 dash-dot
        [4, 2],      # F5 short dash
    ]

    def _lv_parse_time(self, t):
        p = t.split(":")
        return int(p[0])*3600 + int(p[1])*60 + float(p[2])

    def _lv_load_csv(self, path):
        if not HAS_MPL:
            messagebox.showerror("Missing Libraries",
                "pandas and matplotlib are required.\n"
                "Run:  pip install pandas matplotlib")
            return None
        try:
            df = pd.read_csv(path)
            if "Time" not in df.columns:
                messagebox.showerror("Load Error",
                    f"{os.path.basename(path)}\nNo 'Time' column found.")
                return None
            t0      = self._lv_parse_time(df["Time"].iloc[0])
            elapsed = [self._lv_parse_time(t) - t0 for t in df["Time"]]
            sensors = {}
            for col in df.columns:
                if col in self.SKIP_COLS:
                    continue
                num = pd.to_numeric(df[col], errors="coerce")
                if num.notna().sum() > 0:
                    sensors[col] = num.tolist()
            dur = elapsed[-1] if elapsed else 0

            # Extract model name from filename e.g. Spectre_700-2026-04-25-105358.csv
            stem   = os.path.splitext(os.path.basename(path))[0]
            parts  = stem.rsplit("-", 3)
            model  = parts[0].replace("_", " ") if len(parts) == 4 else stem

            # Date formatted nicely
            date_str = ""
            if "Date" in df.columns:
                try:
                    from datetime import datetime as _dt
                    date_str = _dt.strptime(str(df["Date"].iloc[0]), "%Y-%m-%d").strftime("%d %b %Y")
                except Exception:
                    date_str = str(df["Date"].iloc[0])

            # Start time as 12hr AM/PM
            raw_start = df["Time"].iloc[0][:8]
            try:
                h, m, s2 = raw_start.split(":")
                h = int(h); ampm = "am" if h < 12 else "pm"
                start_ampm = f"{h%12 or 12}:{m}{ampm}"
            except Exception:
                start_ampm = raw_start

            return {
                "name": os.path.basename(path), "path": path,
                "model": model, "date": date_str, "start_ampm": start_ampm,
                "elapsed": elapsed, "times": df["Time"].str[:8].tolist(),
                "sensors": sensors,
                "duration": f"{int(dur//60)}m {int(dur%60)}s",
                "samples": len(elapsed), "start": raw_start
            }
        except Exception as e:
            messagebox.showerror("Load Error", f"{os.path.basename(path)}\n{e}")
            return None

    def _build_log_viewer(self):
        page = self._register_page("LogViewer")
        s    = self.settings["log_viewer"]

        self.lv_flights     = []
        self.lv_sensor_vars = []
        self.lv_all_sensors = []
        self.lv_active_fi   = 0   # which flight's sensors are shown in panel
        self.lv_indep_axes  = tk.BooleanVar(value=False)  # independent Y axes per sensor
        self.lv_thresh_var  = tk.IntVar(value=s.get("threshold", 400))
        self.lv_show_thresh = tk.BooleanVar(value=s.get("show_threshold", False))
        self.lv_thresh_sen  = tk.StringVar(value=s.get("thresh_sensor", ""))
        self.lv_profile     = tk.StringVar(value=s.get("last_profile", ""))

        bar = tk.Frame(page, bg=SURFACE, pady=6, padx=10)
        bar.pack(fill=tk.X)
        tk.Label(bar, text="Log Viewer", bg=SURFACE, fg=ACCENT,
                 font=("Segoe UI", 12, "bold")).pack(side=tk.LEFT, padx=(0,16))
        for lbl, cmd in [("+ Load Log", self._lv_load), ("- Remove", self._lv_remove),
                         ("x Clear",    self._lv_clear), ("Export PNG", self._lv_export)]:
            self._btn(bar, lbl, cmd).pack(side=tk.LEFT, padx=2)

        # Independent axes toggle
        self.lv_indep_btn = tk.Button(
            bar, text="  ○  Independent Axes  OFF  ",
            font=("Segoe UI", 9, "bold"), relief="flat", padx=10, pady=5,
            bg="#ffedd5", fg="#c2410c", cursor="hand2", bd=1,
            activebackground="#fed7aa", command=self._lv_toggle_indep_axes)
        self.lv_indep_btn.pack(side=tk.RIGHT, padx=8)

        if not HAS_MPL:
            tk.Label(page,
                text="pandas and matplotlib not installed.\nRun:  pip install pandas matplotlib",
                bg=BG, fg=DANGER, font=("Segoe UI", 12), justify=tk.CENTER).pack(expand=True)
            return

        tk.Frame(page, bg=GRID, height=1).pack(fill=tk.X)
        body = tk.Frame(page, bg=BG)
        body.pack(fill=tk.BOTH, expand=True)

        left = tk.Frame(body, bg=SURFACE, width=270)
        left.pack(side=tk.LEFT, fill=tk.Y)
        left.pack_propagate(False)
        tk.Frame(body, bg=GRID, width=1).pack(side=tk.LEFT, fill=tk.Y)
        chart_area = tk.Frame(body, bg=BG)
        chart_area.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.lv_status = tk.StringVar(value="Load an EdgeTX CSV log to begin.")

        self._lv_build_left(left)
        self._lv_build_chart(chart_area)

    def _lv_toggle_indep_axes(self):
        self.lv_indep_axes.set(not self.lv_indep_axes.get())
        if self.lv_indep_axes.get():
            self.lv_indep_btn.config(
                text="  ●  Independent Axes  ON  ",
                bg="#16a34a", fg="white", activebackground="#15803d")
        else:
            self.lv_indep_btn.config(
                text="  ○  Independent Axes  OFF  ",
                bg="#ffedd5", fg="#c2410c", activebackground="#fed7aa")
        self._lv_redraw()

    def _lv_build_left(self, parent):
        self._sep_label(parent, "LOADED FLIGHTS")
        self.lv_listbox = tk.Listbox(parent, bg=SURF2, fg=TEXT,
            selectbackground=ACCENT, selectforeground="white",
            font=("Segoe UI", 9), borderwidth=1, highlightthickness=0,
            height=4, relief="flat")
        self.lv_listbox.pack(fill=tk.X, padx=8, pady=(0,4))
        self.lv_listbox.bind("<<ListboxSelect>>", self._lv_on_select)

        self._sep_label(parent, "FLIGHT INFO")
        self.lv_info = tk.Label(parent, text="Select a flight above.",
            bg=SURFACE, fg=MUTED, font=("Segoe UI", 9),
            justify=tk.LEFT, anchor="w", wraplength=248, padx=8)
        self.lv_info.pack(fill=tk.X, pady=(0,2))

        self._sep_label(parent, "SENSOR PROFILES")
        pf = tk.Frame(parent, bg=SURFACE)
        pf.pack(fill=tk.X, padx=8, pady=(0,4))

        r1 = tk.Frame(pf, bg=SURFACE)
        r1.pack(fill=tk.X, pady=(0,4))
        self.lv_prof_combo = ttk.Combobox(r1, textvariable=self.lv_profile,
            state="readonly", width=15, font=("Segoe UI", 9))
        self.lv_prof_combo.pack(side=tk.LEFT, padx=(0,4))
        self.lv_prof_combo.bind("<<ComboboxSelected>>", self._lv_on_profile_sel)
        self._btn(r1, "Apply", self._lv_apply_profile).pack(side=tk.LEFT)

        r2 = tk.Frame(pf, bg=SURFACE)
        r2.pack(fill=tk.X)
        self._btn(r2, "Save as…", self._lv_save_profile).pack(side=tk.LEFT, padx=(0,4))
        self._btn(r2, "Rename",   self._lv_rename_profile).pack(side=tk.LEFT, padx=(0,4))
        self._btn(r2, "Delete",   self._lv_delete_profile).pack(side=tk.LEFT)

        self.lv_prof_status = tk.Label(pf, text="", bg=SURFACE, fg=ACCENT,
            font=("Segoe UI", 8, "italic"), anchor="w")
        self.lv_prof_status.pack(fill=tk.X, pady=(4,0))
        self._lv_refresh_profiles()

        self._sep_label(parent, "THRESHOLD LINE")
        tf = tk.Frame(parent, bg=SURFACE)
        tf.pack(fill=tk.X, padx=8, pady=(0,6))
        tk.Checkbutton(tf, text="Show threshold line", variable=self.lv_show_thresh,
            bg=SURFACE, fg=TEXT, selectcolor=GRID, activebackground=SURFACE,
            font=("Segoe UI", 9), command=self._lv_redraw).pack(anchor="w")

        sr = tk.Frame(tf, bg=SURFACE)
        sr.pack(fill=tk.X, pady=2)
        tk.Label(sr, text="Sensor:", bg=SURFACE, fg=MUTED,
                 font=("Segoe UI", 9)).pack(side=tk.LEFT)
        self.lv_thresh_menu = ttk.Combobox(sr, textvariable=self.lv_thresh_sen,
            state="readonly", width=14, font=("Segoe UI", 9))
        self.lv_thresh_menu.pack(side=tk.LEFT, padx=4)
        self.lv_thresh_menu.bind("<<ComboboxSelected>>", lambda _: self._lv_redraw())

        slr = tk.Frame(tf, bg=SURFACE)
        slr.pack(fill=tk.X, pady=2)
        self.lv_thresh_lbl = tk.Label(slr, text=str(self.lv_thresh_var.get()),
            bg=SURFACE, fg=DANGER, font=("Segoe UI", 13, "bold"), width=5)
        self.lv_thresh_lbl.pack(side=tk.RIGHT)
        tk.Scale(slr, from_=0, to=2000, orient=tk.HORIZONTAL,
            variable=self.lv_thresh_var, bg=SURFACE, fg=TEXT,
            troughcolor=GRID, highlightthickness=0, showvalue=False,
            command=lambda v: (self.lv_thresh_lbl.config(text=str(int(float(v)))),
                               self._lv_redraw())
        ).pack(side=tk.LEFT, fill=tk.X, expand=True)

        self._sep_label(parent, "SENSORS  (per flight)")
        self.lv_sensor_scroll = ScrollFrame(parent, bg=SURFACE)
        self.lv_sensor_scroll.pack(fill=tk.BOTH, expand=True)
        self._lv_rebuild_sensors()

    def _lv_build_chart(self, parent):
        # Flight title bar — shows model name, date, start time
        self.lv_title_var = tk.StringVar(value="No flight loaded")
        title_bar = tk.Frame(parent, bg="#0f172a")
        title_bar.pack(fill=tk.X, side=tk.TOP)
        self.lv_title_lbl = tk.Label(
            title_bar, textvariable=self.lv_title_var,
            bg="#0f172a", fg="white",
            font=("Segoe UI", 10, "bold"),
            anchor="center", padx=12, pady=6
        )
        self.lv_title_lbl.pack(fill=tk.X)

        self.lv_fig = plt.Figure(figsize=(10, 6), facecolor=BG)
        self.lv_ax1 = self.lv_fig.add_subplot(111)
        self.lv_ax2 = self.lv_ax1.twinx()
        self._lv_style_axes()
        self.lv_canvas = FigureCanvasTkAgg(self.lv_fig, master=parent)
        self.lv_canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        nf  = tk.Frame(parent, bg=SURF2)
        nf.pack(fill=tk.X)
        nav = NavigationToolbar2Tk(self.lv_canvas, nf)
        nav.config(bg=SURF2)
        # Disable the built-in coordinate display in the toolbar
        nav.set_message = lambda msg: None
        nav.update()
        self.lv_canvas.mpl_connect("motion_notify_event", self._lv_hover)
        self.lv_canvas.mpl_connect("axes_leave_event",   self._lv_hover_leave)
        self.lv_annot  = None   # floating annotation box
        self.lv_vline  = None   # vertical crosshair line
        self._lv_draw_empty()

    def _lv_style_axes(self):
        for ax in (self.lv_ax1, self.lv_ax2):
            ax.set_facecolor(SURFACE)
            ax.tick_params(colors=TEXT, labelsize=8)
            for spine in ax.spines.values():
                spine.set_edgecolor(GRID)
        self.lv_ax1.grid(True, color=GRID, linewidth=0.6)
        self.lv_ax1.set_xlabel("Elapsed (mm:ss)", color=MUTED, fontsize=9)
        self.lv_ax1.set_ylabel("", color=MUTED, fontsize=9)
        self.lv_ax2.set_ylabel("", color=MUTED, fontsize=9)
        # Hide right axis by default — shown only when data is plotted on it
        self.lv_ax2.set_visible(False)
        self.lv_fig.patch.set_facecolor(BG)
        self.lv_fig.tight_layout(pad=1.5, rect=[0, 0, 0.96, 1])

    def _lv_draw_empty(self):
        self.lv_ax1.cla(); self.lv_ax2.cla(); self._lv_style_axes()
        self.lv_ax1.text(0.5, 0.5, "Load an EdgeTX log CSV to begin",
            transform=self.lv_ax1.transAxes, ha="center", va="center",
            color=MUTED, fontsize=13, fontstyle="italic")
        if hasattr(self, "lv_title_var"):
            self._lv_update_title()
        self.lv_canvas.draw_idle()

    def _lv_rebuild_sensors(self):
        for w in self.lv_sensor_scroll.inner.winfo_children():
            w.destroy()
        if not self.lv_flights:
            tk.Label(self.lv_sensor_scroll.inner,
                text="Load a flight to see available sensors.",
                bg=SURFACE, fg=MUTED, font=("Segoe UI", 9),
                wraplength=230, justify=tk.LEFT).pack(anchor="w", padx=8, pady=8)
            return

        fi     = min(self.lv_active_fi, len(self.lv_flights) - 1)
        flight = self.lv_flights[fi]
        pal    = self.FLIGHT_PALETTES[fi % len(self.FLIGHT_PALETTES)]

        # Header showing which flight we're editing
        hdr = tk.Frame(self.lv_sensor_scroll.inner, bg="#dbeafe")
        hdr.pack(fill=tk.X, padx=4, pady=(6, 2))
        tk.Label(hdr, text="  ■", bg="#dbeafe", fg=pal[0],
                 font=("Segoe UI", 10)).pack(side=tk.LEFT)
        model = flight.get("model", flight["name"])
        tk.Label(hdr, text=f" F{fi+1}: {model}  —  select sensors to plot",
                 bg="#dbeafe", fg="#1e40af",
                 font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT)

        # All On / All Off
        qr = tk.Frame(self.lv_sensor_scroll.inner, bg=SURFACE)
        qr.pack(fill=tk.X, padx=16, pady=(2, 4))

        def make_toggle(val):
            def _t():
                for v in self.lv_sensor_vars[fi].values():
                    v.set(val)
                self._lv_redraw()
            return _t

        for lbl, val in [("All On", True), ("All Off", False)]:
            tk.Button(qr, text=lbl, command=make_toggle(val),
                bg=SURF2, fg=TEXT, font=("Segoe UI", 8),
                relief="flat", padx=6, pady=1, cursor="hand2", bd=1
            ).pack(side=tk.LEFT, padx=(0, 4))

        # Sensor checkboxes — colours match exactly what appears on the chart
        colour_map = self._lv_get_colour_map()
        svars = self.lv_sensor_vars[fi]
        for sensor in svars.keys():
            color = colour_map.get(sensor, "#888888")
            row = tk.Frame(self.lv_sensor_scroll.inner, bg=SURFACE)
            row.pack(fill=tk.X, padx=16)
            tk.Checkbutton(row, text=sensor, variable=svars[sensor],
                bg=SURFACE, fg=color, selectcolor=SURF2,
                activebackground=SURFACE, font=("Segoe UI", 9),
                command=self._lv_redraw, anchor="w"
            ).pack(side=tk.LEFT, fill=tk.X)

    def _lv_refresh_profiles(self):
        names = list(self.settings["log_viewer"]["profiles"].keys())
        self.lv_prof_combo["values"] = names
        if self.lv_profile.get() not in names:
            self.lv_profile.set(names[0] if names else "")

    def _lv_get_colour_map(self):
        """Single source of truth for sensor -> colour mapping."""
        all_names = list(dict.fromkeys(
            s for f in self.lv_flights for s in f["sensors"].keys()))
        return {
            s: self.SENSOR_COLOURS[i % len(self.SENSOR_COLOURS)]
            for i, s in enumerate(all_names)
        }

    def _lv_update_sensor_union(self):
        seen = {}
        for f in self.lv_flights:
            for col in f["sensors"]:
                seen[col] = True
        self.lv_all_sensors = list(seen.keys())
        self.lv_thresh_menu["values"] = self.lv_all_sensors
        if self.lv_thresh_sen.get() not in self.lv_all_sensors and self.lv_all_sensors:
            self.lv_thresh_sen.set(self.lv_all_sensors[0])

    def _lv_load(self):
        paths = filedialog.askopenfilenames(
            title="Select EdgeTX log CSV(s)",
            filetypes=[("CSV files","*.csv"),("All files","*.*")])
        added = 0
        for path in paths:
            flight = self._lv_load_csv(path)
            if not flight:
                continue
            self.lv_flights.append(flight)
            svars = {}
            for col in flight["sensors"].keys():
                svars[col] = tk.BooleanVar(value=False)  # always start with nothing selected
            self.lv_sensor_vars.append(svars)
            self.lv_listbox.insert(tk.END, f"  F{len(self.lv_flights)}: {flight['name']}")
            added += 1
        if added:
            self._lv_update_sensor_union()
            # Auto-select the newly added flight in the list
            self.lv_listbox.selection_clear(0, tk.END)
            self.lv_listbox.selection_set(len(self.lv_flights) - 1)
            self.lv_active_fi = len(self.lv_flights) - 1
            self._lv_rebuild_sensors()
            self._lv_redraw()
            self.lv_status.set(
                f"{len(self.lv_flights)} flight(s) loaded  ·  "
                f"{len(self.lv_all_sensors)} sensors detected  ·  select sensors below.")

    def _lv_remove(self):
        sel = self.lv_listbox.curselection()
        if not sel:
            messagebox.showinfo("Remove","Select a flight first.")
            return
        idx = sel[0]
        self.lv_flights.pop(idx)
        self.lv_sensor_vars.pop(idx)
        self.lv_listbox.delete(0, tk.END)
        for i, f in enumerate(self.lv_flights):
            self.lv_listbox.insert(tk.END, f"  F{i+1}: {f['name']}")
        self._lv_update_sensor_union()
        self._lv_rebuild_sensors()
        self._lv_redraw()
        self.lv_status.set(f"{len(self.lv_flights)} flight(s) loaded.")

    def _lv_clear(self):
        self.lv_flights.clear()
        self.lv_sensor_vars.clear()
        self.lv_all_sensors.clear()
        self.lv_listbox.delete(0, tk.END)
        self.lv_info.config(text="Select a flight above.")
        self._lv_rebuild_sensors()
        self._lv_draw_empty()
        self.lv_status.set("All flights cleared.")

    def _lv_on_select(self, _e):
        sel = self.lv_listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        self.lv_active_fi = idx
        f = self.lv_flights[idx]
        self.lv_info.config(text=(
            f"Start:    {f['start']}\nDuration: {f['duration']}\n"
            f"Samples:  {f['samples']}\nSensors:  {len(f['sensors'])}"))
        self._lv_rebuild_sensors()

    def _lv_update_title(self):
        if not hasattr(self, "lv_title_var"):
            return
        if not self.lv_flights:
            self.lv_title_var.set("No flight loaded")
            return
        parts = []
        for fi, f in enumerate(self.lv_flights):
            parts.append(f"F{fi+1}  {f['model']}   {f['date']}   {f['start_ampm']}   {f['duration']}")
        self.lv_title_var.set("          |          ".join(parts))

    def _lv_redraw(self, *_):
        # Clean up any extra twin axes from independent mode
        if hasattr(self, "lv_extra_axes"):
            for ax in self.lv_extra_axes:
                try: ax.remove()
                except Exception: pass
            self.lv_extra_axes = []
        self.lv_ax1.cla()
        self.lv_ax2.cla()
        self.lv_annot = None
        self.lv_vline = None
        self._lv_style_axes()
        self._lv_update_title()
        if not self.lv_flights:
            self._lv_draw_empty()
            return
        legend_handles = []
        indep = self.lv_indep_axes.get()

        # Colour map — same source of truth as the sensor panel
        sensor_colour_map = self._lv_get_colour_map()

        if indep:
            # ── Independent axes mode ─────────────────────────────────────────
            # Collect every (flight, sensor, data, colour, linestyle) to plot
            plots = []
            for fi, (flight, svars) in enumerate(zip(self.lv_flights, self.lv_sensor_vars)):
                el        = flight["elapsed"]
                linestyle = self.FLIGHT_LINESTYLES[fi % len(self.FLIGHT_LINESTYLES)]
                lw        = 1.8 if fi == 0 else 1.5
                for sensor in svars.keys():
                    if not svars[sensor].get() or sensor not in flight["sensors"]:
                        continue
                    plots.append((fi, sensor, el, flight["sensors"][sensor],
                                  sensor_colour_map.get(sensor, "#888"),
                                  linestyle, lw, f"F{fi+1} {sensor}"))

            if not plots:
                self._lv_draw_empty()
                return

            # Use ax1 for the first sensor, create twin axes for others
            # Store extra axes so we can clean them up on redraw
            if not hasattr(self, "lv_extra_axes"):
                self.lv_extra_axes = []
            for ax in self.lv_extra_axes:
                try: ax.remove()
                except Exception: pass
            self.lv_extra_axes = []

            axes_list = []  # one per unique sensor name
            sensor_ax = {}  # sensor_name -> axis
            unique_sensors = list(dict.fromkeys(p[1] for p in plots))

            for i, sname in enumerate(unique_sensors):
                if i == 0:
                    ax = self.lv_ax1
                else:
                    ax = self.lv_ax1.twinx()
                    # Offset each extra axis spine outward
                    spine_pos = 1.0 + (i - 1) * 0.12
                    ax.spines["right"].set_position(("axes", spine_pos))
                    ax.spines["right"].set_visible(True)
                    self.lv_extra_axes.append(ax)
                ax.set_facecolor("none")
                ax.tick_params(labelsize=8)
                for sp in ax.spines.values():
                    sp.set_edgecolor(GRID)
                color = sensor_colour_map.get(sname, "#888")
                ax.set_ylabel(sname, color=color, fontsize=8)
                ax.tick_params(axis="y", colors=color, labelsize=8)
                sensor_ax[sname] = ax
                axes_list.append(ax)

            # Plot each series on its sensor axis
            for fi, sensor, el, data, color, linestyle, lw, label in plots:
                ax = sensor_ax[sensor]
                ls = (0, linestyle) if linestyle else "solid"
                ax.plot(el, data, color=color, linewidth=lw,
                        linestyle=ls, alpha=0.9)
                legend_handles.append(
                    Line2D([0],[0], color=color, lw=2,
                           linestyle=ls, label=label))

            # Scale each axis nicely with a little padding
            for ax in axes_list:
                ax.relim(); ax.autoscale_view()
                ymin, ymax = ax.get_ylim()
                pad = (ymax - ymin) * 0.08 if ymax != ymin else 1
                ax.set_ylim(ymin - pad, ymax + pad)

            self.lv_ax2.set_visible(False)

        else:
            # ── Shared axes mode (original) ───────────────────────────────────
            # Clean up any extra axes from indep mode
            if hasattr(self, "lv_extra_axes"):
                for ax in self.lv_extra_axes:
                    try: ax.remove()
                    except Exception: pass
                self.lv_extra_axes = []

            for fi, (flight, svars) in enumerate(zip(self.lv_flights, self.lv_sensor_vars)):
                el        = flight["elapsed"]
                linestyle = self.FLIGHT_LINESTYLES[fi % len(self.FLIGHT_LINESTYLES)]
                lw        = 1.8 if fi == 0 else 1.5
                for sensor in svars.keys():
                    if not svars[sensor].get() or sensor not in flight["sensors"]:
                        continue
                    data  = flight["sensors"][sensor]
                    color = sensor_colour_map.get(sensor, "#888888")
                    label = f"F{fi+1} {sensor}"
                    on_right = any(k in sensor.lower() for k in self.RIGHT_AXIS_KW)
                    ax = self.lv_ax2 if on_right else self.lv_ax1
                    ls = (0, linestyle) if linestyle else "solid"
                    ax.plot(el, data, color=color, linewidth=lw,
                            linestyle=ls, alpha=0.9)
                    legend_handles.append(
                        Line2D([0],[0], color=color, lw=2, linestyle=ls, label=label))

            ax1_sensors = [h.get_label() for h in legend_handles
                           if not any(k in h.get_label().lower() for k in self.RIGHT_AXIS_KW)]
            ax2_sensors = [h.get_label() for h in legend_handles
                           if any(k in h.get_label().lower() for k in self.RIGHT_AXIS_KW)]
            has_ax2 = bool(ax2_sensors)
            self.lv_ax2.set_visible(has_ax2)
            if has_ax2:
                self.lv_ax2.set_ylabel(", ".join(set(
                    s.split()[-1] for s in ax2_sensors))[:20], color=MUTED, fontsize=9)
            if ax1_sensors:
                names = list(dict.fromkeys(
                    s.split()[-1] for s in ax1_sensors if "Threshold" not in s))
                self.lv_ax1.set_ylabel(", ".join(names[:3]), color=MUTED, fontsize=9)
            self.lv_ax1.relim(); self.lv_ax1.autoscale_view()
            if has_ax2:
                self.lv_ax2.relim(); self.lv_ax2.autoscale_view()

        if self.lv_show_thresh.get():
            thresh = self.lv_thresh_var.get()
            self.lv_ax1.axhline(y=thresh, color=DANGER, linewidth=1.2,
                                linestyle="--", alpha=0.8)
            legend_handles.append(Line2D([0],[0],color=DANGER,lw=1.5,linestyle="--",
                label=f"Threshold {thresh} ({self.lv_thresh_sen.get()})"))
        def fmt(x, _):
            return f"{int(x//60)}:{int(x%60):02d}"
        self.lv_ax1.xaxis.set_major_formatter(ticker.FuncFormatter(fmt))
        self.lv_ax1.xaxis.set_major_locator(ticker.MultipleLocator(30))
        self.lv_fig.autofmt_xdate(rotation=0, ha="center")
        self.lv_fig.tight_layout(pad=1.5, rect=[0, 0, 0.96, 1])
        if legend_handles:
            shown = legend_handles[:24]
            if len(legend_handles) > 24:
                shown.append(Line2D([0],[0],color="none",
                                    label=f"+{len(legend_handles)-24} more"))
            self.lv_ax1.legend(handles=shown, loc="upper right",
                facecolor=SURFACE, edgecolor=GRID, labelcolor=TEXT,
                fontsize=7, framealpha=0.93, ncol=2)
        self.lv_canvas.draw_idle()

    def _lv_hover_leave(self, event):
        """Hide annotation when mouse leaves axes."""
        if self.lv_annot:
            self.lv_annot.set_visible(False)
        if self.lv_vline:
            self.lv_vline.set_visible(False)
        self.lv_canvas.draw_idle()

    def _lv_hover(self, event):
        if not HAS_MPL:
            return
        valid_axes = {self.lv_ax1, self.lv_ax2}
        if hasattr(self, "lv_extra_axes"):
            valid_axes.update(self.lv_extra_axes)
        if event.inaxes not in valid_axes or not self.lv_flights:
            return
        x = event.xdata
        if x is None:
            return

        # Build popup text
        lines = []
        for fi, (flight, svars) in enumerate(zip(self.lv_flights, self.lv_sensor_vars)):
            el  = flight["elapsed"]
            idx = min(range(len(el)), key=lambda j: abs(el[j]-x))
            active = [s for s,v in svars.items()
                      if v.get() and s in flight["sensors"]]
            if active:
                # Format clock time as 12hr AM/PM  e.g. "10:53am"
                raw_time = flight["times"][idx]   # "HH:MM:SS"
                try:
                    h, m, s2 = raw_time.split(":")
                    h = int(h); m = int(m)
                    ampm  = "am" if h < 12 else "pm"
                    h12   = h % 12 or 12
                    clock = f"{h12}:{m:02d}{ampm}"
                except Exception:
                    clock = raw_time

                # Format elapsed time as  m:ss  e.g. "1:23"
                secs    = el[idx]
                elapsed = f"{int(secs//60)}:{int(secs%60):02d}"

                lines.append(f"── F{fi+1}  +{elapsed} ──")
                for s in active[:8]:
                    val = flight["sensors"][s][idx]
                    lines.append(f"  {s:<14} {val:.1f}")
        if not lines:
            return

        popup_text = "\n".join(lines)

        # Vertical crosshair line
        if self.lv_vline:
            self.lv_vline.set_xdata([x, x])
            self.lv_vline.set_visible(True)
        else:
            self.lv_vline = self.lv_ax1.axvline(
                x=x, color="#94a3b8", linewidth=1,
                linestyle="--", alpha=0.7, zorder=3)

        # Floating annotation box — always use ax1 for positioning
        ax   = self.lv_ax1
        xlim = ax.get_xlim()
        ylim = ax.get_ylim()

        x_frac = (x - xlim[0]) / (xlim[1] - xlim[0]) if xlim[1] != xlim[0] else 0.5
        # Use mouse pixel position for vertical fraction — works across all twin axes
        y_frac = 1.0 - (event.y / self.lv_fig.bbox.height) if self.lv_fig.bbox.height > 0 else 0.5

        if x_frac > 0.55:
            offset_x = -30
            h_align  = "right"
        else:
            offset_x = 30
            h_align  = "left"

        if y_frac > 0.55:
            offset_y = -30
            v_align  = "top"
        else:
            offset_y = 30
            v_align  = "bottom"

        # Anchor to ax1 coordinate space — midpoint of y range
        y_anchor = ylim[0] + (ylim[1] - ylim[0]) * (1.0 - y_frac)

        # Always remove and recreate annotation so it sits on the topmost axis
        if self.lv_annot:
            try:
                self.lv_annot.remove()
            except Exception:
                pass
            self.lv_annot = None

        # Use the last extra axis if in indep mode (drawn on top), else ax1
        top_ax = self.lv_extra_axes[-1] if (
            hasattr(self, "lv_extra_axes") and self.lv_extra_axes
        ) else self.lv_ax1

        self.lv_annot = top_ax.annotate(
            popup_text,
            xy=(x, y_anchor),
            xytext=(offset_x, offset_y),
            textcoords="offset points",
            fontsize=8.5,
            fontfamily="monospace",
            verticalalignment=v_align,
            horizontalalignment=h_align,
            bbox=dict(
                boxstyle="round,pad=0.6",
                facecolor="#0f172a",
                edgecolor="#334155",
                alpha=0.95,
                linewidth=1.5
            ),
            color="white",
            zorder=999,
            clip_on=False
        )

        self.lv_canvas.draw_idle()

    def _lv_export(self):
        if not self.lv_flights:
            messagebox.showinfo("Export","Load a flight first.")
            return
        path = filedialog.asksaveasfilename(defaultextension=".png",
            filetypes=[("PNG","*.png"),("All","*.*")],
            initialfile="edgetx_telemetry.png")
        if path:
            self.lv_fig.savefig(path, dpi=150, bbox_inches="tight", facecolor=BG)
            self.lv_status.set(f"Saved → {path}")

    def _lv_on_profile_sel(self, _=None):
        name = self.lv_profile.get()
        if name in self.settings["log_viewer"]["profiles"]:
            c = len(self.settings["log_viewer"]["profiles"][name])
            self.lv_prof_status.config(text=f"{name}  ·  {c} sensor(s)")

    def _lv_apply_profile(self, name=None):
        name = name or self.lv_profile.get()
        if not name or name not in self.settings["log_viewer"]["profiles"]:
            messagebox.showinfo("Apply Profile","No profile selected.\nSave one first.")
            return
        s_set = set(self.settings["log_viewer"]["profiles"][name])
        # Apply only to the currently selected flight
        fi = min(self.lv_active_fi, len(self.lv_sensor_vars) - 1)
        if fi < 0:
            return
        for sensor, var in self.lv_sensor_vars[fi].items():
            var.set(sensor in s_set)
        flight_name = self.lv_flights[fi].get("model", f"F{fi+1}")
        self.lv_prof_status.config(text=f"Applied to F{fi+1} ({flight_name}): {name}")
        self._lv_rebuild_sensors()
        self._lv_redraw()

    def _lv_save_profile(self):
        sel = {}
        for svars in self.lv_sensor_vars:
            for name, var in svars.items():
                if var.get():
                    sel[name] = True
        if not sel:
            messagebox.showwarning("Save Profile","No sensors selected.")
            return
        name = simpledialog.askstring("Save Profile",
            "Profile name (e.g. 'Goblin 700'):",
            initialvalue=self.lv_profile.get() or "", parent=self)
        if not name or not name.strip():
            return
        name = name.strip()
        if name in self.settings["log_viewer"]["profiles"]:
            if not messagebox.askyesno("Overwrite?", f"Overwrite '{name}'?"):
                return
        self.settings["log_viewer"]["profiles"][name] = list(sel.keys())
        self.lv_profile.set(name)
        self._lv_refresh_profiles()
        save_app_settings(self.settings)
        self.lv_prof_status.config(text=f"Saved: {name}  ({len(sel)} sensors)")

    def _lv_rename_profile(self):
        old = self.lv_profile.get()
        if not old or old not in self.settings["log_viewer"]["profiles"]:
            messagebox.showinfo("Rename","Select a profile first.")
            return
        new = simpledialog.askstring("Rename","New name:",
            initialvalue=old, parent=self)
        if not new or not new.strip() or new.strip() == old:
            return
        new = new.strip()
        if new in self.settings["log_viewer"]["profiles"]:
            messagebox.showerror("Rename",f"'{new}' already exists.")
            return
        self.settings["log_viewer"]["profiles"][new] = \
            self.settings["log_viewer"]["profiles"].pop(old)
        self.lv_profile.set(new)
        self._lv_refresh_profiles()
        save_app_settings(self.settings)

    def _lv_delete_profile(self):
        name = self.lv_profile.get()
        if not name or name not in self.settings["log_viewer"]["profiles"]:
            messagebox.showinfo("Delete","Select a profile first.")
            return
        if not messagebox.askyesno("Delete Profile", f"Delete '{name}'?"):
            return
        del self.settings["log_viewer"]["profiles"][name]
        save_app_settings(self.settings)
        self._lv_refresh_profiles()
        self.lv_prof_status.config(text="")

    # =========================================================================
    #  MODEL BACKUP  (single + multi-TX batch toggle)
    # =========================================================================

    def _build_backup(self):
        page = self._register_page("Backup")

        top = tk.Frame(page, bg=SURFACE, pady=8, padx=12)
        top.pack(fill=tk.X)
        tk.Label(top, text="Model Backup", bg=SURFACE, fg=ACCENT2,
                 font=("Segoe UI", 12, "bold")).pack(side=tk.LEFT)
        self.bk_batch_var = tk.BooleanVar(value=False)
        self.bk_toggle_btn = tk.Button(
            top, text="  ○  Multi-TX Mode  OFF  ",
            font=("Segoe UI", 9, "bold"), relief="flat", padx=10, pady=5,
            bg="#ffedd5", fg="#c2410c", cursor="hand2", bd=1,
            activebackground="#fed7aa", command=self._bk_toggle_mode)
        self.bk_toggle_btn.pack(side=tk.RIGHT, padx=12)
        tk.Frame(page, bg=GRID, height=1).pack(fill=tk.X)

        self.bk_single_frame = tk.Frame(page, bg=BG)
        self.bk_single_frame.pack(fill=tk.BOTH, expand=True)
        self._bk_build_single(self.bk_single_frame)

        self.bk_multi_frame = tk.Frame(page, bg=BG)
        self._bk_build_multi(self.bk_multi_frame)

    def _bk_toggle_mode(self):
        self.bk_batch_var.set(not self.bk_batch_var.get())
        if self.bk_batch_var.get():
            self.bk_toggle_btn.config(
                text="  ●  Multi-TX Mode  ON  ",
                bg="#16a34a", fg="white", activebackground="#15803d")
            self.bk_single_frame.pack_forget()
            self.bk_multi_frame.pack(fill=tk.BOTH, expand=True)
        else:
            self.bk_toggle_btn.config(
                text="  ○  Multi-TX Mode  OFF  ",
                bg="#ffedd5", fg="#c2410c", activebackground="#fed7aa")
            self.bk_multi_frame.pack_forget()
            self.bk_single_frame.pack(fill=tk.BOTH, expand=True)

    def _bk_build_single(self, parent):
        s = self.settings["backup"]
        src_val = s["tx"][0]["source"]
        src_card = self._card(parent, "Models Source Folder  (e.g. E:\\MODELS)", pady=12, hdr_bg="#dbeafe", hdr_fg="#1e40af")
        self.bk_s_src_lbl = tk.Label(src_card,
            text=src_val or "No folder selected",
            fg=TEXT if src_val else MUTED, bg=SURFACE, font=("Segoe UI",9), anchor="w")
        self.bk_s_src_lbl.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._btn(src_card,"Browse…",self._bk_s_browse_src).pack(side=tk.RIGHT)

        dst_val = s["tx"][0]["destination"]
        dst_card = self._card(parent, "Backup Destination Folder", hdr_bg="#dbeafe", hdr_fg="#1e40af")
        self.bk_s_dst_lbl = tk.Label(dst_card,
            text=dst_val or "No folder selected",
            fg=TEXT if dst_val else MUTED, bg=SURFACE, font=("Segoe UI",9), anchor="w")
        self.bk_s_dst_lbl.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._btn(dst_card,"Browse…",self._bk_s_browse_dst).pack(side=tk.RIGHT)

        desc_card = self._card(parent, "Backup Description (optional)", hdr_bg="#dbeafe", hdr_fg="#1e40af")
        self.bk_s_desc = tk.StringVar()
        tk.Entry(desc_card, textvariable=self.bk_s_desc,
                 font=("Segoe UI",10)).pack(fill=tk.X)
        tk.Label(desc_card,
            text='Leave blank for date only  e.g. "23feb26mybuild"',
            font=("Segoe UI",8), fg=MUTED, bg=SURFACE).pack(anchor="w")

        btn_row = tk.Frame(parent, bg=BG)
        btn_row.pack(pady=14)
        self._blue_btn(btn_row,"Back Up Now",self._bk_s_run,width=18).pack()

        self.bk_s_status = tk.StringVar(
            value="Ready. Configure folders and click Back Up Now.")
        tk.Label(parent, textvariable=self.bk_s_status,
                 font=("Segoe UI",9), fg=MUTED, bg=BG, wraplength=600).pack()

    def _bk_s_browse_src(self):
        path = filedialog.askdirectory(title="Select Models Source Folder")
        if path:
            self.settings["backup"]["tx"][0]["source"] = path
            self.bk_s_src_lbl.config(text=path, fg=TEXT)
            save_app_settings(self.settings)

    def _bk_s_browse_dst(self):
        path = filedialog.askdirectory(title="Select Backup Destination Folder")
        if path:
            self.settings["backup"]["tx"][0]["destination"] = path
            self.bk_s_dst_lbl.config(text=path, fg=TEXT)
            save_app_settings(self.settings)

    def _bk_s_run(self):
        src = self.settings["backup"]["tx"][0].get("source","")
        dst = self.settings["backup"]["tx"][0].get("destination","")
        self._bk_run_backup(src, dst, self.bk_s_desc.get(), self.bk_s_status)

    def _bk_build_multi(self, parent):
        s = self.settings["backup"]
        self.bk_m_active    = tk.IntVar(value=s.get("active",0))
        self.bk_m_name_vars = []
        self.bk_m_src_prev  = []

        sel_card = self._card(parent, "Transmitter Profile", pady=12, hdr_bg="#dbeafe", hdr_fg="#1e40af")
        for i in range(2):
            row = tk.Frame(sel_card, bg=SURFACE)
            row.pack(fill=tk.X, pady=3)
            tk.Radiobutton(row, variable=self.bk_m_active, value=i,
                command=self._bk_m_switch, bg=SURFACE).pack(side=tk.LEFT)
            nv = tk.StringVar(value=s["tx"][i]["name"])
            nv.trace_add("write", lambda *_, idx=i, v=nv: self._bk_m_rename(idx,v))
            self.bk_m_name_vars.append(nv)
            tk.Entry(row, textvariable=nv, font=("Segoe UI",9,"bold"),
                relief="flat", width=20,
                highlightthickness=1, highlightbackground=GRID).pack(side=tk.LEFT, padx=4)
            src = s["tx"][i]["source"]
            pl  = tk.Label(row, text=src or "not configured",
                fg=TEXT if src else MUTED, font=("Segoe UI",8), anchor="w")
            pl.pack(side=tk.LEFT, fill=tk.X, expand=True)
            self.bk_m_src_prev.append(pl)

        src_card = self._card(parent, "Models Source Folder")
        ai = s["active"]
        self.bk_m_src_lbl = tk.Label(src_card,
            text=s["tx"][ai]["source"] or "No folder selected",
            fg=TEXT if s["tx"][ai]["source"] else MUTED,
            bg=SURFACE, font=("Segoe UI",9), anchor="w")
        self.bk_m_src_lbl.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._btn(src_card,"Browse…",self._bk_m_browse_src).pack(side=tk.RIGHT)

        dst_card = self._card(parent, "Backup Destination Folder", hdr_bg="#dbeafe", hdr_fg="#1e40af")
        self.bk_m_dst_lbl = tk.Label(dst_card,
            text=s["tx"][ai]["destination"] or "No folder selected",
            fg=TEXT if s["tx"][ai]["destination"] else MUTED,
            bg=SURFACE, font=("Segoe UI",9), anchor="w")
        self.bk_m_dst_lbl.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._btn(dst_card,"Browse…",self._bk_m_browse_dst).pack(side=tk.RIGHT)

        desc_card = self._card(parent, "Backup Description (optional)", hdr_bg="#dbeafe", hdr_fg="#1e40af")
        self.bk_m_desc = tk.StringVar()
        tk.Entry(desc_card, textvariable=self.bk_m_desc,
                 font=("Segoe UI",10)).pack(fill=tk.X)
        tk.Label(desc_card, text='Leave blank for date only',
                 font=("Segoe UI",8), fg=MUTED, bg=SURFACE).pack(anchor="w")

        btn_row = tk.Frame(parent, bg=BG)
        btn_row.pack(pady=14)
        self._blue_btn(btn_row,"Back Up Now",self._bk_m_run,width=18).pack()

        self.bk_m_status = tk.StringVar(value="Ready.")
        tk.Label(parent, textvariable=self.bk_m_status,
                 font=("Segoe UI",9), fg=MUTED, bg=BG, wraplength=600).pack()

    def _bk_m_active_tx(self):
        return self.settings["backup"]["tx"][self.bk_m_active.get()]

    def _bk_m_switch(self):
        self.settings["backup"]["active"] = self.bk_m_active.get()
        tx = self._bk_m_active_tx()
        self.bk_m_src_lbl.config(
            text=tx["source"] or "No folder selected",
            fg=TEXT if tx["source"] else MUTED)
        self.bk_m_dst_lbl.config(
            text=tx["destination"] or "No folder selected",
            fg=TEXT if tx["destination"] else MUTED)
        save_app_settings(self.settings)

    def _bk_m_rename(self, idx, var):
        self.settings["backup"]["tx"][idx]["name"] = var.get()
        save_app_settings(self.settings)

    def _bk_m_browse_src(self):
        path = filedialog.askdirectory(title="Select Models Source Folder")
        if path:
            self._bk_m_active_tx()["source"] = path
            self.bk_m_src_lbl.config(text=path, fg=TEXT)
            self.bk_m_src_prev[self.bk_m_active.get()].config(text=path, fg=TEXT)
            save_app_settings(self.settings)

    def _bk_m_browse_dst(self):
        path = filedialog.askdirectory(title="Select Backup Destination Folder")
        if path:
            self._bk_m_active_tx()["destination"] = path
            self.bk_m_dst_lbl.config(text=path, fg=TEXT)
            save_app_settings(self.settings)

    def _bk_m_run(self):
        tx = self._bk_m_active_tx()
        self._bk_run_backup(tx.get("source",""), tx.get("destination",""),
                            self.bk_m_desc.get(), self.bk_m_status)

    # Shared backup engine
    def _bk_get_model_name(self, filepath):
        try:
            with open(filepath,"r",encoding="utf-8") as f:
                c = f.read()
            c = c.replace('\r\n','\n').replace('\r','\n')
            hdr = re.search(r'^header:\s*\n(.*?)(?=^\w)', c, re.M|re.S)
            if hdr:
                m = re.search(r'^\s+name:\s*"?([^"\n]+)"?', hdr.group(1), re.M)
                if m:
                    return re.sub(r'[\\/:*?"<>|]','_', m.group(1).strip())
        except Exception:
            pass
        return None

    def _bk_folder_name(self, desc):
        d = datetime.now().strftime("%d%b%y").lower()
        if desc.strip():
            safe = desc.strip()
            for ch in ['\\', '/', ':', '*', '?', '"', '<', '>', '|']:
                safe = safe.replace(ch, '_')
            safe = '_'.join(safe.split())  # collapse whitespace to single _
            return f"{d}_{safe}"
        return d

    def _bk_unique_folder(self, base, name):
        path = os.path.join(base, name)
        if not os.path.exists(path):
            return path
        n = 2
        while True:
            c = os.path.join(base, f"{name}_{n}")
            if not os.path.exists(c):
                return c
            n += 1

    def _bk_run_backup(self, source, destination, desc, status_var):
        if not source:
            messagebox.showerror("No Source","Please select a source folder.")
            return
        if not destination:
            messagebox.showerror("No Destination","Please select a destination folder.")
            return
        if not os.path.exists(source):
            messagebox.showerror("Not Found",
                f"Source not found:\n{source}\n\nMake sure your TX is plugged in.")
            return
        all_yml     = [f for f in os.listdir(source)
                       if f.lower().endswith(('.yml','.yaml'))]
        model_files = [f for f in all_yml if f.lower() != 'labels.yml']
        if not model_files:
            messagebox.showerror("No Files", f"No model files in:\n{source}")
            return

        bk_folder  = self._bk_unique_folder(destination, self._bk_folder_name(desc))
        mdl_folder = os.path.join(bk_folder, "MODELS")
        try:
            os.makedirs(bk_folder)
            os.makedirs(mdl_folder)
        except Exception as e:
            messagebox.showerror("Folder Error", str(e))
            return

        for fn in all_yml:
            try:
                shutil.copy2(os.path.join(source,fn), os.path.join(mdl_folder,fn))
            except Exception:
                pass

        success = skipped = errors = 0
        used = {}
        for fn in model_files:
            sp = os.path.join(source, fn)
            nm = self._bk_get_model_name(sp)
            if nm:
                base = nm
                if base in used:
                    used[base] += 1
                    base = f"{nm}_{used[base]}"
                else:
                    used[nm] = 1
                new_fn = base + os.path.splitext(fn)[1]
            else:
                new_fn = fn
                skipped += 1
            try:
                shutil.copy2(sp, os.path.join(bk_folder, new_fn))
                success += 1
            except Exception:
                errors += 1

        radio_src  = os.path.join(os.path.dirname(source.rstrip("/\\")), "RADIO")
        radio_dst  = os.path.join(bk_folder, "RADIO")
        radio_note = ""
        if os.path.exists(radio_src):
            try:
                shutil.copytree(radio_src, radio_dst)
                rc = sum(len(fs) for _,_,fs in os.walk(radio_dst))
                radio_note = f"\n\nRADIO folder backed up ({rc} files)"
            except Exception as e:
                radio_note = f"\n\nRADIO backup failed: {e}"
        else:
            radio_note = f"\n\nRADIO folder not found at:\n{radio_src}  (skipped)"

        summary = (f"{success} model(s) backed up to:\n{bk_folder}"
                   f"\n\nMODELS (raw) → {mdl_folder}")
        summary += radio_note
        if skipped: summary += f"\n\n{skipped} file(s) kept original name."
        if errors:  summary += f"\n\n{errors} error(s)."
        status_var.set(
            f"Done — {success} models backed up to {os.path.basename(bk_folder)}")
        messagebox.showinfo("Backup Complete", summary)

    # =========================================================================
    #  MODEL COMPARE
    # =========================================================================

    SW_PILL_COL = {
        "A":"#c0392b","B":"#27ae60","C":"#2980b9","D":"#8e44ad",
        "E":"#d35400","F":"#16a085","G":"#c0392b","H":"#7f8c8d"
    }

    def _build_compare(self):
        page = self._register_page("Compare")
        self.cmp_ca = self.cmp_cb = self.cmp_fa = self.cmp_fb = ""
        self.cmp_hide    = tk.BooleanVar(value=False)
        self.cmp_exp_txt = ""

        hdr = tk.Frame(page, bg=SURFACE, pady=8, padx=12)
        hdr.pack(fill=tk.X)
        tk.Label(hdr, text="Model Compare", bg=SURFACE, fg="#7c3aed",
                 font=("Segoe UI", 12, "bold")).pack(side=tk.LEFT)
        tk.Frame(page, bg=GRID, height=1).pack(fill=tk.X)

        ff = tk.Frame(page, bg=SURFACE, pady=6)
        ff.pack(fill=tk.X)
        row = tk.Frame(ff, bg=SURFACE)
        row.pack(fill=tk.X, padx=10)

        for side, lbl_attr, title, fg in [
                ("a","cmp_fa_lbl","Model A","#7a3a00"),
                ("b","cmp_fb_lbl","Model B","#1a5a20")]:
            fr = tk.LabelFrame(row, text=title,
                font=("Segoe UI",8,"bold"), bg=SURFACE, fg=fg, padx=6, pady=4)
            fr.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0,6))
            lbl = tk.Label(fr, text="No file selected", anchor="w",
                           fg=MUTED, font=("Segoe UI",9), bg=SURFACE)
            lbl.pack(side=tk.LEFT, fill=tk.X, expand=True)
            setattr(self, lbl_attr, lbl)
            self._btn(fr,"Browse…",
                lambda s=side: self._cmp_browse(s)).pack(side=tk.RIGHT)

        self._blue_btn(row,"Compare Models",self._cmp_compare).pack(side=tk.LEFT,pady=2)

        ob = tk.Frame(page, bg=SURF2, pady=4)
        ob.pack(fill=tk.X)
        self.cmp_status = tk.StringVar(
            value="Load two model files and click Compare.")
        tk.Label(ob, textvariable=self.cmp_status,
                 font=("Segoe UI",9), bg=SURF2, fg=MUTED).pack(side=tk.LEFT, padx=10)
        self._btn(ob,"Export Differences…",self._cmp_export,
                  bg="#555",fg="white").pack(side=tk.RIGHT,padx=8)
        tk.Checkbutton(ob, text="Hide matching lines", variable=self.cmp_hide,
                        command=self._cmp_rerender, bg=SURF2,
                        font=("Segoe UI",9)).pack(side=tk.RIGHT,padx=4)

        sty = ttk.Style(); sty.theme_use("default")
        sty.configure("C.TNotebook", background=BG)
        sty.configure("C.TNotebook.Tab", background=GRID, foreground="#444",
                       padding=[10,4], font=("Segoe UI",9))
        sty.map("C.TNotebook.Tab",
                background=[("selected",SURFACE)],
                foreground=[("selected","#000")])
        self.cmp_nb = ttk.Notebook(page, style="C.TNotebook")
        self.cmp_nb.pack(fill=tk.BOTH, expand=True)

        self.cmp_tabs = {}; self.cmp_tidx = {}
        for n in ["Overview","Switches","Inputs/Expo","Mixes",
                  "Logical SW","Special Fn","Screens","All Differences"]:
            f = tk.Frame(self.cmp_nb, bg=BG)
            self.cmp_nb.add(f, text=f"  {n}  ")
            self.cmp_tabs[n] = f
            self.cmp_tidx[n] = len(self.cmp_tidx)

        self.cmp_tw        = {}
        self.cmp_sw_frames = {}
        self._cmp_build_side_tabs()
        self._cmp_build_diff_tab()

    def _cmp_build_side_tabs(self):
        DA_BG="#fff0f0"; DA_FG="#8b0000"; DB_BG="#f0fff4"; DB_FG="#1a5a00"
        SAME_FG="#aaaaaa"; SEC_BG="#f0f0f0"; SEC_FG="#666"
        for name in ["Overview","Switches","Inputs/Expo","Mixes",
                     "Logical SW","Special Fn","Screens"]:
            pw = tk.PanedWindow(self.cmp_tabs[name], orient="horizontal",
                                sashwidth=5, bg=GRID)
            pw.pack(fill=tk.BOTH, expand=True)
            lf = tk.Frame(pw, bg=BG); rf = tk.Frame(pw, bg=BG)
            pw.add(lf, minsize=320, stretch="always")
            pw.add(rf, minsize=320, stretch="always")
            for frm,txt,bg,fg in [(lf,"◀  Model A","#fff3e0","#7a3a00"),
                                   (rf,"Model B  ▶","#e8f5e9","#1a5a20")]:
                h = tk.Frame(frm, bg=bg, height=24)
                h.pack(fill=tk.X); h.pack_propagate(False)
                tk.Label(h, text=txt, font=("Segoe UI",9,"bold"),
                         bg=bg, fg=fg, anchor="w", padx=8).pack(fill=tk.X)
            if name == "Switches":
                self.cmp_sw_frames["a"] = self._cmp_sw_area(lf)
                self.cmp_sw_frames["b"] = self._cmp_sw_area(rf)
            self.cmp_tw[name] = (
                self._cmp_make_text(lf, DA_BG, DA_FG, SAME_FG, SEC_BG, SEC_FG),
                self._cmp_make_text(rf, DB_BG, DB_FG, SAME_FG, SEC_BG, SEC_FG)
            )

    def _cmp_sw_area(self, parent):
        outer = tk.Frame(parent, bg=SURFACE, bd=1, relief="flat",
                         highlightbackground=GRID, highlightthickness=1)
        outer.pack(fill=tk.X, padx=2, pady=(0,2))
        inner = tk.Frame(outer, bg=SURFACE)
        inner.pack(fill=tk.X, padx=6, pady=6)
        return inner

    def _cmp_make_text(self, parent, da_bg, da_fg, same_fg, sec_bg, sec_fg):
        f = tk.Frame(parent, bg=SURFACE)
        f.pack(fill=tk.BOTH, expand=True)
        t = tk.Text(f, wrap="none", font=("Consolas",9), state="disabled",
                    relief="flat", padx=6, pady=3, bg=SURFACE, fg="#333",
                    cursor="arrow", spacing1=1)
        ys = tk.Scrollbar(f, orient="vertical",   command=t.yview)
        xs = tk.Scrollbar(f, orient="horizontal",  command=t.xview)
        t.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        ys.pack(side=tk.RIGHT, fill=tk.Y)
        xs.pack(side=tk.BOTTOM, fill=tk.X)
        t.pack(fill=tk.BOTH, expand=True)
        t.tag_config("da",   background=da_bg, foreground=da_fg,
                     font=("Consolas",9,"bold"))
        t.tag_config("db",   background="#f0fff4", foreground="#1a5a00",
                     font=("Consolas",9,"bold"))
        t.tag_config("same", foreground=same_fg)
        t.tag_config("sec",  background=sec_bg, foreground=sec_fg,
                     font=("Consolas",9,"bold"))
        t.tag_config("ln",   foreground="#cccccc", font=("Consolas",8))
        return t

    def _cmp_build_diff_tab(self):
        f = tk.Frame(self.cmp_tabs["All Differences"], bg=SURFACE)
        f.pack(fill=tk.BOTH, expand=True)
        self.cmp_dt = tk.Text(f, wrap="word", font=("Consolas",9),
                               state="disabled", relief="flat",
                               padx=10, pady=8, bg=SURFACE)
        sb = tk.Scrollbar(f, command=self.cmp_dt.yview)
        self.cmp_dt.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.cmp_dt.pack(fill=tk.BOTH, expand=True)
        self.cmp_dt.tag_config("sec", foreground="#555",
                                font=("Consolas",9,"bold"), background=SURF2)
        self.cmp_dt.tag_config("oa", foreground="#8b0000", background="#fff0f0")
        self.cmp_dt.tag_config("ob", foreground="#1a5a00", background="#f0fff4")

    def _cmp_browse(self, side):
        path = filedialog.askopenfilename(title="Select EdgeTX Model File",
            filetypes=[("YAML","*.yml *.yaml"),("All","*.*")])
        if not path:
            return
        try:
            with open(path,"r",encoding="utf-8") as fh:
                c = norm_yaml(fh.read())
        except Exception as e:
            self.cmp_status.set(f"Error: {e}")
            return
        nm = hdr_val(c,"name")
        if side == "a":
            self.cmp_fa, self.cmp_ca = path, c
            self.cmp_fa_lbl.config(
                text=f"{os.path.basename(path)}  —  {nm}", fg=TEXT)
        else:
            self.cmp_fb, self.cmp_cb = path, c
            self.cmp_fb_lbl.config(
                text=f"{os.path.basename(path)}  —  {nm}", fg=TEXT)

    def _cmp_diff_idx(self, la, lb):
        da, db = set(), set()
        for i in range(max(len(la),len(lb),1)):
            a = la[i] if i < len(la) else ""
            b = lb[i] if i < len(lb) else ""
            if a != b:
                da.add(i); db.add(i)
        return da, db

    def _cmp_write(self, w, lines, diffs, side):
        dtag = "da" if side=="a" else "db"
        hide = self.cmp_hide.get()
        w.config(state="normal"); w.delete("1.0",tk.END)
        for i, line in enumerate(lines):
            is_d = i in diffs
            if hide and not is_d:
                continue
            if line.startswith("── "):
                w.insert(tk.END, line+"\n","sec")
            else:
                w.insert(tk.END, f"{i+1:>3} ","ln")
                w.insert(tk.END, line+"\n", dtag if is_d else "same")
        w.config(state="disabled")

    def _cmp_add_diff(self, section, la, lb, label_a="A", label_b="B"):
        sa, sb = set(la), set(lb)
        oa = [l for l in la if l.strip() and l not in sb]
        ob = [l for l in lb if l.strip() and l not in sa]
        if not oa and not ob:
            return
        self.cmp_dt.config(state="normal")
        d = "─" * max(1, 42 - len(section))
        self.cmp_dt.insert(tk.END, f"\n── {section} {d}\n", "sec")
        max_len = max(len(oa), len(ob))
        for i in range(max_len):
            if i < len(oa):
                self.cmp_dt.insert(tk.END, f"  {label_a}: {oa[i]}\n", "oa")
            if i < len(ob):
                self.cmp_dt.insert(tk.END, f"  {label_b}: {ob[i]}\n", "ob")
            if i < max_len - 1:
                self.cmp_dt.insert(tk.END, "\n")
        self.cmp_dt.config(state="disabled")

    def _cmp_set_tab(self, name, count):
        cnt = f" ({count})" if count else ""
        self.cmp_nb.tab(self.cmp_tidx[name], text=f"  {name}{cnt}  ")

    def _cmp_p_overview(self,c):
        lines = ["── GENERAL ─────────────────────────────────"]
        lines += [f"Name           : {hdr_val(c,'name')}",
                  f"Bitmap         : {hdr_val(c,'bitmap')}"]
        sv = re.search(r'^semver:\s*(.+)',c,re.M)
        lines.append(f"EdgeTX         : {sv.group(1).strip() if sv else 'N/A'}")
        lines.append("── TIMER ────────────────────────────────────")
        tb = re.search(r'timers:\n\s+0:\n(.*?)(?=\n\w|\Z)',c,re.S)
        if tb:
            blk = tb.group(0)
            for k,lbl in [("swtch","Timer Switch  "),
                           ("mode","Timer Mode    "),
                           ("start","Timer Start   ")]:
                m = re.search(rf'{k}:\s*"?([^"\n]+)"?',blk)
                v = m.group(1).strip() if m else "N/A"
                if k=="start":
                    try: s=int(v); v=f"{s//60}m {s%60}s"
                    except Exception: pass
                lines.append(f"{lbl} : {v}")
        lines.append("── MODULE ───────────────────────────────────")
        am = re.search(r'crsf_arming_mode:\s*(.+)',c)
        lines.append(f"CRSF Arming    : {am.group(1).strip() if am else 'N/A'}")
        return lines

    def _cmp_sw_refs(self,c):
        result = {}
        for letter in "ABCDEFGH":
            sw = f"S{letter}"; secs = []
            for sn,pat in [("timers",rf'swtch:\s*"?{sw}\b'),
                            ("expoData",rf'swtch:\s*"?{sw}\b'),
                            ("mixData",rf'swtch:\s*"?{sw}\b'),
                            ("logicalSw",rf'def:\s*"?[^"\n]*{sw}\b'),
                            ("customFn",rf'swtch:\s*"?{sw}\b')]:
                if re.search(pat,c): secs.append(sn)
            positions = sorted(set(re.findall(rf'S{letter}([012])\b',c)))
            if secs or positions:
                result[sw] = {"secs":list(dict.fromkeys(secs)),"pos":positions}
        return result

    def _cmp_p_switch_lines(self,c):
        lines = ["── SWITCHES ─────────────────────────────────"]
        refs  = self._cmp_sw_refs(c)
        for sw,info in refs.items():
            pn  = len(info["pos"]) if info["pos"] else "?"
            ps  = "/".join(info["pos"]) if info["pos"] else "—"
            sec = " · ".join(info["secs"]) if info["secs"] else "unreferenced"
            lines.append(f"{sw}  ({pn}pos: {ps})   {sec}")
        return lines if len(lines)>1 else lines+["(none found)"]

    def _cmp_parse_list(self, c, section):
        """Parse a YAML list section that uses bare ' -' entries (EdgeTX 2.10+)
        or inline '- key: val' entries (older format). Returns list of dicts."""
        sec = re.search(rf'^{section}:\s*\n(.*?)(?=^\w|\Z)', c, re.M|re.S)
        if not sec:
            return []
        items = []
        cur = None
        for line in sec.group(0).splitlines():
            s = line.strip()
            if not s or s == f'{section}:':
                continue
            # Bare list marker (new format)
            if s == '-':
                if cur is not None:
                    items.append(cur)
                cur = {}
            # Inline list marker e.g. "- destCh: 3" (old format)
            elif s.startswith('- ') and ':' in s:
                if cur is not None:
                    items.append(cur)
                cur = {}
                kv = s[2:]
                k, v = kv.split(':', 1)
                cur[k.strip()] = v.strip().strip('"'). strip()
            # Property line
            elif cur is not None and ':' in s and not s.startswith('#'):
                k, v = s.split(':', 1)
                cur[k.strip()] = v.strip().strip('"')
        if cur is not None:
            items.append(cur)
        return items

    def _cmp_parse_numbered(self, c, section):
        """Parse a YAML numbered-dict section (e.g. logicalSw, customFn).
        Returns dict of {index: properties_dict}."""
        sec = re.search(rf'^{section}:\s*\n(.*?)(?=^\w|\Z)', c, re.M|re.S)
        if not sec:
            return {}
        items = {}
        cur_idx = None
        cur = {}
        for line in sec.group(0).splitlines():
            m = re.match(r'^\s{1,6}(\d+):\s*$', line)
            if m:
                if cur_idx is not None:
                    items[cur_idx] = cur
                cur_idx = int(m.group(1))
                cur = {}
            elif cur_idx is not None and ':' in line.strip():
                s = line.strip()
                k, v = s.split(':', 1)
                cur[k.strip()] = v.strip().strip('"')
        if cur_idx is not None:
            items[cur_idx] = cur
        return items

    def _cmp_p_mixes(self,c):
        lines = ["── MIX CHANNELS ─────────────────────────────"]
        mixes = self._cmp_parse_list(c, 'mixData')
        # Group by destCh so multiple lines per channel stay together
        from collections import defaultdict
        by_ch = defaultdict(list)
        for m in mixes:
            ch = int(m.get('destCh', -1)) + 1
            by_ch[ch].append(m)
        for ch in sorted(by_ch.keys()):
            for i, m in enumerate(by_ch[ch]):
                sw   = m.get('swtch','NONE')
                name = m.get('name','')
                mode = m.get('mltpx','')
                dly  = m.get('delayUp','0')
                spd  = m.get('speedUp','0')
                extras = []
                if mode and mode not in ('ADD',''): extras.append(f'mode={mode}')
                if dly and dly != '0': extras.append(f'dlyUp={dly}')
                if spd and spd != '0': extras.append(f'spdUp={spd}')
                extra_str = '  '.join(extras)
                prefix = f'CH{ch}' if i == 0 else f'  +'
                lines.append(
                    f"{prefix:<5} src={m.get('srcRaw','?'):<10}"
                    f"wt={m.get('weight','?'):<7}"
                    f"sw={sw:<12}"
                    f"{name:<12}{extra_str}")
        return lines if len(lines)>1 else lines+["(none)"]

    def _cmp_p_expo(self,c):
        lines = ["── EXPO / INPUTS ─────────────────────────────"]
        items = self._cmp_parse_list(c, 'expoData')
        for m in items:
            ch   = int(m.get('chn', -1)) + 1
            src  = m.get('srcRaw','?')
            sw   = m.get('swtch','NONE')
            wt   = m.get('weight','?')
            name = m.get('name','')
            lines.append(
                f"CH{ch:<4} src={src:<10} sw={sw:<12} wt={wt:<6} {name}")
        return lines if len(lines)>1 else lines+["(none)"]

    def _cmp_p_logical(self,c):
        lines = ["── LOGICAL SWITCHES ──────────────────────────"]
        items = self._cmp_parse_numbered(c, 'logicalSw')
        for idx in sorted(items.keys()):
            m  = items[idx]
            fn = m.get('func','?')
            df = m.get('def','?')
            sw = m.get('andsw','---')
            v1 = m.get('v1','')
            v2 = m.get('v2','')
            lines.append(
                f"L{idx+1:<3} {fn:<20} def={df:<20} v1={v1:<8} v2={v2:<8} and={sw}")
        return lines if len(lines)>1 else lines+["(none)"]

    def _cmp_p_special(self,c):
        lines = ["── SPECIAL FUNCTIONS ─────────────────────────"]
        items = self._cmp_parse_numbered(c, 'customFn')
        for idx in sorted(items.keys()):
            m     = items[idx]
            sw    = m.get('swtch','?')
            fn    = m.get('func','?')
            df    = m.get('def','').replace('\\x00','').strip().strip('"').strip(',')
            rep   = m.get('repeatMode','')
            lines.append(f"{idx:<4} sw={sw:<14} {fn:<22} {df}")
        return lines if len(lines)>1 else lines+["(none)"]

    def _cmp_p_screens(self,c):
        lines = ["── SCREENS ───────────────────────────────────"]
        sec   = re.search(r'^screenData:[ \t]*\n(.*?)(?=^\w|\Z)',c,re.M|re.S)
        if not sec: return lines+["(none)"]
        # Detect indent level
        indent_m = re.search(r'^( +)\d+:\s*$', sec.group(0), re.M)
        if not indent_m: return lines+["(none)"]
        ind = len(indent_m.group(1))
        pad1  = " " * ind
        pad2  = " " * (ind + 1)
        for m in re.finditer(
                r'^' + pad1 + r'(\d+):\s*$\n' + pad2 + r'LayoutId:\s*(\S+)',
                sec.group(0), re.M):
            chunk = sec.group(0)[m.start():]
            nx = re.search(r'^' + pad1 + r'\d+:\s*$', chunk[1:], re.M)
            if nx: chunk = chunk[:nx.start()+1]
            wids = re.findall(r'widgetName:\s*(\S+)', chunk)
            lines.append(
                f"Screen {m.group(1)}: {m.group(2):<24} [{', '.join(wids)}]")
        return lines if len(lines)>1 else lines+["(none)"]

    def _cmp_compare(self):
        if not self.cmp_ca or not self.cmp_cb:
            self.cmp_status.set("Please load both model files first.")
            return
        a, b = self.cmp_ca, self.cmp_cb
        self.cmp_dt.config(state="normal")
        self.cmp_dt.delete("1.0",tk.END)
        self.cmp_dt.config(state="disabled")
        na = hdr_val(a,"name"); nb = hdr_val(b,"name")
        # Shorten names for labels — use up to 20 chars
        la_name = na[:20] if na else "A"
        lb_name = nb[:20] if nb else "B"
        total = 0
        for name, parser in [
                ("Overview",   self._cmp_p_overview),
                ("Inputs/Expo",self._cmp_p_expo),
                ("Mixes",      self._cmp_p_mixes),
                ("Logical SW", self._cmp_p_logical),
                ("Special Fn", self._cmp_p_special),
                ("Screens",    self._cmp_p_screens)]:
            la,lb = parser(a),parser(b)
            da,db = self._cmp_diff_idx(la,lb)
            ta,tb = self.cmp_tw[name]
            self._cmp_write(ta,la,da,"a")
            self._cmp_write(tb,lb,db,"b")
            self._cmp_add_diff(name,la,lb,la_name,lb_name)
            cnt = len(da); total += cnt
            self._cmp_set_tab(name,cnt)
        sla,slb = self._cmp_p_switch_lines(a),self._cmp_p_switch_lines(b)
        da,db   = self._cmp_diff_idx(sla,slb)
        ta,tb   = self.cmp_tw["Switches"]
        self._cmp_write(ta,sla,da,"a")
        self._cmp_write(tb,slb,db,"b")
        self._cmp_add_diff("Switches",sla,slb,la_name,lb_name)
        sw_cnt = len(da); total += sw_cnt
        self._cmp_set_tab("Switches",sw_cnt)
        refs_a = self._cmp_sw_refs(a); refs_b = self._cmp_sw_refs(b)
        sw_a   = self._cmp_build_sw_info(refs_a,sla,da,"a")
        sw_b   = self._cmp_build_sw_info(refs_b,slb,db,"b")
        self._cmp_draw_pills(self.cmp_sw_frames["a"],sw_a,"a")
        self._cmp_draw_pills(self.cmp_sw_frames["b"],sw_b,"b")
        self._cmp_set_tab("All Differences",total)
        self.cmp_status.set(
            f"✔  {na}  vs  {nb}   —   {total} difference(s) found")
        self.cmp_exp_txt = self.cmp_dt.get("1.0",tk.END)

    def _cmp_build_sw_info(self,refs,lines,diffs,side):
        out = {}
        for i,line in enumerate(lines):
            if line.startswith("──"): continue
            m = re.match(r'(S[A-H])',line)
            if not m: continue
            sw     = m.group(1); letter = sw[1]; is_d = i in diffs
            info   = refs.get(sw,{})
            pos_n  = len(info.get("pos",[]) or [1])
            secs   = info.get("secs",[])
            col    = self.SW_PILL_COL.get(letter,"#888")
            out[sw] = {"col":col,"diff":is_d,"pos_n":pos_n,
                       "use":" · ".join(secs[:2])}
        return out

    def _cmp_draw_pills(self,frame,sw_info,side):
        for w in frame.winfo_children(): w.destroy()
        if not sw_info:
            tk.Label(frame, text="(no switches found)", fg=MUTED,
                     font=("Segoe UI",9), bg=SURFACE).pack(anchor="w")
            return
        items = list(sw_info.items())
        for rs in range(0,len(items),4):
            rf = tk.Frame(frame, bg=SURFACE)
            rf.pack(fill=tk.X, pady=2)
            for sw,info in items[rs:rs+4]:
                col = info["col"]; is_d = info["diff"]
                pf2 = tk.Frame(rf, bg=SURFACE, padx=4, pady=2)
                pf2.pack(side=tk.LEFT, padx=3)
                tk.Label(pf2, text=sw, font=("Segoe UI",9,"bold"),
                         bg=col, fg="white", padx=6, pady=2).pack(side=tk.LEFT)
                df2 = tk.Frame(pf2, bg=SURFACE); df2.pack(side=tk.LEFT, padx=3)
                for i in range(max(info["pos_n"],1)):
                    c2 = tk.Canvas(df2, width=9, height=9, bg=SURFACE,
                                   highlightthickness=0)
                    c2.pack(side=tk.LEFT, padx=1)
                    c2.create_oval(1,1,8,8,
                        fill=col if i==0 else "#ddd",
                        outline=col if i==0 else "#bbb", width=1)
                use = info["use"][:18] if info["use"] else ""
                tk.Label(pf2, text=use, font=("Segoe UI",8),
                         bg=SURFACE, fg=MUTED).pack(side=tk.LEFT, padx=2)
                bbg = ("#8b0000" if side=="a" else "#1a5a00") if is_d else "#e8f5e9"
                bfg = "white" if is_d else "#2a7a40"
                tk.Label(pf2, text="DIFF" if is_d else "SAME",
                         font=("Segoe UI",7,"bold"), bg=bbg, fg=bfg,
                         padx=4, pady=1).pack(side=tk.LEFT, padx=3)

    def _cmp_rerender(self):
        if self.cmp_ca and self.cmp_cb:
            self._cmp_compare()

    def _cmp_export(self):
        if not self.cmp_exp_txt.strip():
            self.cmp_status.set("Nothing to export — run Compare first.")
            return
        path = filedialog.asksaveasfilename(defaultextension=".txt",
            filetypes=[("Text","*.txt"),("All","*.*")])
        if not path: return
        with open(path,"w",encoding="utf-8") as fh:
            fh.write(self.cmp_exp_txt)
        self.cmp_status.set(f"Exported → {os.path.basename(path)}")

    # =========================================================================
    #  SWITCH REMAP  (single + batch toggle)
    # =========================================================================

    def _build_switch_remap(self):
        page = self._register_page("SwitchRemap")

        top = tk.Frame(page, bg=SURFACE, pady=8, padx=12)
        top.pack(fill=tk.X)
        tk.Label(top, text="Switch Remap", bg=SURFACE, fg="#c2410c",
                 font=("Segoe UI", 12, "bold")).pack(side=tk.LEFT)
        self.sw_batch_var = tk.BooleanVar(value=False)
        self.sw_toggle_btn = tk.Button(
            top, text="  ○  Batch Mode  OFF  ",
            font=("Segoe UI", 9, "bold"), relief="flat", padx=10, pady=5,
            bg="#ffedd5", fg="#c2410c", cursor="hand2", bd=1,
            activebackground="#fed7aa", command=self._sw_toggle_mode)
        self.sw_toggle_btn.pack(side=tk.RIGHT, padx=12)
        tk.Frame(page, bg=GRID, height=1).pack(fill=tk.X)

        # ── Saved Rulesets panel ─────────────────────────────────────────
        rs_frame = tk.Frame(page, bg=SURFACE, pady=6, padx=12)
        rs_frame.pack(fill=tk.X)
        tk.Frame(rs_frame, bg=GRID, height=1).pack(fill=tk.X, pady=(0,6))

        rs_top = tk.Frame(rs_frame, bg=SURFACE)
        rs_top.pack(fill=tk.X)
        tk.Label(rs_top, text="Saved Rulesets:", bg=SURFACE, fg=MUTED,
                 font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT)

        self.sw_ruleset_var = tk.StringVar()
        self.sw_ruleset_combo = ttk.Combobox(rs_top, textvariable=self.sw_ruleset_var,
            state="readonly", width=20, font=("Segoe UI", 9))
        self.sw_ruleset_combo.pack(side=tk.LEFT, padx=6)
        self._refresh_sw_rulesets()

        self._btn(rs_top, "Load",   self._sw_load_ruleset).pack(side=tk.LEFT, padx=2)
        self._btn(rs_top, "Save as…", self._sw_save_ruleset).pack(side=tk.LEFT, padx=2)
        self._btn(rs_top, "Delete",   self._sw_delete_ruleset).pack(side=tk.LEFT, padx=2)

        self.sw_rs_status = tk.Label(rs_frame, text="", bg=SURFACE, fg=ACCENT,
            font=("Segoe UI", 8, "italic"), anchor="w")
        self.sw_rs_status.pack(fill=tk.X, pady=(2,0))

        tk.Frame(rs_frame, bg=GRID, height=1).pack(fill=tk.X, pady=(6,0))

        self.sw_single_frame = tk.Frame(page, bg=BG)
        self.sw_single_frame.pack(fill=tk.BOTH, expand=True)
        self._sw_build_single(self.sw_single_frame)

        self.sw_batch_frame = tk.Frame(page, bg=BG)
        self._sw_build_batch(self.sw_batch_frame)

    def _sw_toggle_mode(self):
        self.sw_batch_var.set(not self.sw_batch_var.get())
        if self.sw_batch_var.get():
            self.sw_toggle_btn.config(
                text="  ●  Batch Mode  ON  ",
                bg="#16a34a", fg="white", activebackground="#15803d")
            self.sw_single_frame.pack_forget()
            self.sw_batch_frame.pack(fill=tk.BOTH, expand=True)
        else:
            self.sw_toggle_btn.config(
                text="  ○  Batch Mode  OFF  ",
                bg="#ffedd5", fg="#c2410c", activebackground="#fed7aa")
            self.sw_batch_frame.pack_forget()
            self.sw_single_frame.pack(fill=tk.BOTH, expand=True)

    def _sw_build_single(self, parent):
        file_card = self._card(parent,"Model File (.yml)",pady=12, hdr_bg="#dbeafe", hdr_fg="#1e40af")
        self.sw_s_input_file = None
        self.sw_s_file_lbl = tk.Label(file_card, text="No file selected",
            fg=MUTED, bg=SURFACE, font=("Segoe UI",9), anchor="w")
        self.sw_s_file_lbl.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._btn(file_card,"Browse…",self._sw_s_browse).pack(side=tk.RIGHT)

        rules_card = self._card(parent,"Switch Remapping Rules", hdr_bg="#dbeafe", hdr_fg="#1e40af")
        self.sw_s_rows = self.RulesList()
        self._sw_rules_panel(rules_card, self.sw_s_rows)

        btn_row = tk.Frame(parent, bg=BG)
        btn_row.pack(pady=10)
        self._blue_btn(btn_row,"Remap & Save File",self._sw_s_process).pack()
        self.sw_s_status = tk.StringVar(value="Ready.")
        tk.Label(parent,textvariable=self.sw_s_status,
                 font=("Segoe UI",9),fg=MUTED,bg=BG).pack()

    def _sw_build_batch(self, parent):
        in_card = self._card(parent,"Input Folder (models to convert)",pady=12, hdr_bg="#dbeafe", hdr_fg="#1e40af")
        self.sw_b_input_folder = None
        self.sw_b_in_lbl = tk.Label(in_card,text="No folder selected",
            fg=MUTED,bg=SURFACE,font=("Segoe UI",9),anchor="w")
        self.sw_b_in_lbl.pack(side=tk.LEFT,fill=tk.X,expand=True)
        self._btn(in_card,"Browse…",self._sw_b_browse_in).pack(side=tk.RIGHT)

        out_card = self._card(parent,"Output Folder (where to save converted files)", hdr_bg="#dbeafe", hdr_fg="#1e40af")
        self.sw_b_output_folder = None
        self.sw_b_out_lbl = tk.Label(out_card,text="No folder selected",
            fg=MUTED,bg=SURFACE,font=("Segoe UI",9),anchor="w")
        self.sw_b_out_lbl.pack(side=tk.LEFT,fill=tk.X,expand=True)
        self._btn(out_card,"Browse…",self._sw_b_browse_out).pack(side=tk.RIGHT)

        rules_card = self._card(parent,"Switch Remapping Rules", hdr_bg="#dbeafe", hdr_fg="#1e40af")
        self.sw_b_rows = self.RulesList()
        self._sw_rules_panel(rules_card, self.sw_b_rows)

        btn_row = tk.Frame(parent,bg=BG)
        btn_row.pack(pady=10)
        self._blue_btn(btn_row,"Remap All Files",self._sw_b_process).pack()
        self.sw_b_status = tk.StringVar(value="Ready.")
        tk.Label(parent,textvariable=self.sw_b_status,
                 font=("Segoe UI",9),fg=MUTED,bg=BG).pack()

    def _sw_rules_panel(self, parent, rows_list):
        hdr = tk.Frame(parent,bg=SURFACE)
        hdr.pack(fill=tk.X,pady=(0,4))
        tk.Label(hdr,text="From",font=("Segoe UI",9,"bold"),
                 width=10,bg=SURFACE).pack(side=tk.LEFT)
        tk.Label(hdr,text="→",font=("Segoe UI",11),
                 bg=SURFACE).pack(side=tk.LEFT,padx=8)
        tk.Label(hdr,text="To",font=("Segoe UI",9,"bold"),
                 width=10,bg=SURFACE).pack(side=tk.LEFT)

        rows_f = tk.Frame(parent,bg=SURFACE)
        rows_f.pack(fill=tk.X)

        def add_row():
            rf = tk.Frame(rows_f,bg=SURFACE); rf.pack(fill=tk.X,pady=2)
            fv = tk.StringVar(value=SWITCHES[0])
            tv = tk.StringVar(value=SWITCHES[1])
            ttk.Combobox(rf,textvariable=fv,values=SWITCHES,
                         width=10,state="readonly").pack(side=tk.LEFT)
            tk.Label(rf,text="→",font=("Segoe UI",11),
                     bg=SURFACE).pack(side=tk.LEFT,padx=8)
            ttk.Combobox(rf,textvariable=tv,values=SWITCHES,
                         width=10,state="readonly").pack(side=tk.LEFT)
            rows_list.append((fv,tv,rf))

        def remove_row():
            if rows_list:
                _,_,rf = rows_list.pop(); rf.destroy()

        def clear_rows():
            for _,_,rf in rows_list: rf.destroy()
            rows_list.clear()

        for _ in range(3): add_row()

        btn_row = tk.Frame(parent,bg=SURFACE)
        btn_row.pack(fill=tk.X,pady=(6,0))
        self._btn(btn_row,"+ Add Rule",  add_row).pack(side=tk.LEFT)
        self._btn(btn_row,"- Remove Last",remove_row).pack(side=tk.LEFT,padx=4)
        self._btn(btn_row,"Clear All",   clear_rows).pack(side=tk.LEFT)

        # Notes box
        tk.Label(parent, text="Notes (optional):", bg=SURFACE, fg=MUTED,
                 font=("Segoe UI", 8, "bold"), anchor="w").pack(fill=tk.X, pady=(8,2))
        notes_txt = tk.Text(parent, height=3, font=("Segoe UI", 9),
                            relief="flat", bg=SURF2, fg=TEXT,
                            highlightthickness=1, highlightbackground=GRID,
                            wrap="word")
        notes_txt.pack(fill=tk.X)
        rows_list._notes_widget = notes_txt  # attach to list so we can read it later

    def _refresh_sw_rulesets(self):
        names = list(self.settings.get("sw_rulesets", {}).keys())
        self.sw_ruleset_combo["values"] = names
        if self.sw_ruleset_var.get() not in names:
            self.sw_ruleset_var.set(names[0] if names else "")

    def _sw_get_active_rows(self):
        """Return the active rows list (single or batch mode)."""
        return self.sw_b_rows if self.sw_batch_var.get() else self.sw_s_rows

    def _sw_get_notes(self):
        rows = self._sw_get_active_rows()
        nw = getattr(rows, "_notes_widget", None)
        return nw.get("1.0", tk.END).strip() if nw else ""

    def _sw_set_notes(self, text):
        rows = self._sw_get_active_rows()
        nw = getattr(rows, "_notes_widget", None)
        if nw:
            nw.delete("1.0", tk.END)
            nw.insert("1.0", text)

    def _sw_save_ruleset(self):
        rows = self._sw_get_active_rows()
        if not rows:
            messagebox.showwarning("No Rules", "Add at least one rule first.")
            return
        name = simpledialog.askstring("Save Ruleset",
            "Ruleset name (e.g. 'TX15 to TX16'):",
            initialvalue=self.sw_ruleset_var.get() or "", parent=self)
        if not name or not name.strip():
            return
        name = name.strip()
        if name in self.settings.get("sw_rulesets", {}):
            if not messagebox.askyesno("Overwrite?", f"Overwrite '{name}'?"):
                return
        rules = [[fv.get(), tv.get()] for fv, tv, _ in rows]
        notes = self._sw_get_notes()
        if "sw_rulesets" not in self.settings:
            self.settings["sw_rulesets"] = {}
        self.settings["sw_rulesets"][name] = {"rules": rules, "notes": notes}
        save_app_settings(self.settings)
        self._refresh_sw_rulesets()
        self.sw_ruleset_var.set(name)
        self.sw_rs_status.config(
            text=f"Saved: {name}  ({len(rules)} rule(s))")

    def _sw_load_ruleset(self):
        name = self.sw_ruleset_var.get()
        if not name or name not in self.settings.get("sw_rulesets", {}):
            messagebox.showinfo("Load Ruleset", "Select a ruleset first.")
            return
        data  = self.settings["sw_rulesets"][name]
        rules = data.get("rules", [])
        notes = data.get("notes", "")
        rows  = self._sw_get_active_rows()

        # Clear existing rows
        for _, _, rf in rows:
            rf.destroy()
        rows.clear()

        # We need to add rows via the rules panel's add_row — but it's a closure.
        # Instead manually recreate the row widgets into the parent frame.
        # Find the rows frame (parent of the row widgets)
        # Get parent from existing notes widget or rebuild
        nw = getattr(rows, "_notes_widget", None)
        if nw:
            rows_frame = nw.master.master  # Text -> parent frame -> rules card inner
            # Actually just find rows_frame by looking at the card
            # Simpler: re-use the stored frame reference if available
            pass

        # Cleanest approach: call _sw_rules_panel equivalent inline
        # Find the rules_card inner frame from the notes widget ancestry
        if nw:
            # nw is packed in the same parent as rows_frame
            parent_frame = nw.master
            # The rows_f frame is a sibling — find it
            for child in parent_frame.winfo_children():
                if isinstance(child, tk.Frame) and child != nw.master:
                    rows_f = child
                    break
            else:
                rows_f = None

            if rows_f:
                for f, t in rules:
                    rf = tk.Frame(rows_f, bg=SURFACE)
                    rf.pack(fill=tk.X, pady=2)
                    fv = tk.StringVar(value=f)
                    tv = tk.StringVar(value=t)
                    ttk.Combobox(rf, textvariable=fv, values=SWITCHES,
                                 width=10, state="readonly").pack(side=tk.LEFT)
                    tk.Label(rf, text="→", font=("Segoe UI", 11),
                             bg=SURFACE).pack(side=tk.LEFT, padx=8)
                    ttk.Combobox(rf, textvariable=tv, values=SWITCHES,
                                 width=10, state="readonly").pack(side=tk.LEFT)
                    rows.append((fv, tv, rf))

        self._sw_set_notes(notes)
        self.sw_rs_status.config(
            text=f"Loaded: {name}  ({len(rules)} rule(s))"
            + (f"  —  {notes[:40]}" if notes else ""))

    def _sw_delete_ruleset(self):
        name = self.sw_ruleset_var.get()
        if not name or name not in self.settings.get("sw_rulesets", {}):
            messagebox.showinfo("Delete", "Select a ruleset first.")
            return
        if not messagebox.askyesno("Delete Ruleset", f"Delete '{name}'?"):
            return
        del self.settings["sw_rulesets"][name]
        save_app_settings(self.settings)
        self._refresh_sw_rulesets()
        self.sw_rs_status.config(text=f"Deleted: {name}")

    def _sw_s_browse(self):
        path = filedialog.askopenfilename(title="Select Model File",
            filetypes=[("YAML","*.yml *.yaml"),("All","*.*")])
        if path:
            self.sw_s_input_file = path
            self.sw_s_file_lbl.config(text=os.path.basename(path),fg=TEXT)

    def _sw_b_browse_in(self):
        path = filedialog.askdirectory(title="Select Input Folder")
        if path:
            self.sw_b_input_folder = path
            self.sw_b_in_lbl.config(text=path,fg=TEXT)

    def _sw_b_browse_out(self):
        path = filedialog.askdirectory(title="Select Output Folder")
        if path:
            self.sw_b_output_folder = path
            self.sw_b_out_lbl.config(text=path,fg=TEXT)

    def _sw_remap_content(self, content, rules):
        placeholders = {t: f"__SWREMAP_{t}__" for _,t in rules}
        result = content
        for f,t in rules:
            result = re.sub(
                rf'\b{re.escape(f)}(?=[012]|(?![A-Z0-9]))',
                placeholders[t], result)
        for t,ph in placeholders.items():
            result = result.replace(ph,t)
        return result

    def _sw_build_rules(self, rows_list):
        rules = []; seen = set()
        for fv,tv,_ in rows_list:
            f,t = fv.get(),tv.get()
            if f == t:
                messagebox.showerror("Invalid Rule",
                    f"Cannot remap {f} to itself.")
                return None
            if f in seen:
                messagebox.showerror("Duplicate",
                    f"Switch {f} appears twice.")
                return None
            seen.add(f); rules.append((f,t))
        if not rules:
            messagebox.showerror("No Rules","Add at least one rule.")
            return None
        return rules

    def _sw_s_process(self):
        if not self.sw_s_input_file:
            messagebox.showerror("No File","Select a model file first.")
            return
        rules = self._sw_build_rules(self.sw_s_rows)
        if not rules: return
        try:
            with open(self.sw_s_input_file,"r",encoding="utf-8") as fh:
                content = fh.read()
        except Exception as e:
            messagebox.showerror("Read Error",str(e)); return
        new_content = self._sw_remap_content(content,rules)
        base,ext = os.path.splitext(self.sw_s_input_file)
        save_path = filedialog.asksaveasfilename(
            title="Save Remapped File",
            initialfile=os.path.basename(base)+"_remapped"+ext,
            defaultextension=ext,
            filetypes=[("YAML","*.yml *.yaml"),("All","*.*")])
        if not save_path: return
        try:
            with open(save_path,"w",encoding="utf-8") as fh:
                fh.write(new_content)
        except Exception as e:
            messagebox.showerror("Write Error",str(e)); return
        self.sw_s_status.set(f"Saved: {os.path.basename(save_path)}")
        messagebox.showinfo("Done",f"Saved to:\n{save_path}")

    def _sw_b_process(self):
        if not self.sw_b_input_folder:
            messagebox.showerror("No Input","Select input folder."); return
        if not self.sw_b_output_folder:
            messagebox.showerror("No Output","Select output folder."); return
        rules = self._sw_build_rules(self.sw_b_rows)
        if not rules: return
        ymls = [f for f in os.listdir(self.sw_b_input_folder)
                if f.lower().endswith(('.yml','.yaml'))]
        if not ymls:
            messagebox.showerror("No Files","No .yml files found."); return
        success = errors = 0
        for fn in ymls:
            ip = os.path.join(self.sw_b_input_folder,fn)
            op = os.path.join(self.sw_b_output_folder,fn)
            try:
                with open(ip,"r",encoding="utf-8") as fh: c = fh.read()
                with open(op,"w",encoding="utf-8") as fh:
                    fh.write(self._sw_remap_content(c,rules))
                success += 1
            except Exception: errors += 1
        msg = f"Done! {success} file(s) converted."
        if errors: msg += f" {errors} error(s)."
        self.sw_b_status.set(msg)
        messagebox.showinfo("Complete",
            f"{msg}\n\nSaved to:\n{self.sw_b_output_folder}")

    # =========================================================================
    #  SCREEN REORDER
    # =========================================================================

    def _build_screen_reorder(self):
        page = self._register_page("ScreenReorder")
        self.sr_input_file   = None
        self.sr_screens      = []
        self.sr_file_content = ""

        hdr = tk.Frame(page, bg=SURFACE, pady=8, padx=12)
        hdr.pack(fill=tk.X)
        tk.Label(hdr, text="Screen Reorder", bg=SURFACE, fg="#b45309",
                 font=("Segoe UI", 12, "bold")).pack(side=tk.LEFT)
        tk.Frame(page, bg=GRID, height=1).pack(fill=tk.X)

        content = tk.Frame(page, bg=BG)
        content.pack(fill=tk.BOTH, expand=True, padx=16, pady=12)

        file_card = self._card(content,"Model File (.yml)",pady=0, hdr_bg="#dbeafe", hdr_fg="#1e40af")
        self.sr_file_lbl = tk.Label(file_card,text="No file selected",
            fg=MUTED,bg=SURFACE,font=("Segoe UI",9),anchor="w")
        self.sr_file_lbl.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._btn(file_card,"Browse…",self._sr_browse).pack(side=tk.RIGHT)

        list_outer = tk.Frame(content, bg=SURFACE, bd=1, relief="flat",
                              highlightbackground="#93c5fd", highlightthickness=1)
        list_outer.pack(fill=tk.BOTH, expand=True, pady=8)
        lhdr = tk.Frame(list_outer, bg="#dbeafe")
        lhdr.pack(fill=tk.X)
        tk.Label(lhdr,
            text="Screen Order  — select a screen and use buttons to reorder",
            bg="#dbeafe", fg="#1e40af", font=("Segoe UI",9,"bold"),
            anchor="w", padx=10, pady=4).pack(fill=tk.X)

        list_inner = tk.Frame(list_outer, bg=SURFACE)
        list_inner.pack(fill=tk.BOTH, expand=True, padx=8, pady=6)
        sb = tk.Scrollbar(list_inner, orient="vertical")
        self.sr_listbox = tk.Listbox(list_inner, yscrollcommand=sb.set,
            font=("Segoe UI",10), selectmode="single",
            activestyle="dotbox", height=8, bg=SURFACE, relief="flat")
        sb.config(command=self.sr_listbox.yview)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.sr_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        btn_col = tk.Frame(list_outer, bg=SURFACE)
        btn_col.pack(side=tk.RIGHT, padx=8, pady=8)
        self._btn(btn_col,"▲ Up",  self._sr_move_up,  width=10).pack(pady=3)
        self._btn(btn_col,"▼ Down",self._sr_move_down,width=10).pack(pady=3)

        btn_row = tk.Frame(content, bg=BG)
        btn_row.pack(pady=6)
        self._blue_btn(btn_row,"Save Reordered File",self._sr_process).pack()

        self.sr_status = tk.StringVar(value="Load a model file to begin.")
        tk.Label(content,textvariable=self.sr_status,
                 font=("Segoe UI",9),fg=MUTED,bg=BG).pack()

    def _sr_browse(self):
        path = filedialog.askopenfilename(title="Select Model File",
            filetypes=[("YAML","*.yml *.yaml"),("All","*.*")])
        if not path: return
        try:
            with open(path,"r",encoding="utf-8") as fh: c = fh.read()
        except Exception as e:
            messagebox.showerror("Read Error",str(e)); return
        screens = self._sr_parse(c)
        if not screens:
            messagebox.showerror("Parse Error",
                "No screenData entries found."); return
        self.sr_input_file   = path
        self.sr_file_content = c
        self.sr_screens      = screens
        self.sr_file_lbl.config(text=os.path.basename(path),fg=TEXT)
        self._sr_refresh()
        self.sr_status.set(
            f"Loaded {len(screens)} screen(s) from {os.path.basename(path)}")

    def _sr_parse(self,content):
        match = re.search(
            r'^screenData:[ \t]*\n(.*?)(?=^\w|\Z)',content,re.M|re.S)
        if not match: return []
        sec = match.group(0)
        # Detect indent by finding first numbered entry e.g. "   0:"
        indent_m = re.search(r'^( +)\d+:\s*$', sec, re.M)
        if not indent_m: return []
        indent = len(indent_m.group(1))
        self.sr_indent = " " * indent  # store for use in save
        # Split on top-level numbered entries at that exact indent
        pad   = " " * indent
        parts = re.split(r'(?=^' + pad + r'\d+:\s*$)', sec, flags=re.M)
        screens = []
        for part in parts:
            part = part.strip()
            if not part or part.startswith("screenData"): continue
            im = re.match(r'(\d+):',part)
            lm = re.search(r'LayoutId:\s*(\S+)',part)
            # Collect all widget names in this screen block
            wids = re.findall(r'widgetName:\s*(\S+)',part)
            idx    = im.group(1) if im else "?"
            layout = lm.group(1) if lm else "Unknown"
            label  = f"Screen {idx}: {layout}"
            if wids: label += f"  [{wids[0]}{',...' if len(wids)>1 else ''}]"
            screens.append((label, part))
        return screens

    def _sr_refresh(self):
        self.sr_listbox.delete(0,tk.END)
        for label,_ in self.sr_screens:
            self.sr_listbox.insert(tk.END,label)

    def _sr_move_up(self):
        sel = self.sr_listbox.curselection()
        if not sel or sel[0]==0: return
        i = sel[0]
        self.sr_screens[i-1],self.sr_screens[i] = \
            self.sr_screens[i],self.sr_screens[i-1]
        self._sr_refresh()
        self.sr_listbox.selection_set(i-1)

    def _sr_move_down(self):
        sel = self.sr_listbox.curselection()
        if not sel or sel[0]==len(self.sr_screens)-1: return
        i = sel[0]
        self.sr_screens[i],self.sr_screens[i+1] = \
            self.sr_screens[i+1],self.sr_screens[i]
        self._sr_refresh()
        self.sr_listbox.selection_set(i+1)

    def _sr_process(self):
        if not self.sr_input_file:
            messagebox.showerror("No File","Load a file first."); return
        if not self.sr_screens:
            messagebox.showerror("No Screens","No screens loaded."); return
        ind = getattr(self, "sr_indent", "   ")  # default 3 spaces if not detected
        new_block = "screenData:\n"
        for ni,(label,block) in enumerate(self.sr_screens):
            updated = re.sub(r'^\d+:',f'{ni}:',block,count=1)
            lines   = updated.splitlines()
            indented = "\n".join(
                ind+ln if not ln.startswith(ind) else ln for ln in lines)
            new_block += indented+"\n"
        new_content = re.sub(
            r'^screenData:[ \t]*\n.*?(?=^\w|\Z)',
            new_block, self.sr_file_content, flags=re.M|re.S)
        base,ext = os.path.splitext(self.sr_input_file)
        save_path = filedialog.asksaveasfilename(
            title="Save Reordered File",
            initialfile=os.path.basename(base)+"_reordered"+ext,
            defaultextension=ext,
            filetypes=[("YAML","*.yml *.yaml"),("All","*.*")])
        if not save_path: return
        try:
            with open(save_path,"w",encoding="utf-8") as fh:
                fh.write(new_content)
        except Exception as e:
            messagebox.showerror("Write Error",str(e)); return
        self.sr_status.set(f"Saved: {os.path.basename(save_path)}")
        messagebox.showinfo("Done",f"Saved to:\n{save_path}")

    # =========================================================================
    #  CLOSE
    # =========================================================================

    def _on_close(self):
        if HAS_MPL:
            s = self.settings["log_viewer"]
            s["threshold"]      = self.lv_thresh_var.get()
            s["show_threshold"] = self.lv_show_thresh.get()
            s["thresh_sensor"]  = self.lv_thresh_sen.get()
            s["last_profile"]   = self.lv_profile.get()
        self.settings["log_viewer"]["window_geometry"] = self.geometry()
        save_app_settings(self.settings)
        self.destroy()


# ═══════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    app = EdgeTXToolbox()
    app.mainloop()
