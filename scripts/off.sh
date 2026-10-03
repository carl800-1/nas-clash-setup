#!/bin/bash
# ============================================================
# mihomo 代理开关脚本 —— 关闭
# 用途：停止容器，并撤掉 Docker 守护进程代理配置
#       （重要：不撤掉的话，docker pull 会卡 15 秒超时）
# ============================================================

CLASH_DIR="/volume1/docker/clash"
DOCKER_PROXY_CONF="/etc/systemd/system/docker.service.d/proxy.conf"

echo "=========================================="
echo " 关闭 mihomo 代理"
echo "=========================================="

# 1. 撤掉 Docker 代理配置（关键）
echo "[1/3] 撤掉 Docker 守护进程代理..."
if [ -f "$DOCKER_PROXY_CONF" ]; then
    sudo rm -f "$DOCKER_PROXY_CONF"
    sudo systemctl daemon-reload
    sudo systemctl restart docker
    sleep 6
    echo "  ✅ 已撤掉"
else
    echo "  (无代理配置，跳过)"
fi

# 2. 停止容器
echo "[2/3] 停止容器..."
cd "$CLASH_DIR" || exit 1
docker compose down

# 3. 检查
echo "[3/3] 剩余相关容器："
docker ps --filter "name=mihomo" --format "  {{.Names}}\t{{.Status}}" || true
echo "  (无输出表示已全部停止)"

echo
echo "=========================================="
echo " 代理已关闭"
echo "=========================================="
echo " 如需清理 git 代理配置："
echo "   git config --global --unset http.https://github.com.proxy"
echo "=========================================="
