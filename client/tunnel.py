# -*- coding: utf-8 -*-
"""SSH 隧道管理:自动检测 / 建立 / 关闭到后端 (127.0.0.1:28099) 的隧道。

打包成 exe 后实现"一键连接":启动时若后端端口不通,则调用 Windows 自带
OpenSSH(`ssh.exe`)后台建立 `ssh -N -L 28099:127.0.0.1:28099 <alias>`,
轮询 /health 直到就绪;退出时关闭子进程,不留孤儿。

前提(运行机):
  - 有 C:\\Windows\\System32\\OpenSSH\\ssh.exe(Win10+ 自带)
  - 有 ~/.ssh/config(含 SSH_ALIAS 别名 + 网关 ProxyJump 配置)
  - 有对应私钥,且能访问网关
"""
import os
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.request

from config import BACKEND_BASE, BACKEND_PORT, SSH_ALIAS

_SSH_CANDIDATES = (
    r"C:\Windows\System32\OpenSSH\ssh.exe",
    r"C:\Windows\Sysnative\OpenSSH\ssh.exe",
)


def find_ssh():
    """定位 ssh.exe:先探测系统路径,再走 PATH 兜底。"""
    for p in _SSH_CANDIDATES:
        if os.path.isfile(p):
            return p
    return shutil.which("ssh")


def backend_ready(timeout=3):
    """GET /health,判定后端是否已就绪。"""
    try:
        with urllib.request.urlopen(BACKEND_BASE + "/health", timeout=timeout) as r:
            body = r.read(256).decode("utf-8", "ignore")
            return r.status == 200 and '"status":"ok"' in body.replace(" ", "")
    except Exception:
        return False


class Tunnel:
    """一个 SSH 本地转发隧道。start() 非阻塞,wait_ready() 阻塞轮询。"""

    def __init__(self, alias=None, port=None):
        self.alias = alias or SSH_ALIAS
        self.port = port or BACKEND_PORT
        self.proc = None
        self._err_handle = None
        self._err_path = None
        self._state = "未连接"
        self._monitor_stop = None

    @property
    def state(self):
        return self._state

    # ---- 建立 ----
    def start(self):
        """若后端已就绪则复用;否则起 ssh -N 后台子进程。返回是否进入连接流程。"""
        if backend_ready():
            self._state = "已连接"
            return True

        # 关掉旧的(可能已死)子进程,再起新的
        self._kill_proc()

        ssh = find_ssh()
        if not ssh:
            self._state = "失败:未找到 ssh.exe"
            return False

        # stderr 落临时文件,避免管道缓冲阻塞,也便于失败时读诊断信息
        fd, self._err_path = tempfile.mkstemp(prefix="aitrans_tunnel_", suffix=".log")
        os.close(fd)
        self._err_handle = open(self._err_path, "wb")

        cmd = [
            ssh,
            "-o", "ExitOnForwardFailure=yes",
            "-o", "ServerAliveInterval=30",
            "-o", "ServerAliveCountMax=3",
            "-o", "BatchMode=yes",  # 无交互,失败即退出(避免卡在密码提示)
            "-N",
            "-L", f"{self.port}:127.0.0.1:{self.port}",
            self.alias,
        ]
        try:
            self.proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=self._err_handle,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception as e:
            self._state = f"失败:{e}"
            return False

        self._state = "连接中…"
        return True

    def wait_ready(self, timeout=40):
        """轮询 /health 直到就绪。返回 True/False。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if backend_ready():
                self._state = "已连接"
                return True
            # ssh 子进程提前退出(密钥/配置/网络错误)
            if self.proc is not None and self.proc.poll() is not None:
                err = self._read_err()
                self._state = "失败:" + (err or f"ssh 退出码 {self.proc.returncode}")
                return False
            time.sleep(1)
        self._state = "超时:后端未就绪"
        return False

    # ---- 诊断 ----
    def _read_err(self):
        try:
            if self._err_path and os.path.isfile(self._err_path):
                with open(self._err_path, "r", errors="ignore") as f:
                    lines = [ln.strip() for ln in f if ln.strip()]
                return lines[-1][:200] if lines else ""
        except Exception:
            pass
        return ""

    # ---- 后台保活(配合 WS 断线重连)----
    def monitor(self, interval=5, on_state=None):
        """后台守护线程:后端掉线时自动重建隧道。

        on_state(str):状态变化回调(可选),供 GUI 更新连接状态。
        """
        def _report(s):
            self._state = s
            if on_state:
                on_state(s)

        def _loop():
            while not self._monitor_stop.is_set():
                if not backend_ready():
                    _report("重连中…")
                    try:
                        self.start()
                        self.wait_ready(timeout=40)
                    except Exception as e:
                        _report(f"失败:{e}")
                    else:
                        _report(self._state)
                time.sleep(interval)

        self._monitor_stop = threading.Event()
        threading.Thread(target=_loop, daemon=True).start()

    # ---- 关闭 ----
    def _kill_proc(self):
        if self.proc is not None:
            try:
                self.proc.terminate()
            except Exception:
                pass
            try:
                self.proc.wait(timeout=3)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
        self.proc = None

    def close(self):
        if self._monitor_stop is not None:
            self._monitor_stop.set()
        self._kill_proc()
        if self._err_handle is not None:
            try:
                self._err_handle.close()
            except Exception:
                pass
        if self._err_path and os.path.isfile(self._err_path):
            try:
                os.remove(self._err_path)
            except Exception:
                pass
        self._err_handle = None
        self._err_path = None
        self._state = "未连接"
