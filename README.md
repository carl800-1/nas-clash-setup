# nas-clash-setup

> 在绿联 NAS 上用 Docker 按需跑 Clash（mihomo），**不做常驻网关**。
>
> v2.0.0 —— mihomo 代理核心 + 8090 一体化控制台，一个 Compose 项目搞定。
>
> 实测环境：绿联 DXP4800 Plus / UGOS Pro（Debian 12），部署路径 `/volume1/docker/clash/`

---

## 这是什么

一套**按需启停**的 Clash 代理方案。v2.0.0 起，代理核心和控制台合并为**同一个 Compose 项目**，
在绿联 GUI 的「项目」页里能看到、能一键启停。

| 特性 | 说明 |
|---|---|
| **不常驻** | `restart: "no"`，开机不自动拉起，不占内存 |
| **不做网关** | 只开端口，不动任何设备的网关 / 路由表 |
| **一体化面板** | `http://NAS_IP:8090/` 一个页面搞定：容器启停、订阅、节点、连接、日志 |
| **订阅自动更新** | provider 机制，每小时拉取；也可点「⚡ 刷新订阅」立即拉 |
| **真实流量守护** | 「🛡 自动守护」用真实 HTTPS 请求探活，节点挂了自动切换 |
| **可完全卸载** | 删目录即无残留 |

### 为什么不做全屋网关

- 华为 AX3 Pro 不允许修改 DHCP 下发的网关（固件层面锁定）
- AP 模式会丢失远程管理能力（硬需求）
- 代理一关会导致全屋断网

所以本方案定位为：**NAS 及局域网内设备按需取用**。

---

## 架构

```
┌─────────────────────────────────────────────────┐
│  浏览器 / 局域网设备                              │
│  SwitchyOmega → http://192.168.3.3:7890         │
└───────────────────────┬─────────────────────────┘
                        │ HTTP / SOCKS5
                ┌───────▼────────┐
                │  mihomo 容器    │  bridge 网络
                │  :7890 混合代理  │
                │  :9090 控制器API │
                └───────┬────────┘
                        │ 127.0.0.1:9090（host 网络）
                ┌───────▼────────┐
                │ clash-panel 容器 │  host 网络
                │  :8090 控制台    │  直连 docker.sock 启停 mihomo
                └─────────────────┘
```

| 地址 | 用途 |
|---|---|
| `http://192.168.3.3:8090/` | **日常使用** —— 一体化控制台 |
| `http://192.168.3.3:9090/ui/` | mihomo 官方 metacubexd（只读，备用） |
| `192.168.3.3:7890` | HTTP / SOCKS5 混合代理 |
| `192.168.3.3:7891` | SOCKS5（单独） |

---

## 目录结构

```
nas-clash-setup/
├── docker-compose.yaml           # ⭐ 双服务编排（mihomo + panel）
├── config/
│   └── config.example.yaml       # 配置模板（复制成 config.yaml 再改）
├── webapp/
│   ├── sub-panel.py              # 8090 控制台后端（纯 Python 标准库）
│   └── index.html                # 8090 控制台前端
├── scripts/
│   ├── on.sh                     # 开启代理（含 Docker 守护进程代理）
│   ├── off.sh                    # 关闭代理（务必用它，会撤销守护进程代理）
│   ├── setup.sh                  # 首次部署初始化
│   ├── update-sub.sh             # 命令行更新订阅（备用方案）
│   ├── probe-nodes.py            # 节点真实测速 + 自动切最快节点
│   └── merge-sub.py              # 静态订阅合并（v1 方案，保留备用）
├── GUI创建项目.md                 # 在绿联 GUI 里登记项目的完整步骤
├── CHANGELOG.md
├── README.md
└── LICENSE
```

NAS 上部署后的实际结构：

```
/volume1/docker/clash/
├── docker-compose.yaml
├── config/
│   ├── config.yaml               # 真实配置（含订阅链接，已 gitignore）
│   ├── providers/airport.yaml    # 订阅节点缓存（自动更新）
│   ├── ruleset/                  # 本地规则集
│   ├── country.mmdb              # GeoIP 数据库
│   └── ui/                       # metacubexd 静态文件
├── webapp/                       # 控制台代码
├── scripts/                      # 脚本
└── sub.txt                       # 订阅链接
```

---

## 快速开始

### 1. 放置文件

```bash
cd /volume1/docker
git clone <本仓库地址> clash
cd clash
chmod +x scripts/*.sh
```

### 2. 生成配置

