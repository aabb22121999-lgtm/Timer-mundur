"""
Timer Mundur Overlay
--------------------
Timer hitung mundur dengan latar transparan, selalu tampil di paling depan
(di atas aplikasi lain, termasuk pemutar video mode jendela / borderless).

Cara pakai:
    python timer.py            -> default 5 menit
    python timer.py 10:30      -> 10 menit 30 detik
    python timer.py 90s        -> 90 detik

Kontrol:
    Klik kiri + geser   : pindahkan timer
    Scroll mouse        : perbesar / perkecil ukuran
    Ctrl + / Ctrl -     : perbesar / perkecil ukuran
    Klik 2x             : atur waktu
    Spasi               : mulai / jeda
    R                   : reset
    Klik kanan          : menu lengkap
    Esc                 : keluar
"""

import json
import math
import os
import platform
import re
import sys
import time
import tkinter as tk
import tkinter.font as tkfont
from tkinter import simpledialog

SETTINGS_FILE = os.path.join(os.path.expanduser("~"), ".timer_overlay.json")
SYSTEM = platform.system()


def parse_time(text):
    """Ubah teks menjadi detik. Contoh: '5:30', '1:05:00', '90s', '10m', '2m30s', '5'."""
    s = text.strip().lower().replace(" ", "")
    if not s:
        return None

    m = re.fullmatch(r"(\d+):(\d+)(?::(\d+))?", s)
    if m:
        a, b, c = m.groups()
        if c is None:  # mm:ss
            return int(a) * 60 + int(b)
        return int(a) * 3600 + int(b) * 60 + int(c)  # hh:mm:ss

    m = re.fullmatch(r"(?:(\d+)(?:j|h))?(?:(\d+)(?:m|menit))?(?:(\d+)(?:s|d|detik))?", s)
    if m and any(m.groups()):
        h, mi, se = (int(x) if x else 0 for x in m.groups())
        return h * 3600 + mi * 60 + se

    if s.isdigit():  # angka saja = menit
        return int(s) * 60
    return None


def load_settings():
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_settings(data):
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception:
        pass


