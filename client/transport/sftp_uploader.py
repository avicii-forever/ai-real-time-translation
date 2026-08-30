# -*- coding: utf-8 -*-
"""嵌套 SSH + SFTP 上传器:固定路径覆盖上传音频切片。

复用 E:\\ai-dubber\\scripts\\remote.py 的连接模式(网关 direct-tcpip → 节点)。
长连接保活,每块只做 sftp.put 覆盖固定文件名。
"""
import os
import threading
import time

import paramiko

from config import SSH_CONFIG, REMOTE_ASSET_DIR, REMOTE_LIVE_PREFIX


def _load_key(path):
    path = os.path.expanduser(path)
    for cls in (paramiko.RSAKey, paramiko.Ed25519Key, paramiko.ECDSAKey):
        try:
            return cls.from_private_key_file(path)
        except paramiko.SSHException:
            continue
    raise ValueError(f"cannot load private key: {path}")


class SFTPUploader:
    def __init__(self, config=None):
        self.cfg = config or SSH_CONFIG
        self._client = None
        self._gtrans = None
        self._sftp = None
        self._lock = threading.Lock()
        self._next_idx = 1

    # ---- 连接管理 ----
    def connect(self):
        """建立 网关隧道 -> 节点 SSH -> SFTP,长驻。"""
        self.close()
        gw = self.cfg["gateway"]
        node = self.cfg["node"]
        key = _load_key(gw.get("identity_file") or node.get("identity_file"))

        gtrans = paramiko.Transport((gw["host"], gw["port"]))
        gtrans.start_client(timeout=25)
        gtrans.auth_publickey(gw["user"], key)

        channel = gtrans.open_channel(
            "direct-tcpip", (node["host"], node["port"]), ("127.0.0.1", 0)
        )

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        nkey = _load_key(node["identity_file"]) if node.get("identity_file") else key
        client.connect(
            node["host"], port=node["port"], username=node["user"], pkey=nkey,
            sock=channel, look_for_keys=False, allow_agent=False,
            timeout=30, banner_timeout=30, auth_timeout=30,
        )
        sftp = client.open_sftp()
        self._ensure_dir(sftp, REMOTE_ASSET_DIR)

        self._client, self._gtrans, self._sftp = client, gtrans, sftp
        return self

    def _ensure_dir(self, sftp, path):
        # 递归建目录(paramiko 无 mkdir -p)
        parts = path.split("/")
        cur = ""
        for p in parts:
            if not p:
                continue
            cur += "/" + p
            try:
                sftp.stat(cur)
            except IOError:
                try:
                    sftp.mkdir(cur)
                except IOError:
                    pass

    def is_connected(self):
        return self._sftp is not None and self._client is not None and self._client.get_transport() is not None and self._client.get_transport().is_active()

    def close(self):
        for s in (self._sftp, self._client):
            if s is not None:
                try:
                    s.close()
                except Exception:
                    pass
        if self._gtrans is not None:
            try:
                self._gtrans.close()
            except Exception:
                pass
        self._sftp = self._client = self._gtrans = None

    # ---- 上传 ----
    def upload(self, wav_bytes, round_no=None, chunk_no=None):
        """上传一块音频字节,返回服务端 WAV 路径。固定文件名覆盖。"""
        with self._lock:
            if not self.is_connected():
                self.connect()
            idx = chunk_no if chunk_no is not None else self._next_idx
            name = f"{REMOTE_LIVE_PREFIX}_{idx}.wav"
            remote = f"{REMOTE_ASSET_DIR}/{name}"
            with self._sftp.open(remote, "wb") as f:
                f.write(wav_bytes)
            self._next_idx = idx + 1
            return remote

    def reset_idx(self):
        """新轮次从 1 开始编号。"""
        self._next_idx = 1

    # ---- 拉取(读取 TTS 语音等)----
    def listdir(self, remote_dir):
        """列出远端目录下的文件名(wav_*.wav)。"""
        with self._lock:
            if not self.is_connected():
                self.connect()
            try:
                return [f.filename for f in self._sftp.listdir_attr(remote_dir)]
            except IOError:
                return []

    def pull_file(self, remote, local):
        """拉单个远端文件到本地。"""
        with self._lock:
            if not self.is_connected():
                self.connect()
            self._sftp.get(remote, local)
            return local

    def rmtree(self, remote_dir):
        """删除远端目录树(递归)。"""
        with self._lock:
            if not self.is_connected():
                self.connect()
            self._sftp_rmtree(self._sftp, remote_dir)

    def mkdirs(self, remote_dir):
        """递归创建远端目录。"""
        with self._lock:
            if not self.is_connected():
                self.connect()
            self._ensure_dir(self._sftp, remote_dir)

    def _sftp_rmtree(self, sftp, path):
        # paramiko 无 shutil.rmtree,手动递归删
        try:
            for f in sftp.listdir_attr(path):
                p = path + "/" + f.filename
                import stat as statmod
                if statmod.S_ISDIR(f.st_mode):
                    self._sftp_rmtree(sftp, p)
                else:
                    sftp.remove(p)
            sftp.rmdir(path)
        except IOError:
            pass
