#!/bin/bash
# ============================================================
# update-sub.sh —— 从机场订阅自动更新节点（保留本地骨架）
# ============================================================
# 用法：
#   ./update-sub.sh                        # 读取 sub.txt 里的订阅链接
#   ./update-sub.sh "订阅链接"              # 临时指定链接
#
# 原理：
#   采用 mihomo 的 proxy-providers 机制 —— 订阅内容存为独立文件，
#   config.yaml 通过 provider 引用。这样既能自动更新节点，
#   又不会覆盖本地的端口/DNS/规则配置。
#
# 产物：
#   config/providers/airport.yaml   订阅节点
#   config/config.yaml              已写入 provider 引用
# ============================================================

set -e

# 部署目录可用环境变量覆盖（便于在测试目录中验证，不影响生产环境）
CLASH_DIR="${CLASH_DIR:-/volume1/docker/clash}"
CONFIG_DIR="$CLASH_DIR/config"
CONFIG="$CONFIG_DIR/config.yaml"
PROVIDER_DIR="$CONFIG_DIR/providers"
PROVIDER_FILE="$PROVIDER_DIR/airport.yaml"
SUB_FILE="$CLASH_DIR/sub.txt"
IMAGE="docker.1ms.run/metacubex/mihomo:latest"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}[✓]${NC} $1"; }
warn()  { echo -e "${YELLOW}[!]${NC} $1"; }
error() { echo -e "${RED}[✗]${NC} $1"; exit 1; }

# ---------- 1. 获取订阅链接 ----------
SUB_URL="$1"
if [ -z "$SUB_URL" ] && [ -f "$SUB_FILE" ]; then
    SUB_URL=$(grep -v '^#' "$SUB_FILE" | grep -v '^$' | head -1 | tr -d '\r\n ')
fi

[ -z "$SUB_URL" ] && {
    error "未提供订阅链接"
    echo "  方式一：./update-sub.sh \"https://机场订阅链接\""
    echo "  方式二：echo \"链接\" > $SUB_FILE && ./update-sub.sh"
}

info "订阅地址：${SUB_URL:0:60}..."

# ---------- 2. 下载订阅 ----------
mkdir -p "$PROVIDER_DIR"
TMP="/tmp/clash-sub-$$.yaml"

echo "  下载中..."
curl -fsSL --max-time 30 \
     -A "clash-verge/v1.0" \
     -o "$TMP" "$SUB_URL" || error "订阅下载失败（网络不通或链接失效）"

SIZE=$(wc -c < "$TMP")
info "下载完成：${SIZE} 字节"
[ "$SIZE" -lt 100 ] && { head -c 300 "$TMP"; error "订阅内容过小"; }

# ---------- 3. 格式检查 ----------
if grep -qE '^\s*proxies:' "$TMP"; then
    info "格式识别：Clash YAML ✓"
else
    if base64 -d "$TMP" 2>/dev/null | grep -qE '(ss|vmess|trojan|vless|hysteria2?)://'; then
        rm -f "$TMP"
        error "订阅是通用 Base64 格式，不是 Clash 格式 —— 请在机场后台切换订阅类型为 Clash / Clash Meta"
    fi
    echo "--- 前 200 字节 ---"; head -c 200 "$TMP"; echo
    rm -f "$TMP"
    error "无法识别的订阅格式"
fi

NODES=$(grep -cE '^\s*-\s*(\{)?\s*name:' "$TMP" || echo 0)
info "节点数量：$NODES"
[ "$NODES" -lt 1 ] && { rm -f "$TMP"; error "订阅里没有解析到节点"; }

# ---------- 4. 落盘为 provider 文件 ----------
cp "$TMP" "$PROVIDER_FILE"
rm -f "$TMP"
info "节点已写入 providers/airport.yaml"

# ---------- 5. 生成 config.yaml（保留本地骨架 + 引用 provider） ----------
if grep -q "proxy-providers:" "$CONFIG" 2>/dev/null; then
    info "config.yaml 已含 proxy-providers，跳过写入"
else
    [ -f "$CONFIG" ] && cp "$CONFIG" "$CONFIG.bak.$(date +%Y%m%d-%H%M%S)"

    cat > "$CONFIG" <<'YAML'
