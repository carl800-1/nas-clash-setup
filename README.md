# nas-clash-setup

> 在绿联 NAS 上用 Docker 按需跑 Clash（mihomo），不做常驻网关。
>
> 适用环境：绿联 DXP4800 Plus / UGOS Pro，部署路径 `/volume1/docker/clash/`

---

## 这是什么

一套**按需启停**的 Clash 代理方案。核心取舍：

| 特性 | 说明 |
|---|---|
| **不常驻** | `restart: "no"`，开机不自动拉起，不占内存 |
| **不做网关** | 只开端口，不动任何设备的网关/路由表 |
| **一键开关** | `./on.sh` / `./off.sh` |
| **面板可维护** | 内置 metacubexd 面板，测延迟、切策略组 |
| **订阅自动更新** | provider 机制，换节点不用手改配置 |
| **可完全卸载** | 删目录即无残留 |

### 为什么不做全屋网关

- 华为 AX3 Pro 不允许修改 DHCP 下发的网关（固件层面锁定）
- AP 模式会丢失远程管理能力（硬需求）
- 代理一关会导致全屋断网

所以本方案定位为：**NAS 及局域网内设备按需取用**。

---

## 目录结构

```
nas-clash-setup/
├── deploy/
│   ├── docker-compose.yaml     # 容器编排
│   └── config.yaml.example     # 配置模板（占位节点）
├── scripts/
│   ├── on.sh                   # 开启代理
│   ├── off.sh                  # 关闭代理
│   └── update-sub.sh           # 更新订阅节点
├── README.md
└── LICENSE
```

NAS 上部署后的实际结构：

```
/volume1/docker/clash/
├── docker-compose.yaml
├── config/
│   ├── config.yaml             # 主配置
│   ├── providers/
│   │   └── airport.yaml        # 订阅节点（自动更新）
│   ├── country.mmdb            # GeoIP 数据库
│   ├── GeoIP.dat
│   ├── geosite.dat
│   └── ui/                     # metacubexd 面板静态文件
├── sub.txt                     # 订阅链接（一行）
├── on.sh
└── off.sh
```

---

## 快速开始

### 1. 部署

```bash
cd /volume1/docker
git clone <本仓库地址> clash
cd clash
chmod +x scripts/*.sh
```

或手动把 `deploy/` 里的文件放到 `/volume1/docker/clash/`。

### 2. 准备 GeoIP 数据（重要）

容器启动时会尝试从 GitHub 下载 GeoIP 数据，**国内网络会卡死**。必须提前放好：

```bash
cd /volume1/docker/clash/config
# 通过 gh-proxy 镜像下载
curl -L -o GeoIP.dat    https://gh-proxy.com/https://github.com/MetaCubeX/meta-rules-dat/releases/download/latest/geoip.dat
curl -L -o geosite.dat  https://gh-proxy.com/https://github.com/MetaCubeX/meta-rules-dat/releases/download/latest/geosite.dat
curl -L -o country.mmdb https://gh-proxy.com/https://github.com/MetaCubeX/meta-rules-dat/releases/download/latest/country.mmdb
```

### 3. 准备面板 UI（可选）

```bash
cd /volume1/docker/clash/config
curl -L -o ui.zip https://gh-proxy.com/https://github.com/MetaCubeX/metacubexd/archive/refs/heads/gh-pages.zip
unzip -q ui.zip && mv metacubexd-gh-pages ui && rm ui.zip
```

### 4. 填入订阅链接

```bash
echo "https://你的机场/clash订阅链接" > /volume1/docker/clash/sub.txt
cd /volume1/docker/clash && ./update-sub.sh
```

### 5. 启动

```bash
cd /volume1/docker/clash && ./on.sh
```

---

## 日常使用

```bash
cd /volume1/docker/clash

./on.sh          # 开
./off.sh         # 关（务必用它，会同步撤掉 Docker 守护进程代理）
```

| 地址 | 用途 |
|---|---|
| `http://192.168.3.3:9090/ui/` | 网页面板 |
| `http://192.168.3.3:7890` | HTTP 代理 |
| `192.168.3.3:7891` | SOCKS5 |

### 各场景

**浏览器**：装 SwitchyOmega，代理指向 `192.168.3.3:7890`。

**git**：
```bash
git config --global http.https://github.com.proxy http://192.168.3.3:7890
# 撤销
git config --global --unset http.https://github.com.proxy
```

**Docker 拉镜像**：`on.sh` 会自动配置守护进程代理，`off.sh` 自动撤销。

**临时命令**：
```bash
curl -x http://192.168.3.3:7890 https://www.google.com
export https_proxy=http://192.168.3.3:7890
```

---

## ⚠️ 注意事项

1. **关闭必须用 `off.sh`** —— 直接 `docker compose down` 不会撤掉 Docker 守护进程的代理配置，会导致后续 `docker pull` 全部卡 15 秒超时。

2. **不要改成 `restart: always`** —— 需求是偶尔用，常驻会白占内存。

3. **订阅链接只支持 Clash 格式** —— 如果机场给的是 Base64 通用格式，需在机场后台切换订阅类型。

4. **面板不能"新增节点"** —— metacubexd 走的是 mihomo RESTful API，只能切组/测延迟，不能编辑 `proxies`。新增节点靠 provider 机制：改订阅 → 面板点更新，或跑 `update-sub.sh`。

---

## 踩坑记录

| 问题 | 原因 | 解法 |
|---|---|---|
| `Can't find MMDB, start download` 卡死 | 容器要从 GitHub 下 GeoIP 数据，网络不通 | 提前手动下载放 `config/` |
| `MMDB invalid, remove and download` | 下载的 mmdb 不完整 | 用 `gh-proxy.com` 重新下载 |
| 面板镜像 `ghcr.io/metacubex/metacubexd` 拉不动 | 国内加速源不支持 ghcr | 用 mihomo 内置 `external-ui` |
| `mihomo` 镜像 `manifest unknown` | 部分加速源未收录 | 用 `docker.1ms.run/metacubex/mihomo` |
| `git url."git@ssh.github.com:443/"` 无效 | 缺 `ssh://` 前缀，git 把 `:443` 当路径 | 写成 `url."ssh://git@ssh.github.com:443/"` |

---

## 故障排查

```bash
docker logs mihomo --tail 50                      # 日志
docker ps -a | grep mihomo                        # 状态
curl -x http://192.168.3.3:7890 -I https://www.google.com --max-time 10   # 测代理
curl -o /dev/null -w "%{http_code}\n" http://192.168.3.3:9090/ui/         # 测面板
```

配置语法校验：
```bash
docker run --rm -v /volume1/docker/clash/config:/root/.config/mihomo \
  docker.1ms.run/metacubex/mihomo:latest -t -f /root/.config/mihomo/config.yaml
```

---

## 卸载

```bash
cd /volume1/docker/clash && ./off.sh
cd / && rm -rf /volume1/docker/clash
```

---

## License

MIT
