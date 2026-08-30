# -*- coding: utf-8 -*-
"""半透明字幕弹窗(类似 QQ 音乐歌词弹窗)。

特性:
  - 无边框、半透明(-alpha)、永远置顶(-topmost)
  - 未锁定时可拖动(按住鼠标拖动)
  - 锁定时**点击穿透**(clicks 落到下一层窗口),不影响底下界面的操作
  - 只显示当前译文(和主界面"译文"区同步)

点击穿透用 Win32 `WS_EX_TRANSPARENT` + `WS_EX_LAYERED` 扩展样式(仅 Windows,
本应用就是 Windows 打包)。锁定/解锁由主界面控制,因为锁定后弹窗本身
收不到鼠标事件,得靠主界面解锁。
"""
import ctypes
import tkinter as tk

# 窗口扩展样式
_GWL_EXSTYLE = -20
_WS_EX_LAYERED = 0x00080000      # 分层窗口(配合 -alpha 半透明)
_WS_EX_TRANSPARENT = 0x00000020  # 点击穿透

_BG = "#0d0d0d"                 # 深色底,衬托半透明效果
_FG = "#ffffff"
_FONT = ("Microsoft YaHei UI", 16, "bold")


class SubtitleOverlay:
    def __init__(self, root):
        self.root = root
        self.win = None
        self.label = None
        self.locked = False
        self._drag_off = (0, 0)

    @property
    def is_open(self):
        return self.win is not None and self.win.winfo_exists()

    # ---- 生命周期 ----
    def open(self):
        if self.is_open:
            return
        self.locked = False
        win = tk.Toplevel(self.root)
        win.overrideredirect(True)          # 无边框
        win.attributes("-topmost", True)    # 永远置顶
        win.attributes("-alpha", 0.78)      # 半透明
        win.configure(bg=_BG)
        # 默认放到屏幕右下角
        sw = win.winfo_screenwidth()
        sh = win.winfo_screenheight()
        win.geometry("+%d+%d" % (sw - 460, sh - 180))

        self.label = tk.Label(win, text="", bg=_BG, fg=_FG, font=_FONT,
                              wraplength=420, justify="left", anchor="w",
                              padx=18, pady=12)
        self.label.pack(fill="both", expand=True)

        # 拖动(未锁定时才收得到鼠标事件)
        for w in (win, self.label):
            w.bind("<Button-1>", self._start_drag)
            w.bind("<B1-Motion>", self._drag)

        self.win = win
        self._apply_click_through()

    def close(self):
        if self.win is not None:
            try:
                self.win.destroy()
            except Exception:
                pass
            self.win = None
            self.label = None
            self.locked = False

    # ---- 数据 ----
    def set_text(self, text):
        if self.is_open and self.label is not None:
            self.label.config(text=text)

    def set_locked(self, locked):
        if not self.is_open:
            return
        if self.locked == locked:
            return
        self.locked = locked
        self._apply_click_through()

    # ---- 点击穿透 ----
    def _apply_click_through(self):
        if not self.is_open:
            return
        hwnd = self._hwnd()
        if not hwnd:
            return
        exstyle = ctypes.windll.user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
        if self.locked:
            exstyle |= _WS_EX_LAYERED | _WS_EX_TRANSPARENT
        else:
            # 只去掉穿透,保留 LAYERED(否则 -alpha 半透明会失效)
            exstyle &= ~_WS_EX_TRANSPARENT
        ctypes.windll.user32.SetWindowLongW(hwnd, _GWL_EXSTYLE, exstyle)

    def _hwnd(self):
        try:
            self.win.update_idletasks()
            # winfo_id 是子窗口句柄,GetParent 拿到真正的顶层 HWND
            return ctypes.windll.user32.GetParent(self.win.winfo_id())
        except Exception:
            return 0

    # ---- 拖动 ----
    def _start_drag(self, e):
        self._drag_off = (e.x_root - self.win.winfo_x(),
                          e.y_root - self.win.winfo_y())

    def _drag(self, e):
        self.win.geometry("+%d+%d" % (e.x_root - self._drag_off[0],
                                      e.y_root - self._drag_off[1]))
