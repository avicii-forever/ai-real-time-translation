#!/bin/bash
# 重启 llama-omni-server(新节点 <SSH别名>)
# 释放模型占用(19.7GB 显存/内存)并重新加载。
#
# 用法(在远端节点执行):
#   ssh <SSH别名> "bash /workspace/llama.cpp-omni/restart_backend.sh"
# 或本机:
#   ssh <SSH别名> "bash -s" < scripts/restart_backend.sh
#
# 注意:本脚本用 setsid 完全脱离会话启动服务,避免 pkill 时
#       SSH 连接被误杀导致服务起不来(此前多次踩坑)。
set -e

cd /workspace/llama.cpp-omni

# 1. 停旧服务(只杀 llama-omni-server,不匹配 ssh/当前会话)
pkill -x llama-omni-server 2>/dev/null || true
pkill -f "build/bin/llama-omni-server" 2>/dev/null || true
sleep 3

# 2. 用 setsid 完全脱离当前会话启动,服务不随 SSH 断开而死
setsid nohup ./start_server.sh > server.log 2>&1 < /dev/null &

# 3. 等待健康检查
for i in $(seq 1 15); do
    sleep 2
    if curl -s -m 3 http://127.0.0.1:28099/health 2>/dev/null | grep -q '"status":"ok"'; then
        echo "server restarted OK (pid $(pgrep -f llama-omni-server | head -1))"
        exit 0
    fi
done
echo "server failed to start in 30s"
exit 1
