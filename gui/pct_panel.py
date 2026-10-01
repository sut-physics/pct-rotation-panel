#!/usr/bin/env python3
"""pCT Rotation Panel — GUI คุยกับเฟิร์มแวร์ pCT_RotationV3_Protocol ผ่าน Serial

ใช้ได้ทั้ง Windows และ Ubuntu (Tkinter + pyserial)

ออกแบบให้ "คนแก้ logic ใน Arduino แก้ได้โดยไม่ต้องแตะไฟล์นี้":
  - ปุ่ม/ช่องกรอกทั้งหมดมาจาก commands.json  (เพิ่ม/แก้คำสั่งที่นี่ แล้วกด 'โหลด config ใหม่')
  - ค่าคงที่ (baud, ค่า home, โซนเตือน, timeout, รูปแบบข้อความเก่า) อยู่ใน config.json
  - GUI อ่านสถานะจากบรรทัด '#STATE ...', '#DONE ...', '#ERR ...' ที่เฟิร์มแวร์ส่งมา (ดู README.md)
    ข้อความอื่นที่ไม่รู้จักแสดงใน log เฉยๆ ไม่ทำให้โปรแกรมพัง
  - ช่อง 'พิมพ์คำสั่งเอง' ไว้ลองคำสั่งใหม่ก่อนทำปุ่ม
"""
import json
import math
import os
import queue
import re
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    serial = None
    list_ports = None

BASE = os.path.dirname(os.path.abspath(__file__))

DEFAULT_CONFIG = {
    "baud": 115200, "boot_wait_ms": 2500, "busy_timeout_s": 240, "line_ending": "\n",
    "protocol_prefix": "#", "log_state_lines": False, "legacy_parsing": False,
    "on_connect_send": "", "show_raw_terminal": True,
    "initial_state": {"A": "0", "B": "0", "INIT": "0"},
    "state_display": [{"key": "A", "label": "A"}, {"key": "B", "label": "B"}],
    "state_extra": [], "info_text": "", "legacy_patterns": {},
    "dial": {"size": 220, "rotate": 0, "clockwise": False, "ticks": 30},
    "sensor": {"enabled": False},
}


# ---------------------------------------------------------------- parsing (ไม่ผูกกับ GUI, ทดสอบแยกได้)
def parse_protocol(line, prefix="#"):
    """บรรทัดที่ขึ้นต้นด้วย prefix -> (KIND, data) ถ้าไม่ใช่ -> None
    STATE คืน dict ของ KEY=VALUE, ตัวอื่นคืนข้อความที่เหลือ"""
    if not line.startswith(prefix):
        return None
    kind, _, rest = line[len(prefix):].strip().partition(" ")
    kind = kind.upper()
    if kind == "STATE":
        return kind, {k.upper(): v for k, v in re.findall(r"(\w+)=(\S+)", rest)}
    return kind, rest.strip()


def parse_legacy(line, pats):
    """อ่านข้อความแบบเฟิร์มแวร์รุ่นต้นแบบ (ไม่มี '#') คืน (state_update dict, done bool)"""
    upd, done = {}, False
    try:
        m = re.search(pats["angles_both"], line)
        if m:
            upd["A"], upd["B"] = m.group(1), m.group(2)
        m = re.search(pats["single_current"], line) or re.search(pats["single_set"], line)
        if m:
            upd[m.group(1)] = m.group(2)
        done = any(x in line for x in pats.get("done_markers", []))
        if any(x in line for x in pats.get("init_true_markers", [])):
            upd["INIT"] = "1"
        elif any(x in line for x in pats.get("init_false_markers", [])):
            upd["INIT"] = "0"
    except (KeyError, re.error):
        pass
    return upd, done


# ---------------------------------------------------------------- ธีมพาสเทล (แก้สีได้ที่นี่ที่เดียว)
PASTEL = {
    "bg": "#fff7fa",            # พื้นหลังหลัก (ชมพูครีม)
    "text": "#5b4a63",          # ตัวอักษร
    "muted": "#a293a8",         # ข้อความรอง
    "border": "#f3c6d8",        # เส้นกรอบ
    "title": "#c06c93",         # หัวกล่อง
    "big": "#8b6bb8",           # ตัวเลขมุมใหญ่
    "btn": "#fbd3e2", "btn_hover": "#f7b9d0",
    "minus": "#e3dcf7", "minus_hover": "#d0c4f2",
    "plus": "#d3f0e2", "plus_hover": "#b9e6d0",
    "disabled_bg": "#f4edf0", "disabled_fg": "#c3b6bd",
    "field": "#ffffff",
    "warn": "#c9803a", "ok": "#3f9a6a", "err": "#d05a7a", "tx": "#7a6fc2",
    "log_bg": "#fffcfd",
    "cat": "#fde3cc", "cat_line": "#8a6f7e", "cat_ear": "#f9bcd0", "blush": "#fac3d3", "heart": "#f28bb0",
    "zone": "#ffd9a8",          # ช่วงโซนแรงโน้มถ่วงบนวงกลม
    "sensor": "#4fb98c",        # มุมจากเซนเซอร์ (บอร์ดที่ 2)
    "keys": ["#ec7fa9", "#9b8be0", "#6cc4a0", "#f0a860"],  # สีของ carriage ตามลำดับใน state_display
}

# ข้อความใต้แมว ตามสถานะ
CAT_TEXT = {
    "sleep": "zzz… ยังไม่ได้เชื่อมต่อ",
    "wait": "กำลังปลุกบอร์ด…",
    "idle": "เมี๊ยว~ พร้อมแล้ว",
    "busy": "กำลังทำงาน รอแป๊บนะ…",
    "happy": "เสร็จแล้ว เมี๊ยว!",
    "sad": "อุ๊ย บอร์ดปฏิเสธคำสั่ง",
}


