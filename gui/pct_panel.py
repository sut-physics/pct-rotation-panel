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

        style = ttk.Style(self)
        style.configure("Big.TLabel", font=("TkDefaultFont", 22, "bold"))
        style.configure("Head.TLabel", font=("TkDefaultFont", 9, "bold"))
        style.configure("Warn.TLabel", foreground="#b26a00")
        style.configure("Ok.TLabel", foreground="#1f8a4c")

        self.build()
        self.load_configs(first=True)
        self.refresh_ports()
        self.after(50, self.poll)
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        if serial is None:
            messagebox.showerror("ไม่พบ pyserial", "ยังไม่ได้ติดตั้ง pyserial\nรัน install.sh (Ubuntu) หรือ install.bat (Windows) ก่อน")

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
        if self.cfg.get("show_raw_terminal", True):
            self.raw_frame.pack(fill="x", pady=(8, 0))
        self.update_state()

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

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, padx=10, pady=6)
        body.columnconfigure(1, weight=1)
        body.columnconfigure(2, weight=1)
        body.rowconfigure(0, weight=1)

        self.left = ttk.LabelFrame(body, text="ตำแหน่งปัจจุบัน (ค่าที่เฟิร์มแวร์รายงาน)")
        self.left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        self.state_frame = ttk.Frame(self.left)
        self.state_frame.pack(fill="x")
        self.busy_lbl = ttk.Label(self.left)
        self.busy_lbl.pack(anchor="w", padx=10, pady=(6, 0))
        ttk.Button(self.left, text="ล้างสถานะ 'กำลังหมุน'", command=self.clear_busy).pack(anchor="w", padx=10, pady=6)
        self.info_lbl = ttk.Label(self.left, justify="left", foreground="gray40", wraplength=230)
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

        right = ttk.LabelFrame(body, text="Serial log")
        right.grid(row=0, column=2, sticky="nsew")
        right.rowconfigure(0, weight=1)
        right.columnconfigure(0, weight=1)
        self.log_box = tk.Text(right, width=44, height=20, state="disabled", wrap="word",
                               bg="#14191d", fg="#cfd8dd", font="TkFixedFont", insertbackground="white")
        self.log_box.grid(row=0, column=0, sticky="nsew", padx=(6, 0), pady=6)
        sb = ttk.Scrollbar(right, command=self.log_box.yview)
        sb.grid(row=0, column=1, sticky="ns", pady=6)
        self.log_box["yscrollcommand"] = sb.set
        for tag, color in (("tx", "#7fd6db"), ("rx", "#cfd8dd"), ("ok", "#7ddc9d"), ("err", "#ff8a80"),
                           ("warn", "#f0b24a"), ("dim", "#7c8a93")):
            self.log_box.tag_config(tag, foreground=color)
        ttk.Button(right, text="ล้าง log", command=self.clear_log).grid(row=1, column=0, sticky="w", padx=6, pady=(0, 6))

    def build_state_panel(self):
        for w in self.state_frame.winfo_children():
            w.destroy()
        self.state_labels = {}
        for d in self.cfg["state_display"]:
            ttk.Label(self.state_frame, text=d["label"], style="Head.TLabel").pack(anchor="w", padx=10, pady=(10, 0))
            big = ttk.Label(self.state_frame, style="Big.TLabel")
            big.pack(anchor="w", padx=10)
            zone = ttk.Label(self.state_frame, style="Warn.TLabel")
            zone.pack(anchor="w", padx=10)
            self.state_labels[d["key"]] = (big, zone, d)
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
                nb = ttk.Button(nrow, text=f"{step:+g}", width=5, command=lambda s=step: self.run_nudge(item, var, nudge, s))
                nb.pack(side="left", padx=2)
                self.cmd_widgets.append(nb)
        prev = item.get("preview")
        if prev:
            lbl = ttk.Label(parent, foreground="gray40")
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
        self.stop_reader.clear()
        self.reader = threading.Thread(target=self.read_loop, daemon=True)
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

    def read_loop(self):
        buf = b""
        while not self.stop_reader.is_set():
            try:
                chunk = self.ser.read(256)
            except Exception as e:  # noqa: BLE001  สายหลุด
                self.rx.put(("error", str(e)))
                return
            if not chunk:
                continue
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                self.rx.put(("line", line.decode("utf-8", errors="replace").rstrip("\r")))

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
        try:
            while True:
                kind, payload = self.rx.get_nowait()
                if kind == "error":
                    self.log(f"พอร์ตหลุด: {payload}", "err")
                    self.disconnect()
                else:
                    self.handle_line(payload)
        except queue.Empty:
            pass
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
                self.log(line, "ok")
            elif kind == "ERR":
                self.busy = False
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
        for key, (big, zone, d) in getattr(self, "state_labels", {}).items():
            v = self.num(key)
            big.config(text="--" if v is None else f"{v:.2f}°")
            z = d.get("zone")
            zone.config(text="อยู่ในโซนแรงโน้มถ่วง" if (z and v is not None and z[0] <= v <= z[1]) else "")
        for val, d in getattr(self, "extra_labels", []):
            raw = str(self.state.get(d["key"], "--"))
            text = d.get("map", {}).get(raw, raw + d.get("suffix", ""))
            val.config(text=text)
        self.busy_lbl.config(text="กำลังทำงาน..." if self.busy else "ว่าง", style="Warn.TLabel" if self.busy else "TLabel")
        state = "normal" if (self.ser and self.ready and not self.busy) else "disabled"
        for w in self.cmd_widgets:
            w.config(state=state)
        self.raw_btn.config(state=state)

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
        self.log_box.config(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.config(state="disabled")

    def on_close(self):
        self.disconnect()
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
