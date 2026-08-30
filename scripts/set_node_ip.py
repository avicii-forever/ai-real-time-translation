# -*- coding: utf-8 -*-
"""换节点 IP:一条命令改两处 + 验通。

OpenLibing 容器每次重启都重分配内网 IP(179.x),旧 IP 立刻失效,要同步改:
  1. ~/.ssh/config 里 <SSH别名> 的 HostName
  2. client/config.py 的 SSH_CONFIG["node"]["host"](SFTP 直连用)

⚠️ 控制台"连接实例"生成的命令会把 IP 塞进 -p,ssh 会报 `Bad port '<ip>'`。
   看到 Bad port 就是这个,不是密码/网络问题 —— 用本脚本改完走别名直连。

用法:
    python scripts/set_node_ip.py <节点IP>        # 改 + 验
    python scripts/set_node_ip.py <节点IP> --dry  # 只看要改什么
    python scripts/set_node_ip.py --check             # 不改,只测当前配置通不通
"""
import argparse
import ipaddress
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SSH_CONFIG = Path.home() / ".ssh" / "config"
CLIENT_CONFIG = ROOT / "client" / "config.py"
ALIAS = "<SSH别名>"


def current_ips():
    """返回 (ssh_config 里的 HostName, client/config.py 里的 node host)。"""
    ssh_ip = node_ip = None
    if SSH_CONFIG.exists():
        text = SSH_CONFIG.read_text(encoding="utf-8", errors="replace")
        m = re.search(rf"^Host\s+{re.escape(ALIAS)}\s*$(.*?)(?=^Host\s|\Z)",
                      text, re.M | re.S)
        if m:
            h = re.search(r"^\s*HostName\s+(\S+)", m.group(1), re.M)
            ssh_ip = h.group(1) if h else None
    text = CLIENT_CONFIG.read_text(encoding="utf-8")
    m = re.search(r'"node":\s*\{.*?"host":\s*"([^"]+)"', text, re.S)
    node_ip = m.group(1) if m else None
    return ssh_ip, node_ip


def patch_ssh_config(new_ip, dry=False):
    text = SSH_CONFIG.read_text(encoding="utf-8", errors="replace")
    m = re.search(rf"^Host\s+{re.escape(ALIAS)}\s*$(.*?)(?=^Host\s|\Z)",
                  text, re.M | re.S)
    if not m:
        print(f"  ✗ ~/.ssh/config 里找不到 `Host {ALIAS}`,请手工加")
        return False
    block = m.group(1)
    new_block, n = re.subn(r"(^\s*HostName\s+)\S+", rf"\g<1>{new_ip}",
                           block, count=1, flags=re.M)
    if not n:
        print("  ✗ 该 Host 段里没有 HostName 行")
        return False
    if not dry:
        SSH_CONFIG.write_text(text[:m.start(1)] + new_block + text[m.end(1):],
                              encoding="utf-8")
    print(f"  ✓ ~/.ssh/config  Host {ALIAS} -> HostName {new_ip}")
    return True


def patch_client_config(new_ip, dry=False):
    text = CLIENT_CONFIG.read_text(encoding="utf-8")
    m = re.search(r'("node":\s*\{.*?"host":\s*")([^"]+)(")', text, re.S)
    if not m:
        print("  ✗ client/config.py 里找不到 SSH_CONFIG[\"node\"][\"host\"]")
        return False
    if not dry:
        CLIENT_CONFIG.write_text(
            text[:m.start(2)] + new_ip + text[m.end(2):], encoding="utf-8")
    print(f'  ✓ client/config.py  SSH_CONFIG["node"]["host"] -> {new_ip}')
    return True


def check(timeout=15):
    """走别名连一次,顺便看后端进程在不在。"""
    print(f"验证 ssh {ALIAS} ...")
    try:
        r = subprocess.run(
            ["ssh", "-o", f"ConnectTimeout={timeout}", "-o", "BatchMode=yes",
             ALIAS, "echo NODE_OK; pgrep -c llama-omni-server || echo 0"],
            capture_output=True, text=True, timeout=timeout + 20,
            encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        print("  ✗ 连接超时")
        return False
    out = (r.stdout or "").strip()
    if "NODE_OK" not in out:
        print(f"  ✗ 连不上:{(r.stderr or out).strip().splitlines()[-1:] or ''}")
        return False
    procs = out.splitlines()[-1].strip()
    print(f"  ✓ 节点可达;llama-omni-server 进程数 = {procs}")
    if procs == "0":
        print("    → 服务没起,跑 scripts/restart_backend.sh")
    else:
        print(f"    → 建隧道:ssh -N -L 28099:127.0.0.1:28099 {ALIAS}")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ip", nargs="?", help="新的节点 IP(179.x.x.x)")
    ap.add_argument("--dry", action="store_true", help="只打印不改")
    ap.add_argument("--check", action="store_true", help="不改,只验证当前配置")
    args = ap.parse_args()

    ssh_ip, node_ip = current_ips()
    print(f"当前:~/.ssh/config = {ssh_ip} | client/config.py = {node_ip}")

    if args.check or not args.ip:
        if not args.ip:
            print("\n(没给新 IP,只做连通性检查;要改传 IP:"
                  " python scripts/set_node_ip.py 179.x.x.x)")
        return 0 if check() else 1

    try:
        ipaddress.ip_address(args.ip)
    except ValueError:
        print(f"✗ `{args.ip}` 不是合法 IP")
        return 2
    if ssh_ip == args.ip and node_ip == args.ip:
        print("两处都已经是这个 IP,跳过修改")
    else:
        print(f"\n改成 {args.ip}{'(dry-run)' if args.dry else ''}:")
        ok = patch_ssh_config(args.ip, args.dry) & patch_client_config(args.ip, args.dry)
        if not ok:
            return 2
    if args.dry:
        return 0
    print()
    return 0 if check() else 1


if __name__ == "__main__":
    sys.exit(main())