```bash
cp config/config.example.yaml config/config.yaml
vi config/config.yaml
```

改两处：
- `proxy-providers.airport.url` → 你的机场订阅链接（**必须是 Clash 格式**）
- `dns.nameserver-policy` → 你的机场节点域名

### 3. 准备 GeoIP 数据（重要）

容器启动时会尝试从 GitHub 下载 GeoIP 数据，**国内网络会卡死**。必须提前放好：

```bash
cd /volume1/docker/clash/config
curl -L -o country.mmdb https://gh-proxy.com/https://github.com/MetaCubeX/meta-rules-dat/releases/download/latest/country.mmdb
```

> ⚠️ 用 `country.mmdb` 而不是 `GeoIP.dat`。China-only 精简版 `GeoIP.dat` 缺 US/JP 等国家码，
> 配上 `GEOIP,US` 这类规则会直接报 `country code us not found` 导致配置加载失败。

### 4. 在绿联 GUI 里创建项目

**为什么必须走 GUI**：绿联 Docker 的「项目」页只显示通过 GUI「创建」按钮登记过的项目。
用 `docker compose up` 命令行建的容器在 Docker 层面完整（`docker compose ls` 查得到），
但 GUI 里不显示。

完整步骤见 **[GUI创建项目.md](GUI创建项目.md)**。

要点：
1. 先 `sudo docker compose down` 让出容器名
2. Docker → 项目 → 创建 → 名称 `clash` → 目录 `/volume1/docker/clash`
3. **整份粘贴**根目录的 `docker-compose.yaml`（两个服务必须一起）
4. 点「立即构建」

命令行部署则直接：
```bash
cd /volume1/docker/clash && sudo docker compose up -d
```

### 5. 打开控制台

```
http://192.168.3.3:8090/
```

面板上可以：切模式、启停容器、刷新订阅、测速切节点、看实时连接 / 速率 / 日志。

---

## 日常使用

### 网页控制台（推荐）

`http://192.168.3.3:8090/`

| 区域 | 能做什么 |
|---|---|
| **模式** | rule / global / direct 切换，切换后即时生效 |
| **容器** | 启停 mihomo、recreate 使新配置生效 |
| **订阅** | 查看 / 修改订阅链接，点「⚡ 刷新订阅」立即拉取（实测约 1 秒） |
| **节点** | 各策略组切换、全部测速、「🛡 自动守护」开关 |
| **连接** | 实时连接列表、断开全部、看实时速率 |
| **日志** | SSE 实时日志 |

### 命令行开关

```bash
cd /volume1/docker/clash
./on.sh          # 开（同时给 Docker 守护进程配代理，让 docker pull 也能走代理）
./off.sh         # 关（务必用它，会撤销 Docker 守护进程代理）
```

### 各场景

**浏览器**：装 SwitchyOmega / SwitchyOmegaMV3，新建代理配置：

| 字段 | 值 |
|---|---|
| 协议 | **HTTP** |
| 服务器 | `192.168.3.3` |
| 端口 | `7890` |

> 7890 是 mixed-port，HTTP / SOCKS5 都走它。Windows **系统代理保持关闭**，
> 只让浏览器插件接管，避免影响其他程序。

**git**：
```bash
git config --global http.https://github.com.proxy http://192.168.3.3:7890
# 撤销
git config --global --unset http.https://github.com.proxy
```

**临时命令**：
```bash
curl -x http://192.168.3.3:7890 https://www.google.com
export https_proxy=http://192.168.3.3:7890
```

---

## 订阅说明

### 两种订阅方式

| 方式 | 机制 | 适用 |
|---|---|---|
| **provider（v2 默认）** | mihomo 自己每小时拉订阅 | 日常用这个，无需手动 |
| 静态订阅（v1 遗留） | `merge-sub.py` 手动生成 `airport.yaml` | provider 抽风时的备用 |

### 换机场

**三步，缺一步就会全超时：**

**① 改订阅链接** —— 编辑 `config/config.yaml`：

```yaml
proxy-providers:
  airport:
    url: "https://新机场/新订阅路径?clash=1"
```

**② 改 DNS 解析白名单** —— 不同机场的节点域名后缀不同，漏了就全挂：

```bash
# 先查新机场的节点域名
curl -s "https://新机场订阅链接" | grep -oE 'server: [a-z0-9.-]+' | head -5
```

然后把 `dns.nameserver-policy` 里的域名后缀补全，**用通配符**：