def draw_cat(c, mood, P=PASTEL):
    """วาดหน้าแมวบน Canvas ขนาด 140x100 ตามอารมณ์ (คีย์เดียวกับ CAT_TEXT)"""
    c.delete("all")
    line = P["cat_line"]
    for pts in ((34, 52, 40, 12, 64, 34), (106, 52, 100, 12, 76, 34)):
        c.create_polygon(pts, fill=P["cat"], outline=line, width=2, joinstyle="round")
    for pts in ((41, 44, 44, 22, 57, 36), (99, 44, 96, 22, 83, 36)):
        c.create_polygon(pts, fill=P["cat_ear"], outline="")
    c.create_oval(28, 28, 112, 94, fill=P["cat"], outline=line, width=2)
    c.create_oval(38, 68, 52, 76, fill=P["blush"], outline="")
    c.create_oval(88, 68, 102, 76, fill=P["blush"], outline="")
    for ex, d in ((55, 1), (85, -1)):
        if mood in ("idle", "wait"):
            c.create_oval(ex - 5, 53, ex + 5, 65, fill=line, outline="")
            c.create_oval(ex - 2, 55, ex + 1, 58, fill="white", outline="")
        elif mood == "happy":
            c.create_arc(ex - 6, 54, ex + 6, 66, start=0, extent=180, style="arc", outline=line, width=2)
        elif mood == "sad":
            c.create_line(ex - 5 * d, 54, ex + 4 * d, 59, ex - 5 * d, 64, fill=line, width=2)
        else:  # busy / sleep = หลับตา
            c.create_arc(ex - 6, 52, ex + 6, 64, start=180, extent=180, style="arc", outline=line, width=2)
    c.create_polygon(66, 68, 74, 68, 70, 73, fill=P["cat_ear"], outline=line)
    if mood == "sad":
        c.create_arc(64, 76, 76, 86, start=0, extent=180, style="arc", outline=line, width=2)
    else:
        c.create_arc(62, 68, 70, 78, start=180, extent=180, style="arc", outline=line, width=2)
        c.create_arc(70, 68, 78, 78, start=180, extent=180, style="arc", outline=line, width=2)
    for x1, x2 in ((14, 40), (126, 100)):
        c.create_line(x1, 67, x2, 71, fill=line)
        c.create_line(x1, 79, x2, 76, fill=line)
    extra = {"sleep": ("z Z", P["muted"]), "busy": ("…", P["muted"]), "happy": ("♥", P["heart"]), "sad": ("!", P["warn"])}
    if mood in extra:
        text, color = extra[mood]
        c.create_text(124, 20, text=text, fill=color, font=("TkDefaultFont", 13, "bold"))


def draw_dial(c, items, dial, extras=(), P=PASTEL):
    """วาดวงกลมแสดงตำแหน่ง carriage  items = [(ชื่อสั้น, มุม|None, สี, zone|None), ...]
    extras = [(ชื่อสั้น, มุม|None, สี), ...] จุดเพิ่ม เช่น มุมจากเซนเซอร์
    dial: rotate = มุมจอ (องศา ทวนเข็มจากขวา) ที่ใช้วาง 0°, clockwise = มุมบวกหมุนตามเข็มบนจอ"""
    c.delete("all")
    size = int(dial.get("size", 220))
    cx = cy = size / 2
    r = size / 2 - 32
    rot = float(dial.get("rotate", 0))
    sign = -1 if dial.get("clockwise") else 1

    def scr(deg):  # มุมเครื่อง -> มุมจอแบบเดียวกับ create_arc (ทวนเข็มจาก 3 นาฬิกา)
        return rot + sign * deg

    def pt(deg, rad):
        a = math.radians(scr(deg))
        return cx + rad * math.cos(a), cy - rad * math.sin(a)

    c.create_oval(cx - r, cy - r, cx + r, cy + r, outline=P["border"], width=8)
    for *_, zone in items:
        if zone:
            c.create_arc(cx - r, cy - r, cx + r, cy + r, start=scr(zone[0]), extent=sign * (zone[1] - zone[0]),
                         style="arc", outline=P["zone"], width=8)
    step = int(dial.get("ticks", 30)) or 30
    for deg in range(0, 360, step):
        c.create_line(*pt(deg, r - 8), *pt(deg, r - (18 if deg % 90 == 0 else 13)), fill=P["muted"])
    for deg, txt in ((0, "0°"), (90, "90°"), (180, "180°"), (-90, "-90°")):
        c.create_text(*pt(deg, r + 18), text=txt, fill=P["muted"], font=("TkDefaultFont", 8))
    pts = [pt(v, r) for _, v, _, _ in items if v is not None]
    if len(pts) == 2:  # เส้นเชื่อม A-B (ปกติอยู่ตรงข้ามกันเมื่อซิงค์ B = A - 180)
        c.create_line(*pts[0], *pts[1], fill=P["muted"], dash=(3, 3))
    c.create_oval(cx - 3, cy - 3, cx + 3, cy + 3, fill=P["muted"], outline="")
    for name, v, color, _ in items:
        if v is None:
            continue
        x, y = pt(v, r)
        c.create_line(cx, cy, x, y, fill=color, width=2)
        c.create_oval(x - 12, y - 12, x + 12, y + 12, fill=color, outline="white", width=2)
        c.create_text(x, y, text=name, fill="white", font=("TkDefaultFont", 9, "bold"))
    for name, v, color in extras:  # ค่าจากเซนเซอร์ วาดเป็นจุดเล็กด้านในวง จะได้ไม่ทับจุด carriage
        if v is None:
            continue
        x, y = pt(v, r - 30)
        c.create_line(cx, cy, x, y, fill=color, width=2, dash=(4, 2))
        c.create_oval(x - 9, y - 9, x + 9, y + 9, fill=color, outline="white", width=2)
        c.create_text(x, y, text=name, fill="white", font=("TkDefaultFont", 8, "bold"))


def read_lines(ser, stop, out, tag):
    """thread อ่าน Serial ทีละบรรทัด ส่งเข้า queue เป็น (tag, "line"|"error", ข้อความ)"""
    buf = b""
    while not stop.is_set():
        try:
            chunk = ser.read(256)
        except Exception as e:  # noqa: BLE001  สายหลุด
            out.put((tag, "error", str(e)))
            return
        if not chunk:
            continue
        buf += chunk
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            out.put((tag, "line", line.decode("utf-8", errors="replace").rstrip("\r")))


def load_json(name):
    with open(os.path.join(BASE, name), encoding="utf-8") as f:
        return json.load(f)


def fmt_num(v):
    return f"{v:g}"