mixed-port: 7890
allow-lan: true
bind-address: "*"
mode: rule
log-level: info
ipv6: false
unified-delay: true
tcp-concurrent: true

external-controller: 0.0.0.0:9090
external-ui: ui
secret: ""

dns:
  enable: true
  listen: 0.0.0.0:1053
  ipv6: false
  enhanced-mode: fake-ip
  fake-ip-range: 198.18.0.1/16
  fake-ip-filter:
    - "*.lan"
    - "*.local"
    - "*.ugreen.com"
  default-nameserver:
    - 223.5.5.5
    - 119.29.29.29
  nameserver:
    - https://1.1.1.1/dns-query
    - https://dns.alidns.com/dns-query
    - https://doh.pub/dns-query
  fallback:
    - https://1.1.1.1/dns-query
    - https://8.8.8.8/dns-query
  fallback-filter:
    geoip: true
    geoip-code: CN

proxy-providers:
  airport:
    type: http
    url: "SUB_URL_PLACEHOLDER"
    interval: 3600
    path: ./providers/airport.yaml
    health-check:
      enable: true
      url: http://www.gstatic.com/generate_204
      interval: 300

proxy-groups:
  - name: "🚀 节点选择"
    type: select
    use:
      - airport
    proxies:
      - DIRECT
  - name: "♻️ 自动选择"
    type: url-test
    use:
      - airport
    url: http://www.gstatic.com/generate_204
    interval: 300
    tolerance: 50
  - name: "🌍 国外媒体"
    type: select
    use:
      - airport
    proxies:
      - "🚀 节点选择"
      - "♻️ 自动选择"
      - DIRECT
  - name: "🐟 漏网之鱼"
    type: select
    use:
      - airport
    proxies:
      - "🚀 节点选择"
      - DIRECT

rules:
  - GEOIP,LAN,DIRECT,no-resolve
  - DOMAIN-SUFFIX,github.com,🚀 节点选择
  - DOMAIN-SUFFIX,githubusercontent.com,🚀 节点选择
  - DOMAIN-SUFFIX,github.io,🚀 节点选择
  - DOMAIN-SUFFIX,ghcr.io,🚀 节点选择
  - DOMAIN-SUFFIX,docker.io,🚀 节点选择
  - DOMAIN-SUFFIX,docker.com,🚀 节点选择
  - DOMAIN-SUFFIX,google.com,🚀 节点选择
  - DOMAIN-SUFFIX,youtube.com,🌍 国外媒体
  - GEOIP,CN,DIRECT
  - MATCH,🐟 漏网之鱼
YAML

    # 用真实链接替换占位符（避免 sed 特殊字符问题，用 awk）
    awk -v url="$SUB_URL" '{gsub(/SUB_URL_PLACEHOLDER/, url); print}' \
        "$CONFIG" > "$CONFIG.tmp" && mv "$CONFIG.tmp" "$CONFIG"

    info "config.yaml 已生成（含 provider 引用）"
fi

# ---------- 6. 语法校验 ----------
echo "  校验配置语法..."
if docker run --rm -v "$CONFIG_DIR:/root/.config/mihomo" "$IMAGE" \
       -t -f /root/.config/mihomo/config.yaml 2>&1 | grep -qi "successful"; then
    info "语法校验通过"
else
    warn "语法校验未明确通过，请检查："
    docker run --rm -v "$CONFIG_DIR:/root/.config/mihomo" "$IMAGE" \
        -t -f /root/.config/mihomo/config.yaml 2>&1 | tail -10
fi

# ---------- 7. 重启 ----------
echo "  重启容器..."
cd "$CLASH_DIR" && docker compose restart mihomo >/dev/null 2>&1 || true
sleep 3
docker logs mihomo --tail 8 2>&1 | grep -E "error|Error|fatal" && warn "日志中有错误" || info "容器已重启"

echo ""
info "完成！"
echo "  网页面板 : http://192.168.3.3:9090/ui/"
echo "  代理地址 : http://192.168.3.3:7890"
echo ""
echo "  订阅每 3600 秒自动更新，也可随时手动重跑本脚本。"