```yaml
dns:
  nameserver-policy:
    '+.新机场域名后缀':      # 节点域名
      - 223.5.5.5
      - 119.29.29.29
    '新机场订阅域名':         # 订阅接口域名
      - 223.5.5.5
      - 119.29.29.29
```

> 实测踩过：某机场节点域名是 `*.qpon`，而配置里只有 `*.fr0528.art`，
> 结果换过去所有节点都超时。**换机场后先测一轮再判定可用**。

**③ 重启并刷新**

```bash
sudo docker restart mihomo
# 控制台点「⚡ 刷新订阅」，或：
curl -X POST http://127.0.0.1:8090/api/refresh
```

### 验证换机场是否成功

**别只看延迟数字**，要真实流量测试（免费/付费机场都可能骗人）：

```bash
# 20 次连续采样，看成功率和失败模式
for i in $(seq 1 20); do
  curl -x http://127.0.0.1:7890 -o /dev/null -s -w "%{http_code} " \
       --max-time 20 https://www.google.com/
  sleep 1.5
done
echo

# 抓 <title> 确认真 HTML，不是空响应
curl -x http://127.0.0.1:7890 -s --max-time 20 https://www.google.com/ | grep -o '<title>[^<]*</title>'

# 验出口 IP 确实不是本地
curl -x http://127.0.0.1:7890 -s --max-time 15 https://api.ipify.org
```

**实测参考**（同一台 NAS，同一时间）：

| 机场 | 20 次采样 google 首页 | 结论 |
|---|---|---|
| iKuuu 免费（7 节点） | 最优 15/20，5 个节点 0/3 | 机场侧 AWS 实例 i/o timeout |
| 付费 IEPL（47 节点） | 20/20，12/12，10/10 | 稳定 |

### 自动更新频率

`interval: 3600`（每小时）。**不建议调更短** —— 免费机场域名动态轮换，
但过于频繁的刷新反而容易触发限流。需要立即更新时点面板的「⚡ 刷新订阅」。

### 自动守护该不该开

**取决于节点池质量**：

- **付费 / 多节点（推荐开）**：节点池大，守护切到哪个都能用
- **免费 / 少节点（建议关）**：实测遇到过守护把主选择组切到 0/3 的死节点，
  反而比自己手动选更差。这种情况手动锁定一个验证过的节点更稳

```bash
curl -X POST -d '{"on":false}' http://127.0.0.1:8090/api/guard   # 关
curl -X POST -d '{"on":true}'  http://127.0.0.1:8090/api/guard   # 开
```

---

## 自动守护（🛡）

免费机场节点会突然挂掉，但 mihomo 内置的健康检查**只看隧道建立 + 一个 204 响应**，
会出现「延迟 150ms 显示绿灯，但 google / github 全打不开」。

所以面板提供了两个独立指标：

| 指标 | 含义 | 可信度 |
|---|---|---|
| 延迟数字 | 隧道建立耗时 | ⚠️ 不准 |
| **✅ 实测通 / ❌ 实测不通** | 真实 HTTPS 请求 google + cloudflare | ✅ 准 |

开启「🛡 自动守护」后（约 60 秒一轮）：
- 用真实 HTTPS 流量检查**当前节点**
- 当前节点失败才扫描其他节点
- 找到能用的立刻切过去，并停止切换（不会来回震荡）

---

## ⚠️ 注意事项

1. **关闭代理务必用 `off.sh`** —— 直接 `docker compose down` 不会撤掉 Docker 守护进程的代理配置，会导致后续 `docker pull` 全部卡 15 秒超时。

2. **不要改成 `restart: always`** —— 需求是偶尔用，常驻会白占内存。要开机自启改 `unless-stopped`。

3. **订阅链接只支持 Clash 格式** —— 机场给 Base64 通用格式的话，去后台切换订阅类型。

4. **9090 面板的「启动」按钮会报错** —— 那是从 metacubexd（只读面板）点的，它不知道容器已在运行，Docker 就返回启动失败。**无害**，日常用 8090。mihomo 的 `RestartPolicy` 是 `no`，点了也不会真重启。

5. **真实配置不要提交** —— `config/config.yaml` 含订阅链接（带 token），已在 `.gitignore` 里。仓库只提交 `config.example.yaml` 模板。

---

## 踩坑记录

### 部署 / 启动