# ---------------------------------------------------------------- GUI
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("pCT Rotation Panel")
        self.minsize(960, 600)
        self.cfg = dict(DEFAULT_CONFIG)
        self.commands = {"groups": []}
        self.ser = None
        self.rx = queue.Queue()
        self.reader = None
        self.stop_reader = threading.Event()
        self.ready = False
        self.busy = False
        self.busy_since = 0.0
        self.proto_seen = False
        self.state = {}
        self.cmd_widgets = []
        self.cat_mood = "idle"
        self._cat_shown = None
        # บอร์ดที่ 2 (เซนเซอร์มุม) อ่านอย่างเดียว
        self.s_ser = None
        self.s_reader = None
        self.s_stop = threading.Event()
        self.s_val = None
        self.s_time = 0.0
        self._s_stale = True
        self.s_row = None
        self.s_zero_ref = None

        self.apply_theme()
        self.build()
        self.load_configs(first=True)
        self.refresh_ports()
        self.after(50, self.poll)
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        try:  # เปิดเต็มจอ ช่อง Serial Monitor ของเซนเซอร์จะได้มีที่
            tk.Wm.state(self, "zoomed")  # self.state เป็น dict สถานะเฟิร์มแวร์ จึงเรียกของ Tk ตรงๆ
        except tk.TclError:
            try:
                self.attributes("-zoomed", True)  # Ubuntu
            except tk.TclError:
                pass
        if serial is None:
            messagebox.showerror("ไม่พบ pyserial", "ยังไม่ได้ติดตั้ง pyserial\nรัน install.sh (Ubuntu) หรือ install.bat (Windows) ก่อน")

    # ---------------------------------------------------- ธีม
    def apply_theme(self):
        P = PASTEL
        self.configure(bg=P["bg"])
        s = ttk.Style(self)
        s.theme_use("clam")  # ธีมที่ยอมให้เปลี่ยนสีปุ่มได้ทั้ง Windows และ Ubuntu
        s.configure(".", background=P["bg"], foreground=P["text"], fieldbackground=P["field"],
                    bordercolor=P["border"], lightcolor=P["bg"], darkcolor=P["bg"], troughcolor=P["disabled_bg"],
                    focuscolor=P["border"], selectbackground=P["btn_hover"], selectforeground=P["text"])
        s.configure("TLabelframe", bordercolor=P["border"], relief="solid", borderwidth=1)
        s.configure("TLabelframe.Label", foreground=P["title"], font=("TkDefaultFont", 10, "bold"))
        for name, base, hover, pad in (("TButton", "btn", "btn_hover", (10, 4)),
                                       ("Minus.TButton", "minus", "minus_hover", (4, 4)),
                                       ("Plus.TButton", "plus", "plus_hover", (4, 4))):
            s.configure(name, background=P[base], bordercolor=P[base], lightcolor=P[base], darkcolor=P[base], padding=pad)
            states = [("disabled", P["disabled_bg"]), ("pressed", P[hover]), ("active", P[hover])]
            s.map(name, background=states, bordercolor=states, lightcolor=states, darkcolor=states,
                  foreground=[("disabled", P["disabled_fg"])])
        s.configure("TEntry", bordercolor=P["border"])
        s.map("TCombobox", fieldbackground=[("readonly", P["field"])],
              selectbackground=[("readonly", P["field"])], selectforeground=[("readonly", P["text"])])
        s.configure("Big.TLabel", font=("TkDefaultFont", 18, "bold"), foreground=P["big"])
        s.configure("Head.TLabel", font=("TkDefaultFont", 9, "bold"))
        s.configure("Warn.TLabel", foreground=P["warn"])
        s.configure("Ok.TLabel", foreground=P["ok"])
        s.configure("Muted.TLabel", foreground=P["muted"])
        s.configure("Sensor.TLabel", foreground=P["sensor"], font=("TkDefaultFont", 9, "bold"))
        s.configure("SensorVal.TLabel", foreground=P["sensor"], font=("TkDefaultFont", 14, "bold"))

    # ---------------------------------------------------- config
    def load_configs(self, first=False):
        errors = []
        try:
            self.cfg = {**DEFAULT_CONFIG, **load_json("config.json")}
        except Exception as e:  # noqa: BLE001
            errors.append(f"config.json: {e}")
            self.cfg = dict(DEFAULT_CONFIG)
        try:
            self.commands = load_json("commands.json")
        except Exception as e:  # noqa: BLE001
            errors.append(f"commands.json: {e}")
            self.commands = {"groups": []}
        if errors:
            messagebox.showerror("อ่านไฟล์ตั้งค่าไม่ได้", "\n".join(errors) + "\n\nใช้ค่าเริ่มต้นแทน แก้ไฟล์แล้วกด 'โหลด config ใหม่'")
        if first or not self.ser:
            self.state = dict(self.cfg["initial_state"])
        self.build_state_panel()
        self.build_commands()
        self.raw_frame.pack_forget()
        self.s_mon_frame.pack_forget()
        if self.cfg.get("show_raw_terminal", True):
            self.raw_frame.pack(fill="x", pady=(8, 0))
        self.sensor_bar.pack_forget()
        if self.sensor_cfg().get("enabled"):
            self.s_mon_frame.pack(fill="both", expand=True, pady=(8, 0))
            self.build_sensor_buttons()
            self.sensor_bar.pack(fill="x", padx=10, pady=(0, 2), before=self.body)
            self.s_title.config(text=self.sensor_cfg().get("label", "เซนเซอร์"))
        self.update_state()

    def sensor_cfg(self):
        return self.cfg.get("sensor") or {}

    # ---------------------------------------------------- layout
    def build(self):
        top = ttk.Frame(self)
        top.pack(fill="x", padx=10, pady=6)
        ttk.Label(top, text="พอร์ต").pack(side="left")
        self.port_var = tk.StringVar()
        self.port_box = ttk.Combobox(top, textvariable=self.port_var, width=42, state="readonly")
        self.port_box.pack(side="left", padx=6)
        ttk.Button(top, text="รีเฟรช", command=self.refresh_ports).pack(side="left")
        self.btn_conn = ttk.Button(top, text="เชื่อมต่อ", command=self.toggle_connect)
        self.btn_conn.pack(side="left", padx=6)
        self.conn_lbl = ttk.Label(top, text="ยังไม่ได้เชื่อมต่อ")
        self.conn_lbl.pack(side="left", padx=6)
        ttk.Button(top, text="โหลด config ใหม่", command=self.load_configs).pack(side="right")

        # แถวเชื่อมต่อบอร์ดที่ 2 (แสดงเมื่อ sensor.enabled = true ใน config.json)
        self.sensor_bar = ttk.Frame(self)
        self.s_title = ttk.Label(self.sensor_bar, style="Sensor.TLabel")
        self.s_title.pack(side="left")
        self.s_port_var = tk.StringVar()
        self.s_port_box = ttk.Combobox(self.sensor_bar, textvariable=self.s_port_var, width=42, state="readonly")
        self.s_port_box.pack(side="left", padx=6)
        self.s_btn = ttk.Button(self.sensor_bar, text="เชื่อมต่อ", command=self.toggle_sensor)
        self.s_btn.pack(side="left")
        self.s_lbl = ttk.Label(self.sensor_bar, text="ยังไม่ได้เชื่อมต่อ (อ่านอย่างเดียว)")
        self.s_lbl.pack(side="left", padx=6)

        body = self.body = ttk.Frame(self)
        body.pack(fill="both", expand=True, padx=10, pady=6)
        body.columnconfigure(1, weight=1)
        body.columnconfigure(2, weight=1)
        body.rowconfigure(0, weight=1)

        self.left = ttk.LabelFrame(body, text="ตำแหน่งปัจจุบัน (ค่าที่เฟิร์มแวร์รายงาน)")
        self.left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        self.cat = tk.Canvas(self.left, width=140, height=98, bg=PASTEL["bg"], highlightthickness=0)
        self.cat.pack(pady=(2, 0))
        self.cat_lbl = ttk.Label(self.left, style="Muted.TLabel")
        self.cat_lbl.pack()
        self.state_frame = ttk.Frame(self.left)
        self.state_frame.pack(fill="x")
        self.busy_lbl = ttk.Label(self.left)
        self.busy_lbl.pack(anchor="w", padx=10, pady=(6, 0))
        ttk.Button(self.left, text="ล้างสถานะ 'กำลังหมุน'", command=self.clear_busy).pack(anchor="w", padx=10, pady=6)
        self.info_lbl = ttk.Label(self.left, justify="left", style="Muted.TLabel", wraplength=230)
        self.info_lbl.pack(anchor="w", padx=10, pady=(4, 10))

        mid = ttk.Frame(body)
        mid.grid(row=0, column=1, sticky="nsew", padx=(0, 8))
        self.cmd_frame = ttk.Frame(mid)
        self.cmd_frame.pack(fill="x")
        self.raw_frame = ttk.LabelFrame(mid, text="พิมพ์คำสั่งเอง (เหมือน Serial Monitor)")
        self.raw_var = tk.StringVar()
        row = ttk.Frame(self.raw_frame)
        row.pack(fill="x", padx=10, pady=8)
        self.raw_entry = ttk.Entry(row, textvariable=self.raw_var)
        self.raw_entry.pack(side="left", fill="x", expand=True)
        self.raw_entry.bind("<Return>", lambda _e: self.send_raw())
        self.raw_btn = ttk.Button(row, text="ส่ง", command=self.send_raw)
        self.raw_btn.pack(side="left", padx=(6, 0))

        # Serial Monitor ของบอร์ดเซนเซอร์ แสดงทุกบรรทัดต่อเนื่อง (แสดงเมื่อ sensor.enabled)
        self.s_mon_frame = ttk.LabelFrame(mid, text="Serial Monitor: เซนเซอร์")
        bar = ttk.Frame(self.s_mon_frame)
        bar.pack(fill="x", padx=6, pady=(4, 0))
        # ช่องพิมพ์คำสั่งไปบอร์ดเซนเซอร์ + ปุ่มลัดจาก sensor.buttons ใน config.json (เช่น z = ตั้งศูนย์)
        self.s_cmd_var = tk.StringVar()
        self.s_entry = ttk.Entry(bar, textvariable=self.s_cmd_var, width=12)
        self.s_entry.pack(side="left")
        self.s_entry.bind("<Return>", lambda _e: self.send_sensor_raw())
        self.s_send_btn = ttk.Button(bar, text="ส่ง", command=self.send_sensor_raw)
        self.s_send_btn.pack(side="left", padx=(4, 0))
        self.s_btn_frame = ttk.Frame(bar)
        self.s_btn_frame.pack(side="left", padx=(8, 0))
        self.s_cmd_widgets = [self.s_send_btn]
        self.s_autoscroll = tk.BooleanVar(value=True)
        self.s_timestamp = tk.BooleanVar(value=False)
        ttk.Button(bar, text="ล้าง", command=lambda: self.clear_text(self.s_mon)).pack(side="right")
        ttk.Checkbutton(bar, text="แสดงเวลา", variable=self.s_timestamp).pack(side="right", padx=10)
        ttk.Checkbutton(bar, text="เลื่อนอัตโนมัติ", variable=self.s_autoscroll).pack(side="right")
        box = ttk.Frame(self.s_mon_frame)
        box.pack(fill="both", expand=True, padx=6, pady=6)
        self.s_mon = tk.Text(box, width=40, height=4, state="disabled", wrap="none", bg=PASTEL["log_bg"], fg=PASTEL["sensor"],
                             font="TkFixedFont", relief="flat", highlightthickness=1,
                             highlightbackground=PASTEL["border"], highlightcolor=PASTEL["border"])
        self.s_mon.pack(side="left", fill="both", expand=True)
        msb = ttk.Scrollbar(box, command=self.s_mon.yview)
        msb.pack(side="left", fill="y")
        self.s_mon["yscrollcommand"] = msb.set
        self.s_mon.tag_config("ts", foreground=PASTEL["muted"])
        self.s_mon.tag_config("info", foreground=PASTEL["warn"])
        self.s_mon.tag_config("tx", foreground=PASTEL["tx"])
        self.s_pending = []

        right = ttk.LabelFrame(body, text="Serial log")
        right.grid(row=0, column=2, sticky="nsew")
        right.rowconfigure(0, weight=1)
        right.columnconfigure(0, weight=1)
        self.log_box = tk.Text(right, width=44, height=20, state="disabled", wrap="word",
                               bg=PASTEL["log_bg"], fg=PASTEL["text"], font="TkFixedFont", insertbackground=PASTEL["text"],
                               relief="flat", highlightthickness=1, highlightbackground=PASTEL["border"],
                               highlightcolor=PASTEL["border"])
        self.log_box.grid(row=0, column=0, sticky="nsew", padx=(6, 0), pady=6)
        sb = ttk.Scrollbar(right, command=self.log_box.yview)
        sb.grid(row=0, column=1, sticky="ns", pady=6)
        self.log_box["yscrollcommand"] = sb.set
        for tag, color in (("tx", PASTEL["tx"]), ("rx", PASTEL["text"]), ("ok", PASTEL["ok"]), ("err", PASTEL["err"]),
                           ("warn", PASTEL["warn"]), ("dim", PASTEL["muted"]), ("sensor", PASTEL["sensor"])):
            self.log_box.tag_config(tag, foreground=color)
        ttk.Button(right, text="ล้าง log", command=self.clear_log).grid(row=1, column=0, sticky="w", padx=6, pady=(0, 6))

    def build_state_panel(self):
        for w in self.state_frame.winfo_children():
            w.destroy()
        size = int(self.cfg["dial"].get("size", 220))
        self.dial = tk.Canvas(self.state_frame, width=size, height=size, bg=PASTEL["bg"], highlightthickness=0)
        self.dial.pack(pady=(4, 0))
        nums = ttk.Frame(self.state_frame)
        nums.pack(fill="x", padx=10)
        self.state_labels = {}
        style = ttk.Style(self)
        for i, d in enumerate(self.cfg["state_display"]):
            color = d.get("color") or PASTEL["keys"][i % len(PASTEL["keys"])]
            style.configure(f"Key{i}.TLabel", foreground=color, font=("TkDefaultFont", 9, "bold"))
            nums.columnconfigure(i, weight=1)
            ttk.Label(nums, text="● " + d["label"], style=f"Key{i}.TLabel").grid(row=0, column=i, sticky="w", pady=(6, 0))
            big = ttk.Label(nums, style="Big.TLabel")
            big.grid(row=1, column=i, sticky="w")
            zone = ttk.Label(nums, style="Warn.TLabel")
            zone.grid(row=2, column=i, sticky="w")
            self.state_labels[d["key"]] = (big, zone, d, color)
        self.s_row = None
        sc = self.sensor_cfg()
        if sc.get("enabled"):
            self.s_row = ttk.Frame(self.state_frame)
            self.s_row.pack(fill="x", padx=10, pady=(6, 0))
            ttk.Label(self.s_row, text=f"● {sc.get('display_label', 'เซนเซอร์')}", style="Sensor.TLabel").grid(
                row=0, column=0, sticky="w")
            self.s_val_lbl = ttk.Label(self.s_row, style="SensorVal.TLabel")
            self.s_val_lbl.grid(row=0, column=1, sticky="w", padx=(8, 0))
            self.s_diff_lbl = ttk.Label(self.s_row, style="Muted.TLabel")
            self.s_diff_lbl.grid(row=1, column=0, columnspan=2, sticky="w")
        ttk.Separator(self.state_frame).pack(fill="x", padx=10, pady=8)
        self.extra_labels = []
        for d in self.cfg["state_extra"]:
            row = ttk.Frame(self.state_frame)
            row.pack(fill="x", padx=10)
            ttk.Label(row, text=d["label"]).pack(side="left")
            val = ttk.Label(row)
            val.pack(side="right")
            self.extra_labels.append((val, d))
        self.info_lbl.config(text=self.cfg.get("info_text", ""))

    def build_commands(self):
        for w in self.cmd_frame.winfo_children():
            w.destroy()
        self.cmd_widgets = []
        for g in self.commands.get("groups", []):
            box = ttk.LabelFrame(self.cmd_frame, text=g.get("title", ""))
            box.pack(fill="x", pady=(0, 10))
            for item in g.get("items", []):
                self.build_item(box, item)

    def build_item(self, parent, item):
        t = item.get("type", "button")
        if t == "button":
            b = ttk.Button(parent, text=item.get("label", item.get("send", "?")), command=lambda: self.run_item(item))
            b.pack(anchor="w", padx=10, pady=4)
            self.cmd_widgets.append(b)
        elif t == "row":
            row = ttk.Frame(parent)
            row.pack(fill="x", padx=10, pady=4)
            for sub in item.get("items", []):
                b = ttk.Button(row, text=sub.get("label", sub.get("send", "?")), command=lambda s=sub: self.run_item(s))
                b.pack(side="left", padx=(0, 6))
                self.cmd_widgets.append(b)
        elif t == "note":
            ttk.Label(parent, text=item.get("text", ""), wraplength=420, justify="left").pack(anchor="w", padx=10, pady=(6, 0))
        elif t == "separator":
            ttk.Separator(parent).pack(fill="x", padx=10, pady=6)
        elif t == "field":
            self.build_field(parent, item)
        else:
            ttk.Label(parent, text=f"(ไม่รู้จัก type '{t}' ใน commands.json)", style="Warn.TLabel").pack(anchor="w", padx=10)

    def build_field(self, parent, item):
        var = tk.StringVar(value=str(item.get("default", "")))
        row = ttk.Frame(parent)
        row.pack(fill="x", padx=10, pady=3)
        ttk.Label(row, text=item.get("prefix", ""), width=6).pack(side="left")
        ttk.Entry(row, textvariable=var, width=10).pack(side="left", padx=6)
        b = ttk.Button(row, text=item.get("button", "ส่ง"), command=lambda: self.run_item(item, var))
        b.pack(side="left")
        self.cmd_widgets.append(b)
        nudge = item.get("nudge")
        if nudge:
            nrow = ttk.Frame(parent)
            nrow.pack(fill="x", padx=10, pady=3)
            for step in nudge.get("steps", []):
                nb = ttk.Button(nrow, text=f"{step:+g}", width=5, style="Minus.TButton" if step < 0 else "Plus.TButton",
                                command=lambda s=step: self.run_nudge(item, var, nudge, s))
                nb.pack(side="left", padx=2)
                self.cmd_widgets.append(nb)
        prev = item.get("preview")
        if prev:
            lbl = ttk.Label(parent, style="Muted.TLabel")
            lbl.pack(anchor="w", padx=10)

            def upd(*_):
                try:
                    v = float(var.get())
                    lbl.config(text=f"จะสั่ง: {fmt_num(v)}  →  {prev.get('label', '')} = {v + prev.get('offset', 0):.2f}°")
                except ValueError:
                    lbl.config(text="")
            var.trace_add("write", upd)
            upd()

    # ---------------------------------------------------- พอร์ต
    def refresh_ports(self):
        if list_ports is None:
            return
        self.port_map = {f"{p.device} — {p.description}": p.device for p in list_ports.comports()}
        names = list(self.port_map)
        self.port_box["values"] = names
        if self.port_var.get() not in names:
            self.port_var.set(next((n for n in names if any(k in n for k in ("Arduino", "ACM", "USB", "CH340"))),
                                   names[0] if names else ""))
        self.s_port_box["values"] = names
        if self.s_port_var.get() not in names:  # เดาพอร์ตเซนเซอร์เป็นพอร์ตที่ไม่ใช่ของ Mega
            self.s_port_var.set(next((n for n in names if n != self.port_var.get()), ""))

    def toggle_sensor(self):
        if self.s_ser:
            self.disconnect_sensor()
            return
        dev = getattr(self, "port_map", {}).get(self.s_port_var.get())
        if not dev:
            messagebox.showwarning("ไม่มีพอร์ต", "ไม่พบพอร์ตของเซนเซอร์ ลองเสียบสายแล้วกด รีเฟรช")
            return
        if self.ser and dev == self.ser.port:
            messagebox.showwarning("พอร์ตซ้ำ", f"{dev} ใช้กับ Arduino Mega อยู่แล้ว เลือกพอร์ตของบอร์ดเซนเซอร์")
            return
        try:
            self.s_ser = serial.Serial(dev, int(self.sensor_cfg().get("baud", 9600)), timeout=0.1)
        except Exception as e:  # noqa: BLE001
            self.s_ser = None
            messagebox.showerror("เปิดพอร์ตเซนเซอร์ไม่ได้", f"{dev}\n{e}\n\nปิด Serial Monitor ของ Arduino IDE ก่อน")
            return
        self.s_val, self.s_time = None, 0.0
        self.s_zero_ref = None  # บอร์ดเซนเซอร์รีเซ็ตตอนเปิดพอร์ต แล้วตั้งศูนย์ใหม่เอง รอดูข้อความใน zero_markers
        self.s_stop.clear()
        self.s_reader = threading.Thread(target=read_lines, args=(self.s_ser, self.s_stop, self.rx, "sensor"), daemon=True)
        self.s_reader.start()
        self.s_btn.config(text="ตัดการเชื่อมต่อ")
        self.s_lbl.config(text=f"เชื่อมต่อ {dev}", style="Ok.TLabel")
        self.monitor_note(f"เชื่อมต่อ {dev} @ {self.s_ser.baudrate}")
        self.update_state()

    def disconnect_sensor(self):
        was_connected = self.s_ser is not None
        self.s_stop.set()
        if self.s_reader:
            self.s_reader.join(timeout=1)
        try:
            if self.s_ser:
                self.s_ser.close()
        except Exception:  # noqa: BLE001
            pass
        self.s_ser = None
        self.s_val = None
        self.s_btn.config(text="เชื่อมต่อ")
        self.s_lbl.config(text="ยังไม่ได้เชื่อมต่อ", style="TLabel")
        if was_connected:
            self.monitor_note("ตัดการเชื่อมต่อ")
        self.update_state()

    def handle_sensor_line(self, line):
        """ทุกบรรทัดไปที่ Serial Monitor ของเซนเซอร์ (เก็บไว้ใน s_pending แล้ว poll() เขียนทีเดียว) และดึงค่ามุมถ้าตรง pattern"""
        if not line.strip():
            return
        self.s_pending.append((time.strftime("%H:%M:%S.") + f"{int(time.time() * 1000) % 1000:03d}", line, None))
        sc = self.sensor_cfg()
        if any(mk in line for mk in sc.get("zero_markers", [])):
            self.on_sensor_zero()
        m = None
        try:
            m = re.search(sc.get("pattern", r"(-?\d+(?:\.\d+)?)"), line)
        except re.error:
            pass
        if m:
            try:
                # ค่าจากเซนเซอร์เป็นมุมสัมพัทธ์จากจุดศูนย์ บวกมุมของ carriage ตอนตั้งศูนย์ จะได้มุมในระบบเดียวกับ A/B
                self.s_val = (float(m.group(1)) * float(sc.get("scale", 1)) + float(sc.get("offset", 0))
                              + (self.s_zero_ref or 0.0))
                self.s_time = time.time()
            except (ValueError, IndexError):
                pass

    def on_sensor_zero(self):
        """เซนเซอร์เพิ่งตั้งศูนย์ (สั่ง z หรือเพิ่งบูต) จำมุมของ carriage ตอนนี้ไว้เป็นจุดอ้างอิง"""
        key = self.sensor_cfg().get("zero_ref", "")
        if not key:
            return
        self.s_zero_ref = self.num(key)
        if self.s_zero_ref is None:
            self.monitor_note(f"เซนเซอร์ตั้งศูนย์แล้ว แต่ยังไม่รู้มุม {key} จากเฟิร์มแวร์")
        else:
            self.monitor_note(f"เซนเซอร์ตั้งศูนย์: 0 ของเซนเซอร์ = {key} {self.s_zero_ref:.2f}°")

    def build_sensor_buttons(self):
        for w in self.s_btn_frame.winfo_children():
            w.destroy()
        self.s_cmd_widgets = [self.s_send_btn]
        for b in self.sensor_cfg().get("buttons", []):
            btn = ttk.Button(self.s_btn_frame, text=b.get("label", b.get("send", "?")),
                             command=lambda b=b: self.run_sensor_button(b))
            btn.pack(side="left", padx=(0, 4))
            self.s_cmd_widgets.append(btn)

    def run_sensor_button(self, b):
        if b.get("confirm") and not messagebox.askyesno("ยืนยัน", b["confirm"]):
            return
        self.send_sensor(b.get("send", ""))

    def send_sensor_raw(self):
        cmd = self.s_cmd_var.get().strip()
        if cmd and self.send_sensor(cmd):
            self.s_cmd_var.set("")

    def send_sensor(self, cmd):
        if not self.s_ser:
            messagebox.showinfo("ยังไม่ได้เชื่อมต่อ", "เชื่อมต่อบอร์ดเซนเซอร์ก่อน")
            return False
        try:
            self.s_ser.write((cmd + self.sensor_cfg().get("line_ending", "\n")).encode("utf-8"))
        except Exception as e:  # noqa: BLE001
            self.monitor_note(f"ส่งไม่ได้: {e}")
            return False
        self.s_pending.append((None, "> " + cmd, "tx"))
        self.flush_monitor()
        return True

    def monitor_note(self, text):
        """ข้อความของ GUI เอง (เช่น เชื่อมต่อ/ตัด) ใน Serial Monitor เซนเซอร์"""
        self.s_pending.append((None, f"--- {text} ---", "info"))
        self.flush_monitor()

    def flush_monitor(self):
        if not self.s_pending:
            return
        box = self.s_mon
        box.config(state="normal")
        show_ts = self.s_timestamp.get()
        for ts, line, tag in self.s_pending:
            if show_ts and ts:
                box.insert("end", ts + " -> ", "ts")
            box.insert("end", line + "\n", tag or ())
        self.s_pending.clear()
        limit = int(self.sensor_cfg().get("monitor_max_lines", 2000))
        lines = int(box.index("end-1c").split(".")[0])
        if lines > limit:
            box.delete("1.0", f"{lines - limit + 1}.0")
        if self.s_autoscroll.get():
            box.see("end")
        box.config(state="disabled")

    def sensor_stale(self):
        return time.time() - self.s_time > float(self.sensor_cfg().get("stale_ms", 1000)) / 1000

    def toggle_connect(self):
        if self.ser:
            self.disconnect()
            return
        dev = getattr(self, "port_map", {}).get(self.port_var.get())
        if not dev:
            messagebox.showwarning("ไม่มีพอร์ต", "ไม่พบพอร์ต ลองเสียบสายแล้วกด รีเฟรช")
            return
        try:
            self.ser = serial.Serial(dev, int(self.cfg["baud"]), timeout=0.1)
        except Exception as e:  # noqa: BLE001
            self.ser = None
            messagebox.showerror("เปิดพอร์ตไม่ได้", f"{dev}\n{e}\n\nUbuntu: ต้องอยู่ในกลุ่ม dialout (install.sh ทำให้)\n"
                                                    "Windows: ปิด Serial Monitor ของ Arduino IDE ก่อน")
            return
        # Arduino รีเซ็ตตอนเปิดพอร์ต ค่าที่เฟิร์มแวร์จำจึงกลับเป็นค่าเริ่มต้น
        self.state = dict(self.cfg["initial_state"])
        self.busy = self.ready = self.proto_seen = False
        self.cat_mood = "idle"
        self.stop_reader.clear()
        self.reader = threading.Thread(target=read_lines, args=(self.ser, self.stop_reader, self.rx, "main"), daemon=True)
        self.reader.start()
        self.btn_conn.config(text="ตัดการเชื่อมต่อ")
        self.conn_lbl.config(text=f"เชื่อมต่อ {dev} (บอร์ดรีเซ็ต รอสักครู่...)", style="Warn.TLabel")
        self.log("บอร์ดรีเซ็ตตอนเปิดพอร์ต ค่ามุมกลับเป็นค่าเริ่มต้น ถ้าเครื่องอยู่ที่อื่น ให้ประกาศตำแหน่งจริงก่อน", "warn")
        self.after(int(self.cfg["boot_wait_ms"]), self.boot_done)
        self.update_state()

    def boot_done(self):
        if not self.ser:
            return
        self.ready = True
        self.conn_lbl.config(text=f"เชื่อมต่อ {self.ser.port}", style="Ok.TLabel")
        if self.cfg.get("on_connect_send"):
            self.send(self.cfg["on_connect_send"], busy=False)
        self.update_state()

    def disconnect(self):
        self.stop_reader.set()
        if self.reader:
            self.reader.join(timeout=1)
        try:
            if self.ser:
                self.ser.close()
        except Exception:  # noqa: BLE001
            pass
        self.ser = None
        self.ready = self.busy = False
        self.btn_conn.config(text="เชื่อมต่อ")
        self.conn_lbl.config(text="ยังไม่ได้เชื่อมต่อ", style="TLabel")
        self.update_state()

    # ---------------------------------------------------- ส่งคำสั่ง
    def send(self, cmd, busy=True):
        if not self.ser or not self.ready:
            messagebox.showinfo("ยังไม่พร้อม", "ต่อพอร์ตและรอบอร์ดบูตก่อน")
            return False
        try:
            self.ser.write((cmd + self.cfg["line_ending"]).encode("utf-8"))
        except Exception as e:  # noqa: BLE001
            self.log(f"ส่งไม่ได้: {e}", "err")
            return False
        self.log("> " + cmd, "tx")
        if busy:
            self.busy, self.busy_since = True, time.time()
        self.update_state()
        return True

    def send_raw(self):
        cmd = self.raw_var.get().strip()
        if not cmd or self.busy:
            return
        # เฟิร์มแวร์ที่ใช้ protocol จะตอบ #DONE/#ERR เสมอ จึงล็อกปุ่มรอได้ ถ้าเป็นรุ่นเก่าไม่ล็อกเพราะอาจไม่มีข้อความจบงาน
        if self.send(cmd, busy=self.proto_seen):
            self.raw_var.set("")

    def is_init(self):
        return str(self.state.get("INIT", "0")).lower() in ("1", "true")

    def num(self, key):
        try:
            return float(self.state.get(key))
        except (TypeError, ValueError):
            return None

    def run_item(self, item, var=None):
        if self.busy:
            return
        if item.get("requires_init") and not self.is_init():
            messagebox.showinfo("ยังไม่ init", "ต้อง start หรือประกาศตำแหน่ง (SETA+SETB) ให้ครบก่อน")
            return
        cmd = item.get("send", "")
        if var is not None:
            try:
                v = float(var.get().strip())
            except ValueError:
                messagebox.showwarning("ค่าไม่ถูกต้อง", f"'{var.get()}' ไม่ใช่ตัวเลข")
                return
            lim = item.get("warn_abs_over")
            if lim and abs(v) > lim and not messagebox.askyesno(
                    "มุมเกินขีดจำกัดเตือน", f"{fmt_num(v)}° เกิน {fmt_num(lim)}° ยืนยันจะสั่งจริงหรือไม่?"):
                return
            cmd = cmd.replace("{value}", fmt_num(v))
        if item.get("confirm") and not messagebox.askyesno("ยืนยัน", item["confirm"]):
            return
        self.send(cmd, busy=item.get("busy", True))

    def run_nudge(self, item, var, nudge, step):
        base = self.num(nudge.get("from", ""))
        if base is None:
            messagebox.showinfo("ไม่ทราบตำแหน่ง", "ยังไม่ได้รับค่าตำแหน่งจากเฟิร์มแวร์")
            return
        var.set(fmt_num(round(base + step, 2)))
        self.run_item(item, var)

    # ---------------------------------------------------- รับข้อมูล
    def poll(self):
        sensor_changed = False
        try:
            while True:
                tag, kind, payload = self.rx.get_nowait()
                if tag == "sensor":
                    if kind == "error":
                        self.log(f"พอร์ตเซนเซอร์หลุด: {payload}", "err")
                        self.disconnect_sensor()
                    elif self.s_ser:
                        self.handle_sensor_line(payload)
                        sensor_changed = True
                elif kind == "error":
                    self.log(f"พอร์ตหลุด: {payload}", "err")
                    self.disconnect()
                else:
                    self.handle_line(payload)
        except queue.Empty:
            pass
        self.flush_monitor()
        stale = self.sensor_stale()
        if sensor_changed or stale != self._s_stale:  # วาดใหม่ครั้งเดียวต่อรอบ ไม่ใช่ทุกบรรทัด (เซนเซอร์ส่ง 50 Hz)
            self._s_stale = stale
            self.update_state()
        if self.busy and time.time() - self.busy_since > float(self.cfg["busy_timeout_s"]):
            self.log("ไม่ได้รับข้อความจบงานนานเกินไป ปลดล็อกปุ่มให้แล้ว", "warn")
            self.clear_busy()
        self.after(50, self.poll)

    def handle_line(self, line):
        if not line.strip():
            return
        p = parse_protocol(line, self.cfg["protocol_prefix"])
        if p:
            self.proto_seen = True
            kind, data = p
            if kind == "STATE":
                self.state.update(data)
                if self.cfg.get("log_state_lines"):
                    self.log(line, "dim")
            elif kind == "DONE":
                self.busy = False
                self.cat_mood = "happy"
                self.log(line, "ok")
            elif kind == "ERR":
                self.busy = False
                self.cat_mood = "sad"
                self.log(line, "err")
            else:
                self.log(line, "dim")
        else:
            self.log(line, "rx")
            if self.cfg.get("legacy_parsing") and not self.proto_seen:
                upd, done = parse_legacy(line, self.cfg.get("legacy_patterns", {}))
                self.state.update(upd)
                if done:
                    self.busy = False
        self.update_state()

    # ---------------------------------------------------- แสดงผล
    def update_state(self):
        dial_items = []
        for key, (big, zone, d, color) in getattr(self, "state_labels", {}).items():
            v = self.num(key)
            big.config(text="--" if v is None else f"{v:.2f}°")
            z = d.get("zone")
            zone.config(text="⚠ โซนแรงโน้มถ่วง" if (z and v is not None and z[0] <= v <= z[1]) else "")
            dial_items.append((d.get("short", key), v, color, z))
        extras = []
        if self.s_row is not None:
            sc = self.sensor_cfg()
            fresh = self.s_ser is not None and self.s_val is not None and not self.sensor_stale()
            for w in self.s_cmd_widgets:
                w.config(state="normal" if self.s_ser else "disabled")
            if not self.s_ser:
                self.s_val_lbl.config(text="--")
                self.s_diff_lbl.config(text="ยังไม่ได้เชื่อมต่อ", style="Muted.TLabel")
            elif sc.get("zero_ref") and self.s_zero_ref is None:
                # ยังไม่รู้ว่า 0 ของเซนเซอร์ตรงกับมุมไหน แสดงค่าดิบ ไม่วาดบนวงกลม ไม่เทียบกับ A
                self.s_val_lbl.config(text="--" if self.s_val is None else f"{self.s_val:+.1f}°")
                self.s_diff_lbl.config(text=f"ยังไม่รู้จุดศูนย์ กด ตั้งศูนย์ (z) ตอน {sc['zero_ref']} อยู่ที่มุมที่รู้แน่",
                                       style="Warn.TLabel")
            elif not fresh:
                self.s_val_lbl.config(text="--" if self.s_val is None else f"{self.s_val:.1f}°")
                self.s_diff_lbl.config(text="ไม่มีข้อมูลใหม่จากเซนเซอร์", style="Warn.TLabel")
            else:
                self.s_val_lbl.config(text=f"{self.s_val:.1f}°")
                ref_key = sc.get("compare_to", "A")
                ref = self.num(ref_key)
                if ref is None:
                    self.s_diff_lbl.config(text="", style="Muted.TLabel")
                else:
                    diff = self.s_val - ref
                    warn = abs(diff) > float(sc.get("warn_diff", 2))
                    self.s_diff_lbl.config(text=f"ต่างจาก {ref_key}: {diff:+.1f}°",
                                           style="Warn.TLabel" if warn else "Muted.TLabel")
                extras.append((sc.get("short", "S"), self.s_val, PASTEL["sensor"]))
        if hasattr(self, "dial"):
            draw_dial(self.dial, dial_items, self.cfg["dial"], extras)
        for val, d in getattr(self, "extra_labels", []):
            raw = str(self.state.get(d["key"], "--"))
            text = d.get("map", {}).get(raw, raw + d.get("suffix", ""))
            val.config(text=text)
        self.busy_lbl.config(text="กำลังทำงาน..." if self.busy else "ว่าง", style="Warn.TLabel" if self.busy else "TLabel")
        state = "normal" if (self.ser and self.ready and not self.busy) else "disabled"
        for w in self.cmd_widgets:
            w.config(state=state)
        self.raw_btn.config(state=state)
        mood = ("sleep" if not self.ser else "wait" if not self.ready else "busy" if self.busy else self.cat_mood)
        if mood != self._cat_shown:
            draw_cat(self.cat, mood)
            self.cat_lbl.config(text=CAT_TEXT[mood])
            self._cat_shown = mood

    def clear_busy(self):
        self.busy = False
        self.update_state()

    def log(self, text, tag="rx"):
        self.log_box.config(state="normal")
        self.log_box.insert("end", text + "\n", tag)
        if int(self.log_box.index("end-1c").split(".")[0]) > 3000:
            self.log_box.delete("1.0", "500.0")
        self.log_box.see("end")
        self.log_box.config(state="disabled")

    def clear_log(self):
        self.clear_text(self.log_box)

    @staticmethod
    def clear_text(box):
        box.config(state="normal")
        box.delete("1.0", "end")
        box.config(state="disabled")

    def on_close(self):
        self.disconnect()
        self.disconnect_sensor()
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
