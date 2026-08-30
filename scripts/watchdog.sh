#!/bin/bash
# llama-omni-server 服务看门狗
#
# 定期检测 /health,连续失败则调用 restart_backend.sh 重启服务。
# 设计要点:
#   - 连续 FAIL_THRESHOLD 次失败才重启(抗单次网络抖动)
#   - RESTART_COOLDOWN 秒内不重复重启(防"起不来→疯狂重启"风暴)
#   - 所有输出落到 watchdog.log
#
# 启动(脱离会话,日志落 watchdog.log):
#   cd /workspace/llama.cpp-omni
#   setsid nohup bash watchdog.sh > watchdog.log 2>&1 < /dev/null &
#
# 停止:
#   pkill -f "bash watchdog.sh"
#
# 注意:本容器 PID1=tail(无 systemd/cron),看门狗是后台死循环,
#       容器重启后需要重新拉起(见启动命令)。

CHECK_INTERVAL=30      # 检测间隔(秒)
FAIL_THRESHOLD=2       # 连续失败 N 次才重启
RESTART_COOLDOWN=180   # 两次重启最小间隔(秒)
HEALTH_URL="http://127.0.0.1:28099/health"

cd /workspace/llama.cpp-omni || exit 1

log() { echo "$(date '+%F %T') $*"; }

fails=0
last_restart=0

log "watchdog started (interval=${CHECK_INTERVAL}s threshold=${FAIL_THRESHOLD} cooldown=${RESTART_COOLDOWN}s)"

while true; do
    if curl -s -m 5 "$HEALTH_URL" 2>/dev/null | grep -q '"status":"ok"'; then
        if [ "$fails" -ge "$FAIL_THRESHOLD" ]; then
            log "health recovered"
        fi
        fails=0
    else
        fails=$((fails + 1))
        log "health check failed ($fails/$FAIL_THRESHOLD)"
        if [ "$fails" -ge "$FAIL_THRESHOLD" ]; then
            now=$(date +%s)
            elapsed=$((now - last_restart))
            if [ "$elapsed" -ge "$RESTART_COOLDOWN" ]; then
                log "restarting backend (last restart ${elapsed}s ago)..."
                bash restart_backend.sh
                rc=$?
                log "restart done (rc=$rc)"
                last_restart=$(date +%s)
                fails=0
            else
                log "cooldown active (${elapsed}s < ${RESTART_COOLDOWN}s), skip restart"
                fails=0
            fi
        fi
    fi
    sleep "$CHECK_INTERVAL"
done