| 问题 | 原因 | 解法 |
|---|---|---|
| GUI「项目」页是空的 | 命令行建的项目没在 GUI 登记 | 走 GUI 创建流程粘贴 compose |
| `Can't find MMDB, start download` 卡死 | 容器要从 GitHub 下 GeoIP，国内不通 | 提前手动下载 `country.mmdb` |
| `mihomo` 镜像 `manifest unknown` | 部分加速源未收录 | 用 `docker.1ms.run/metacubex/mihomo` |
| metacubexd 镜像拉不动 | 国内加速源不支持 ghcr | 用 mihomo 内置 `external-ui` |
| 9090 点启动报「启动失败」 | metacubexd 只读，不知道容器已跑 | 无害，改用 8090 |
| 传文件到 NAS 得到 0 字节 | `ssh "cat > f" < local` 会静默产生空文件 | `cat local \| ssh host "sudo tee /path/f >/dev/null"`，传完 `wc -c` 验证 |

### 配置 / 代理

| 问题 | 原因 | 解法 |
|---|---|---|
| 配置加载失败 `country code us not found` | 用了 China-only `GeoIP.dat` | 换完整 `country.mmdb` |
| 面板连不上 mihomo | mihomo 是 bridge 网络，跨网络解析不到容器名 | panel 改 `network_mode: host` |
| 面板报 Docker「未知」 | 没挂 docker.sock | 挂 `/var/run/docker.sock` |
| 面板里没法启停容器 | panel 镜像没有 `docker` CLI | 改直连 Docker Engine API（unix socket） |
| DNS 解析死锁 | `nameserver` 只用 DoH，解析 DoH 域名本身要 DNS | 保留国内明文 DNS 打底 |
| 国外域名被污染 | `fallback` 用了 cloudflare/google DoH（大陆被 reset） | 只用 `doh.pub` / `dns.alidns.com` |
| 节点服务器域名解析失败 | 机场域名没走直连解析 | `nameserver-policy` 加通配符 `+.节点域名` |
| **换机场后全部节点超时** | 新机场节点域名后缀不同（如 `*.qpon`），配置里只有旧的 | 见「换机场」第②步，`grep 'server:'` 查新后缀补进去 |
| 延迟绿灯但 google 打不开 | 延迟只验隧道不验 TLS/带宽 | 跑 20 次真实采样；见「验证换机场是否成功」 |
| 自动守护把节点切到死节点 | 守护探活与真实流量判定不一致 | 节点池 <5 个时关守护，手动锁定验证过的节点 |
| 手动选的节点被冲回第一个 | mihomo 未启用 `store-selected` | 加 `store-selected: true` |
| **所有流量直连、代理形同虚设** | `♻️ 自动选择` 组含 `local` 里的 `direct` 占位节点，延迟近 0 永远选它 | 自动组只 `use: [airport]` |
| github/google 报 `error: xxx-node` | `🌐 代理` 默认用 url-test 组，全挂时退化成选第一个死节点 | 默认改用 `🔰 选择节点`（手动） |
| 测速绿灯但 google 打不开 | 延迟只验隧道，不验 TLS | 看「实测通」；开自动守护 |
| **Windows 上 shell 脚本 `bad interpreter: /bin/bash^M`** | `git add` 把 CRLF 带进去了 | 仓库根 `.gitattributes`：`*.sh text eol=lf` |

### 环境相关（NAS / Windows）

| 问题 | 解法 |
|---|---|
| `git url."git@ssh.github.com:443/"` 无效 | **必须带 `ssh://` 前缀**，否则 git 把 `:443` 当路径去连 22 |
| `github.com:443` 间歇性阻断 | 单次测试不可信，采样 4 次以上判断；其他域名 443 正常，是针对性干扰 |

---

## 故障排查

```bash
cd /volume1/docker/clash

docker compose ps                            # 项目状态
docker logs mihomo --tail 50                 # 代理核心日志
docker logs clash-panel --tail 50            # 控制台日志

# 测代理
curl -x http://127.0.0.1:7890 -I https://www.google.com --max-time 10

# 测控制台 / 面板
curl -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8090/
curl -o /dev/null -w "%{http_code}\n" http://127.0.0.1:9090/ui/

# 配置语法校验
docker run --rm -v /volume1/docker/clash/config:/root/.config/mihomo \
  docker.1ms.run/metacubex/mihomo:latest -t -f /root/.config/mihomo/config.yaml

# 节点真实测速（自动切最快）
python3 scripts/probe-nodes.py
```

---

## 卸载

```bash
cd /volume1/docker/clash && ./off.sh
cd / && sudo rm -rf /volume1/docker/clash
```

---

## License

MIT
