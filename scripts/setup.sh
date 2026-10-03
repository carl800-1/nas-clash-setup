#!/bin/bash
# ============================================================
# setup.sh —— 首次部署 / 修复环境
# ============================================================
# 用法：在 NAS 上以 root 执行
#   ./setup.sh
#
# 做什么：
#   1. 检查目录结构
#   2. 安装依赖（unzip / curl）
#   3. 下载 GeoIP 数据（避免容器启动卡死）
#   4. 下载面板 UI
#   5. 拉取 mihomo 镜像
# ============================================================

set -e

CLASH_DIR="/volume1/docker/clash"
CONFIG_DIR="$CLASH_DIR/config"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE="docker.1ms.run/metacubex/mihomo:latest"
GH_PROXY="https://gh-proxy.com"
META_RULES="$GH_PROXY/https://github.com/MetaCubeX/meta-rules-dat/releases/download/latest"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info()  { echo -e "${GREEN}[✓]${NC} $1"; }
warn()  { echo -e "${YELLOW}[!]${NC} $1"; }
error() { echo -e "${RED}[✗]${NC} $1"; exit 1; }

[ "$(id -u)" -ne 0 ] && error "请以 root 执行（sudo ./setup.sh）"

echo "=========================================="
echo " nas-clash-setup 部署"
echo "=========================================="

# ---------- 1. 目录 ----------
echo "[1/5] 准备目录..."
mkdir -p "$CONFIG_DIR" "$CONFIG_DIR/providers"
cp -n "$REPO_DIR/deploy/docker-compose.yaml" "$CLASH_DIR/" 2>/dev/null || true
[ -f "$CONFIG_DIR/config.yaml" ] || cp "$REPO_DIR/deploy/config.yaml.example" "$CONFIG_DIR/config.yaml"
cp "$REPO_DIR/scripts/on.sh" "$REPO_DIR/scripts/off.sh" "$REPO_DIR/scripts/update-sub.sh" "$CLASH_DIR/" 2>/dev/null || true
chmod +x "$CLASH_DIR"/*.sh 2>/dev/null || true
info "目录就绪：$CLASH_DIR"

# ---------- 2. 依赖 ----------
echo "[2/5] 检查依赖..."
for pkg in curl unzip; do
    if ! command -v "$pkg" >/dev/null 2>&1; then
        warn "安装 $pkg ..."
        apt-get install -y "$pkg" >/dev/null 2>&1 || warn "$pkg 安装失败，请手动装"
    fi
done
info "依赖检查完成"

# ---------- 3. GeoIP 数据 ----------
echo "[3/5] 检查 GeoIP 数据..."
declare -A DATA=(
    ["GeoIP.dat"]="geoip.dat|2000000"
    ["geosite.dat"]="geosite.dat|1000000"
    ["country.mmdb"]="country.mmdb|5000000"
)
for f in "GeoIP.dat" "geosite.dat" "country.mmdb"; do
    target="$CONFIG_DIR/$f"
    if [ -f "$target" ] && [ "$(stat -c%s "$target")" -gt 100000 ]; then
        info "$f 已存在（$(du -h "$target" | cut -f1)）"
    else
        IFS='|' read -r remote min <<< "${DATA[$f]}"
        echo "  下载 $f ..."
        curl -L --max-time 180 -o "$target" "$META_RULES/$remote" 2>/dev/null || true
        if [ -f "$target" ] && [ "$(stat -c%s "$target")" -gt "$min" ]; then
            info "$f 下载完成（$(du -h "$target" | cut -f1)）"
        else
            warn "$f 下载失败，请手动放到 $CONFIG_DIR/"
        fi
    fi
done

# ---------- 4. 面板 UI ----------
echo "[4/5] 检查面板 UI..."
if [ -f "$CONFIG_DIR/ui/index.html" ]; then
    info "面板已存在"
else
    echo "  下载 metacubexd ..."
    cd /tmp
    curl -L --max-time 180 -o ui.zip \
        "$GH_PROXY/https://github.com/MetaCubeX/metacubexd/archive/refs/heads/gh-pages.zip" 2>/dev/null || true
    if [ -f ui.zip ]; then
        rm -rf metacubexd-gh-pages
        unzip -q ui.zip 2>/dev/null || true
        if [ -d metacubexd-gh-pages ]; then
            rm -rf "$CONFIG_DIR/ui"
            mv metacubexd-gh-pages "$CONFIG_DIR/ui"
            info "面板部署完成"
        fi
        rm -f ui.zip
    else
        warn "面板下载失败（不影响代理功能，仅无 web 面板）"
    fi
fi

# ---------- 5. 镜像 ----------
echo "[5/5] 拉取 mihomo 镜像..."
docker pull "$IMAGE" >/dev/null 2>&1 && info "镜像就绪" || warn "镜像拉取失败"

echo ""
echo "=========================================="
echo -e "${GREEN} 部署完成${NC}"
echo "=========================================="
echo " 下一步："
echo "   1. 填订阅：echo \"你的Clash订阅链接\" > $CLASH_DIR/sub.txt"
echo "   2. 更新节点：cd $CLASH_DIR && ./update-sub.sh"
echo "   3. 启动代理：./on.sh"
echo ""
echo " 面板地址：http://192.168.3.3:9090/ui/"
echo "=========================================="
