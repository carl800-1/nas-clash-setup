# 在绿联 GUI 里创建 clash 项目

## 为什么命令行建的容器在 GUI 里看不到

绿联 Docker 的「项目」页只显示**通过 GUI「创建」按钮登记过**的项目。
用 `docker compose up` 命令行建的容器，Docker 层面是完整的 compose 项目
（`docker compose ls` 能查到），但没有写进 GUI 的项目清单，所以界面不显示。

本机另外两个容器 `emby`、`qbittorrent` 也是同样情况，都不在 GUI 项目页里。

结论：**想让项目出现在 GUI，必须走 GUI 的创建流程登记一次。**

---

## 前置准备（已完成，无需重复操作）

NAS 上 `/volume1/docker/clash/` 目录已就绪，包含：

```
docker-compose.yaml     ← 含 mihomo + panel 两个服务
config/                 ← mihomo 配置、规则集、provider
webapp/                 ← 订阅控制台（sub-panel.py + index.html）
scripts/                ← probe-nodes.py 节点探测
sub.txt                 ← 订阅地址
```

当前两个容器均已通过命令行正常运行，service 可访问：

| 入口 | 地址 | 说明 |
|---|---|---|
| 订阅控制台 | http://192.168.3.3:8090/ | 说明 / 控制 / 状态 一体化 |
| mihomo 官方面板 | http://192.168.3.3:9090/ui/ | metacubexd，只读 |
| 代理端口 | 192.168.3.3:7890 / 7891 | HTTP / SOCKS5 |

---

## 创建步骤

### 第 0 步：移走现有容器（重要）

GUI 创建同名项目会报冲突，必须先让出 `mihomo` 和 `clash-panel` 这两个名字。

SSH 登录 NAS 后执行：

```bash
cd /volume1/docker/clash
sudo docker compose down
```

> 只删容器，不动数据。`config/`、`webapp/`、`sub.txt` 都在磁盘上，不受影响。
> 想恢复随时 `sudo docker compose up -d` 即可。

### 第 1 步：打开创建项目界面

Docker 应用 → 左侧 **项目** → 右上角 **创建**

### 第 2 步：填写项目信息

| 字段 | 填什么 |
|---|---|
| 项目名称 | `clash` |
| 项目目录 / 路径 | `/volume1/docker/clash` |

### 第 3 步：粘贴编排文件

选择「使用现有的编排文件」，或直接把仓库里的
`nas-clash-setup/docker-compose.yaml` 内容整份粘贴进去。

> **必须整份粘贴**，两个服务（`mihomo` 和 `panel`）要在一起，
> 这样 GUI 里启停项目时两个容器会联动。

### 第 4 步：创建

点「立即构建」。首次需要拉取 `python:3.13-slim` 镜像，约 1～2 分钟。

完成后项目页会出现 `clash`，显示 **2 / 2 运行中**。

---

## 验证清单

| 检查项 | 期望结果 |
|---|---|
| GUI 项目列表 | 出现 `clash`，2/2 运行中 |
| http://192.168.3.3:8090/ | 控制台正常打开，容器状态显示 running |
| http://192.168.3.3:9090/ui/ | metacubexd 正常 |
| 控制台内「停止」按钮 | mihomo 退出，7890 端口不通 |
| 控制台内「启动」按钮 | mihomo 起来，约 20 秒后代理恢复 |
| 控制台内「立即拉取」 | 日志显示 `更新完成`，节点数刷新 |
| GUI 内停止整个项目 | 两个容器一起停 |

---

## 架构说明

### 为什么 panel 用 host 网络

mihomo 是 `network_mode: bridge`，容器名跨网络无法解析。
panel 若在同一 bridge 网络，`http://mihomo:9090` 会 DNS 失败。

改用 `network_mode: host` 后：
- `127.0.0.1:9090` 即宿主映射的 mihomo 控制器
- `/var/run/docker.sock` 也在宿主，启停容器直接可用
- 代价是 8090 直接占用宿主端口，compose 里不能再写 `ports`

### 为什么面板不用 docker CLI

面板自己也是容器，基础镜像里没有 `docker` 命令。
`sub-panel.py` 已改为直连 **Docker Engine API**（HTTP over unix socket），
纯标准库实现：

- `docker_req()` — 底层请求，走 `/var/run/docker.sock`
- `container_action()` — start / stop / restart
- `container_recreate()` — 采集原容器参数后重建，使新配置生效
- `container_status()` — 读容器状态

不依赖 docker CLI，也就不需要在 panel 镜像里装 docker。

### 重启策略

两个服务都是 `restart: "no"`，即不随系统自启、按需手动启停。
想改成开机自启，把 compose 里两个服务的 `restart` 改为 `unless-stopped`。

---

## 故障排查

**GUI 报项目名冲突**
没执行第 0 步，或上次的容器没清干净：
```bash
sudo docker ps -a --filter name=mihomo
sudo docker ps -a --filter name=clash-panel
```

**GUI 里显示项目但容器没起来**
点「启动」看日志，或 SSH 执行：
```bash
cd /volume1/docker/clash && sudo docker compose logs --tail=50
```

**8090 打不开**
```bash
sudo docker ps --filter name=clash-panel     # 是否 Up
sudo docker logs clash-panel --tail=30       # 看启动报错
```

**控制台显示「未知」**
面板连不上 Docker daemon，确认 compose 里 panel 服务挂了 socket：
```yaml
- /var/run/docker.sock:/var/run/docker.sock
```

**代理不通但控制台正常**
节点问题，不是部署问题。控制台点「全部测速」看哪些节点还活着，
免费节点本来就不稳定。日志里出现 `i/o timeout` 就是机场侧节点挂了。