class OverlayTimer:
    COLORS = {
        "Putih": "#FFFFFF",
        "Kuning": "#FFE600",
        "Hijau": "#3CFF6B",
        "Cyan": "#40E0FF",
        "Merah": "#FF4B4B",
    }

    def __init__(self, start_seconds):
        cfg = load_settings()
        self.total = start_seconds if start_seconds is not None else cfg.get("total", 300)
        self.remaining = float(self.total)
        self.running = False
        self.end_at = 0.0
        self.font_size = cfg.get("font_size", 48)
        self.color = cfg.get("color", "#FFFFFF")
        self.text_color = self.color
        self.opacity = min(1.0, max(0.3, cfg.get("opacity", 0.8)))
        self.flashing = False
        self._layout_key = None
        self._text_id = None

        self.root = tk.Tk()
        self.root.title("Timer")
        self.root.overrideredirect(True)  # tanpa bingkai
        self.root.attributes("-topmost", True)

        # --- latar transparan ---
        if SYSTEM == "Windows":
            self.bg = "#010101"
            self.root.config(bg=self.bg)
            self.root.wm_attributes("-transparentcolor", self.bg)
        elif SYSTEM == "Darwin":
            self.bg = "systemTransparent"
            self.root.config(bg=self.bg)
            self.root.wm_attributes("-transparent", True)
        else:  # Linux: butuh compositor untuk transparansi
            self.bg = "#000000"
            self.root.config(bg=self.bg)
        self.apply_opacity()  # transparansi panel hitam (berlaku di semua OS)

        families = ("Segoe UI Light", "Helvetica Neue Light", "SF Pro Display", "Helvetica")
        self.font_family = "Helvetica"
        try:
            available = set(tkfont.families())
            for fam in families:
                if fam in available:
                    self.font_family = fam
                    break
        except Exception:
            pass

        self.font = tkfont.Font(family=self.font_family, size=self.font_size)
        self.canvas = tk.Canvas(self.root, bg=self.bg, highlightthickness=0, bd=0)
        self.canvas.pack()

        pos = cfg.get("pos")
        if pos:
            self.root.geometry(f"+{pos[0]}+{pos[1]}")
        else:
            self.root.geometry("+80+80")

        self._build_menu()
        self._bind_events()
        self.refresh_font()
        self.update_display()
        self.root.after(15, self.tick)
        self.root.after(1000, self.keep_on_top)

    # ---------- UI ----------
    def _build_menu(self):
        self.menu = tk.Menu(self.root, tearoff=0)
        self.menu.add_command(label="Mulai / Jeda   (Spasi)", command=self.toggle)
        self.menu.add_command(label="Reset   (R)", command=self.reset)
        self.menu.add_command(label="Atur waktu…   (klik 2x)", command=self.ask_time)
        self.menu.add_separator()
        self.menu.add_command(label="Perbesar   (Ctrl +)", command=lambda: self.change_size(+6))
        self.menu.add_command(label="Perkecil   (Ctrl -)", command=lambda: self.change_size(-6))
        color_menu = tk.Menu(self.menu, tearoff=0)
        for name, hexv in self.COLORS.items():
            color_menu.add_command(label=name, command=lambda h=hexv: self.set_color(h))
        self.menu.add_cascade(label="Warna teks", menu=color_menu)
        opacity_menu = tk.Menu(self.menu, tearoff=0)
        for label, val in (("40%", 0.4), ("55%", 0.55), ("70%", 0.7), ("85%", 0.85), ("100% (pekat)", 1.0)):
            opacity_menu.add_command(label=label, command=lambda v=val: self.set_opacity(v))
        self.menu.add_cascade(label="Kepekatan latar", menu=opacity_menu)
        preset = tk.Menu(self.menu, tearoff=0)
        for label, sec in (("30 detik", 30), ("1 menit", 60), ("5 menit", 300),
                           ("10 menit", 600), ("25 menit", 1500), ("1 jam", 3600)):
            preset.add_command(label=label, command=lambda s=sec: self.set_time(s))
        self.menu.add_cascade(label="Preset waktu", menu=preset)
        self.menu.add_separator()
        self.menu.add_command(label="Keluar   (Esc)", command=self.quit)

    def _bind_events(self):
        r = self.root
        for w in (r, self.canvas):
            w.bind("<ButtonPress-1>", self.on_press)
            w.bind("<B1-Motion>", self.on_drag)
            w.bind("<Double-Button-1>", lambda e: self.ask_time())
            w.bind("<MouseWheel>", self.on_wheel)
            w.bind("<Button-4>", lambda e: self.change_size(+4))  # Linux
            w.bind("<Button-5>", lambda e: self.change_size(-4))  # Linux
            w.bind("<Button-3>", self.show_menu)
            if SYSTEM == "Darwin":
                w.bind("<Button-2>", self.show_menu)
        r.bind("<space>", lambda e: self.toggle())
        r.bind("<r>", lambda e: self.reset())
        r.bind("<R>", lambda e: self.reset())
        r.bind("<Escape>", lambda e: self.quit())
        r.bind("<Control-plus>", lambda e: self.change_size(+6))
        r.bind("<Control-equal>", lambda e: self.change_size(+6))
        r.bind("<Control-minus>", lambda e: self.change_size(-6))
        r.protocol("WM_DELETE_WINDOW", self.quit)

    def on_press(self, e):
        self._dx, self._dy = e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y()
        self.root.focus_force()

    def on_drag(self, e):
        self.root.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")

    def on_wheel(self, e):
        step = 4 if abs(e.delta) < 120 else 6
        self.change_size(step if e.delta > 0 else -step)

    def show_menu(self, e):
        try:
            self.menu.tk_popup(e.x_root, e.y_root)
        finally:
            self.menu.grab_release()

    def keep_on_top(self):
        self.root.attributes("-topmost", True)
        self.root.lift()
        self.root.after(1000, self.keep_on_top)

    # ---------- tampilan ----------
    def change_size(self, delta):
        self.font_size = max(10, min(400, self.font_size + delta))
        self.refresh_font()

    def refresh_font(self):
        self.font.configure(size=self.font_size)
        self._layout_key = None  # paksa gambar ulang panel
        self.update_display()

    def set_color(self, hexv):
        self.color = hexv
        self.set_text_color(hexv)

    def set_text_color(self, hexv):
        self.text_color = hexv
        if self._text_id is not None:
            self.canvas.itemconfig(self._text_id, fill=hexv)

    def apply_opacity(self):
        try:
            self.root.attributes("-alpha", self.opacity)
        except tk.TclError:
            pass

    def set_opacity(self, value):
        self.opacity = value
        self.apply_opacity()

    def _rrect(self, x1, y1, x2, y2, r, **kw):
        """Persegi panjang dengan sudut membulat."""
        r = max(0, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
        pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
               x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
               x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
        return self.canvas.create_polygon(pts, smooth=True, **kw)

    def layout(self, text):
        """Gambar panel hitam + bayangan lembut; hanya diulang jika ukuran berubah."""
        key = (len(text), self.font_size)
        if key == self._layout_key:
            return
        self._layout_key = key

        ref = "".join("8" if ch.isdigit() else ch for ch in text)  # lebar tetap, tidak goyang
        tw = self.font.measure(ref)
        th = self.font.metrics("linespace")
        sh = max(5, int(self.font_size * 0.16))   # tebal bayangan
        dy = max(2, sh // 3)                       # bayangan sedikit turun ke bawah
        px = int(self.font_size * 0.40)
        py = int(self.font_size * 0.12)
        radius = int(self.font_size * 0.22)

        W = tw + 2 * px + 2 * sh
        H = th + 2 * py + 2 * sh + dy
        x1, y1, x2, y2 = sh, sh, W - sh, H - sh - dy

        c = self.canvas
        c.delete("all")
        c.config(width=W, height=H)

        # bayangan: lapisan luar lebih terang -> memudar keluar (hindari #010101 = warna kunci transparan)
        for i in range(sh, 0, -1):
            g = 2 + int(0x20 * i / sh)
            self._rrect(x1 - i, y1 - i + dy, x2 + i, y2 + i + dy, radius + i,
                        fill=f"#{g:02x}{g:02x}{g:02x}", outline="")
        # panel hitam
        self._rrect(x1, y1, x2, y2, radius, fill="#000000", outline="")
        self._text_id = c.create_text((x1 + x2) / 2, (y1 + y2) / 2, text=text,
                                      font=self.font, fill=self.text_color, anchor="center")

    def update_display(self):
        t = max(self.remaining, 0.0)
        cs_total = int(t * 100)
        m, s, cs = cs_total // 6000, (cs_total // 100) % 60, cs_total % 100
        text = f"{m:02d}:{s:02d}.{cs:02d}"
        self.layout(text)
        self.canvas.itemconfig(self._text_id, text=text)

    # ---------- logika timer ----------
    def tick(self):
        if self.running:
            self.remaining = self.end_at - time.perf_counter()
            if self.remaining <= 0:
                self.remaining = 0
                self.running = False
                self.update_display()
                self.finished()
            else:
                self.update_display()
        self.root.after(15, self.tick)

    def toggle(self):
        if self.flashing:
            return
        if self.running:
            self.running = False
        else:
            if self.remaining <= 0:
                self.remaining = float(self.total)
            self.end_at = time.perf_counter() + self.remaining
            self.running = True

    def reset(self):
        self.running = False
        self.flashing = False
        self.remaining = float(self.total)
        self.set_text_color(self.color)
        self.update_display()

    def set_time(self, seconds):
        self.total = max(1, int(seconds))
        self.reset()

    def ask_time(self):
        was_running = self.running
        self.running = False
        remaining_backup = self.remaining
        answer = simpledialog.askstring(
            "Atur waktu",
            "Masukkan waktu:\n"
            "  5:30   = 5 menit 30 detik\n"
            "  90s    = 90 detik\n"
            "  10m    = 10 menit\n"
            "  1:00:00 = 1 jam\n"
            "  5      = 5 menit",
            parent=self.root,
        )
        if answer is not None:
            sec = parse_time(answer)
            if sec and sec > 0:
                self.set_time(sec)
                return
        # batal / tidak valid -> lanjutkan seperti semula
        self.remaining = remaining_backup
        if was_running:
            self.end_at = time.perf_counter() + self.remaining
            self.running = True

    def finished(self):
        self.flashing = True
        self.beep()
        self.flash(0)

    def flash(self, n):
        if not self.flashing:
            return
        if n >= 24:  # ~12 detik lalu berhenti berkedip
            self.flashing = False
            self.set_text_color("#FF4B4B")
            return
        self.set_text_color("#FF4B4B" if n % 2 == 0 else self.color)
        if n % 6 == 0:
            self.beep()
        self.root.after(500, lambda: self.flash(n + 1))

    def beep(self):
        try:
            if SYSTEM == "Windows":
                import winsound
                winsound.Beep(1000, 250)
            else:
                self.root.bell()
        except Exception:
            pass

    def quit(self):
        save_settings({
            "total": self.total,
            "font_size": self.font_size,
            "color": self.color,
            "opacity": self.opacity,
            "pos": [self.root.winfo_x(), self.root.winfo_y()],
        })
        self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    start = parse_time(sys.argv[1]) if len(sys.argv) > 1 else None
    OverlayTimer(start).run()
