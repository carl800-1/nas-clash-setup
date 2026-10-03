#!/bin/bash
# ============================================================
# mihomo 代理开关脚本 —— 开启
# 用途：按需启动 mihomo，并让 Docker 守护进程也走代理
# ============================================================

CLASH_DIR="${CLASH_DIR:-/volume1/docker/clash}"
PROXY_PORT="7890"
PROXY_ADDR="192.168.3.3:${PROXY_PORT}"
DOCKER_PROXY_CONF="/etc/systemd/system/docker.service.d/proxy.conf"

echo "=========================================="
echo " 开启 mihomo 代理"
echo "=========================================="

# 1. 启动容器
cd "$CLASH_DIR" || exit 1
echo "[1/4] 启动容器..."
docker compose up -d

sleep 4

# 2. 检查状态
echo "[2/4] 容器状态："
docker ps --filter "name=mihomo" --format "  {{.Names}}\t{{.Status}}"

# 3. 配置 Docker 守护进程代理（让 docker pull 也能走代理）
echo "[3/4] 配置 Docker 守护进程代理..."
sudo mkdir -p "$(dirname "$DOCKER_PROXY_CONF")"
sudo tee "$DOCKER_PROXY_CONF" >/dev/null <<EOF
[Service]
Environment="HTTP_PROXY=http://${PROXY_ADDR}"
Environment="HTTPS_PROXY=http://${PROXY_ADDR}"
Environment="NO_PROXY=localhost,127.0.0.1,192.168.3.0/24"
EOF
sudo systemctl daemon-reload
sudo systemctl restart docker
sleep 6

# 4. 验证
echo "[4/4] 验证..."
if curl -s -x "http://127.0.0.1:${PROXY_PORT}" -o /dev/null -w "%{http_code}" \
   --max-time 10 http://www.baidu.com 2>/dev/null | grep -qE "200|301|302"; then
    echo "  ✅ 代理端口工作正常"
else
    echo "  ⚠️  代理不可用，请检查 config.yaml 里的节点是否已填"
fi

echo
echo "=========================================="
echo " 代理已开启"
echo "=========================================="
echo " HTTP 代理 : http://${PROXY_ADDR}"
echo " SOCKS5    : 192.168.3.3:7891"
echo " 网页面板  : http://192.168.3.3:9090/ui/"
echo
echo " 常用命令："
echo "   git 走代理 : git config --global http.https://github.com.proxy http://${PROXY_ADDR}"
echo "   关闭代理   : ./off.sh"
echo "=========================================="
