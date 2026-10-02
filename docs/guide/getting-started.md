# wg-panel 从零开始：把你的 WireGuard 管起来

> 这篇教程沿用一篇经典中文 WireGuard 长文（*《如何优雅地异地组网？超详细的 WireGuard 安装与使用说明》*）
> 的思路：**中转节点部署 → 客户端接入 → 多地局域网互联**。
> 区别在于：那篇文章里靠 SSH 敲命令和 Shell 脚本完成的每一步，这里都变成面板上的一个按钮。
> 概念细节（`AllowedIPs` 的双侧语义、站点互联）建议对照原文一起看。

| 博客里的做法 | wg-panel 里对应 |
|---|---|
| 手写 `wg0.conf`、`wg set`、`wg addconf` | 面板「新增客户端」表单 |
| `create_client.sh`（分配 IP + 生成配置 + 二维码） | 新增客户端时自动完成 |
| `remove_client.sh`（sed 删块 + 清理文件） | 客户端列表的「删除」按钮 |
| 忘了 SaveConfig、注释丢失、配置漂移 | 登记表渲染 + `SaveConfig=false` + 写前自动备份 |
| 脚本只在本机，换台电脑就得重新拷 | 浏览器打开面板，哪里都能管 |

---

## 0. 你需要准备什么

- **一台有公网 IP 的服务器**（VPS / 轻量应用服务器，Ubuntu 22.04+ 或 Debian 12+）作为中转节点——就是博客里的"中转枢纽"；
- **一台能 SSH 登录这台服务器的机器**（跑 Docker 的 NAS、家里的主机都行），面板从这里遥控节点；
- **一把 SSH 私钥**（`ed25519` / `rsa` / `ecdsa` 均可），能免密登录中转节点；
- 跑 Docker 的机器（NAS、服务器、台式机均可）。

> 面板**不承载任何 VPN 流量**。隧道永远是 客户端 ↔ 中转节点，面板只负责发指令。

---

## 1. 中转节点：一次性准备（对应博客「服务端部署」）

这部分只需要做一次，以后再也不用 SSH 上去了。登录你的 VPS：

```bash
sudo apt update && sudo apt install -y wireguard
```

生成服务端密钥对（博客第 1 节）：

```bash
wg genkey | sudo tee /etc/wireguard/private.key
sudo chmod go= /etc/wireguard/private.key
sudo cat /etc/wireguard/private.key | wg pubkey | sudo tee /etc/wireguard/public.key
```

写一份**最小**配置。和博客不同的是：这里不需要手写每个 Peer 的块，
`[Interface]` 段只要四行（Peer 交给面板管理）：

```ini
# /etc/wireguard/wg0.conf
[Interface]
Address = 10.8.1.1/24
ListenPort = 10015
PrivateKey = <刚生成的私钥>
SaveConfig = false
```

> 为什么现在就写 `SaveConfig = false`？`true` 会让 wg-quick 把运行时快照写回配置文件，
> 丢注释、丢顺序、临时 Endpoint 全都烙进去——这是博客脚本方案最常见的翻车点。

开启内核转发（博客第 3 节）：

```bash
echo 'net.ipv4.ip_forward=1' | sudo tee /etc/sysctl.d/99-wg.conf
sudo sysctl -p /etc/sysctl.d/99-wg.conf
```

如果你要让客户端**通过节点上外网**（全局代理模式），再加 NAT 规则；
只做内网互访（局域网模式）可以不加：

```ini
# 追加到 wg0.conf 的 [Interface] 段（按实际出口网卡名改 eth0）
PostUp   = iptables -A FORWARD -i %i -j ACCEPT; iptables -A FORWARD -o %i -j ACCEPT; iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE
PostDown = iptables -D FORWARD -i %i -j ACCEPT; iptables -D FORWARD -o %i -j ACCEPT; iptables -t nat -D POSTROUTING -o eth0 -j MASQUERADE
```

启动并设开机自启（博客第 4 节）：

```bash
sudo systemctl enable --now wg-quick@wg0
```

**最后一步，也是最容易漏的一步**：去云服务商控制台放行 UDP 端口
（上面例子是 `10015/udp`）。连不上的问题十有八九出在这里。

---

## 2. 部署 wg-panel（5 分钟）

在跑 Docker 的机器上（比如 NAS）：

```bash
mkdir -p wg-panel/ssh && cd wg-panel
cp <能登录中转节点的私钥> ssh/id_ed25519 && chmod 600 ssh/id_ed25519
curl -O https://raw.githubusercontent.com/xueayis/wg-panel/main/docker-compose.example.yml \
  -o docker-compose.yml    # 或者手动复制仓库里的 docker-compose.example.yml
```

编辑 `docker-compose.yml`，只需要改这几行：

```yaml
environment:
  WGP_DRIVER: ssh                  # 纳管真实节点
  WGP_SSH_HOST: 203.0.113.10       # ← 换成你中转节点的公网地址
  WGP_SSH_PORT: "22"               # ← SSH 端口
  WGP_ADMIN_PASSWORD: __ADMIN_PASSWORD__   # ← 面板登录口令（只首次生效，建议 openssl rand -base64 18）
  WGP_SECRET_KEY: __SECRET_KEY__           # ← openssl rand -hex 32
```

| 变量 | 说明 |
|---|---|
| `WGP_SSH_KEY` | 私钥在容器内的路径，默认 `/ssh/id_ed25519`（对应上面的只读挂载） |
| `WGP_SSH_USER` | 需要能读写 `/etc/wireguard`，通常是 `root` |
| `WGP_SSH_REMOTE_TOOL` | 面板内核脚本在节点上的落点，默认 `/root/wireguard/wgagent.py` |
| `WGP_READ_ONLY` | `1` = 面板只显示不修改（上线首日验证用），确认无误后改 `0` |

启动：

```bash
docker compose up -d
```

浏览器打开 `http://<部署机IP>:13010`，用户名 `admin`，口令就是你刚填的 `WGP_ADMIN_PASSWORD`。

> 📌 **口令只在这第一次生效**：它被写入面板数据库的哈希后就与 env 无关了，
> 之后改口令请走「连接设置」页。忘了口令的话，删掉数据库里的 admin 记录再重启即可重新初始化
> （`docker compose exec wg-panel python -c "import sqlite3;c=sqlite3.connect('/data/panel.db');c.execute('DELETE FROM admin');c.commit()"`）。

### 进面板第一件事：核对「连接设置」

**这是标准流程，别跳过。** 登录后打开左侧「**连接设置**」：

| 项目 | 说明 |
|---|---|
| 服务地址（Endpoint） | 中转节点的公网地址——客户端最终连的就是它 |
| SSH 端口 / 用户 | 面板登节点用的，用户需要能读写 `/etc/wireguard`（通常 `root`） |
| WireGuard 接口名 | 默认 `wg0` |
| SSH 私钥 | 显示"已就绪"才算配好；没有就粘贴私钥上传（面板落盘 600，只回指纹） |

点一次「**测试连接**」：面板会重连、把管理脚本下发到节点、读回客户端数量。
成功说明这一层通了，之后换节点或改端口都在这个页面完成，不必再碰 `docker-compose.yml`。

> 安全提醒：面板会经 SSH 下发一个小脚本（`wgagent.py`）到节点的
> `/root/wireguard/`，所有 WireGuard 语义都在这一个文件里，sha256 校验后执行。
> 私钥只读挂载，面板不落任何明文凭证。

---

## 3. 首次纳管

分两种情况：

### 3a. 节点上已经有手工/脚本维护的 peer（比如照博客做过的）

进「备份 & 审计」页 → 点**「纳管既有配置」**。面板会：

1. 解析现有 `wg0.conf` 里的每个 Peer，能用 `clients/*.pub` 认出名字的认名字；
2. 把它们连同接口参数一起写进登记表 `registry.json`（权威数据源）；
3. **先自动备份**，然后按登记表重渲染 `wg0.conf`，补回 `# 名字` 注释；
4. `wg syncconf` 增量同步到内核——**在线设备不会掉线**。

之后配置文件就是登记表的"渲染产物"，手工改动会被面板下次写入覆盖回去。

### 3b. 全新节点

什么都不用做，直接进下一步添加客户端。

---

## 4. 添加设备（对应博客「客户端接入」）

「客户端」页 → **新增客户端**：

- **名称**：按设备起名（`iphone15`、`mac-mini`），虚拟 IP 自动分配最小空闲地址；
- **隧道模式**：
  - **局域网**：只通内网（默认，`AllowedIPs` = 转发网段，见下）；
  - **全局**：全部流量走 VPN（出差上咖啡厅 Wi-Fi 用这个）；
  - **自定义**：手填 `AllowedIPs`；
- 创建完成后弹出**配置导入三件套**：
  - 📱 手机：扫二维码，WireGuard App 一秒导入；
  - 💻 电脑：下载 `.conf` 双击导入；
  - 📋 或者复制文本自己贴。

**转发网段**在「中转节点 & IP」页设置（默认 `10.8.1.0/24`）——它决定"局域网模式"的客户端
能直达哪些网段。想顺手把家里内网也放开，就把它加进去（比如 `192.168.1.0/24`）。

---

## 5. 站点互联：让整个局域网互访（对应博客「高级篇」）

博客讲得最透的一点：`AllowedIPs` 在服务端和客户端是两套语义——

- **服务端侧**（peer 的 `AllowedIPs`）："**这个包该转给谁**" + "只有他有资格用这个源地址"；
- **客户端侧**："**我想去哪里**"——流量要不要进隧道。

### 场景：家里路由器（istore/openwrt）背后挂着 192.168.1.0/24

1. 在面板「客户端」页为这台路由器**新增或编辑**时，把
   **网关网段**填上 `192.168.1.0/24`。
   面板会在服务端侧把它写进该 peer 的 `AllowedIPs`：
   `AllowedIPs = 10.8.1.10/32, 192.168.1.0/24`。
2. 「中转节点 & IP」页会出现**站点互联**卡片，列出每个网关节点背后的网段。
   点**「并入客户端并重签」**——所有客户端的配置里都会加上这个网段并重新下发。
   以后每加一个站点，点一下这个按钮就行，不用挨个改配置。
3. **被访问侧（家里路由器）**还要满足博客 Checklist：
   - 开启 `net.ipv4.ip_forward=1`；
   - WireGuard 接口放进防火墙转发区域，允许 `lan ↔ vpn`（OpenWrt 里勾选 IP 动态伪装）；
   - 各站点局域网网段**不能冲突**（家里 `192.168.1.x`、公司就别再用这个段）。

做完这些，你在公司 ping `192.168.1.x` 就是家里的 NAS。

### 多个站点？

一样的：每个网关节点填自己的网关网段，站点互联卡片自动汇总，
「并入客户端并重签」把所有网段广播给全部客户端。IP 池页能看到整个 `10.8.1.0/24`
的占用网格，谁占了哪个地址一目了然。

---

### 地址不够用了？

一个 `/24` 网段最多 253 个客户端。用满了在「中转节点 & IP」页底部填一个新网段
（比如 `10.8.2.0/24`）点「添加网段」——面板会把这个网段挂到接口上（接口地址随之变成两个），
新客户端自动落到第一个有空位的段；新增时也可以手动指定从哪个段分配。
摘除网段时如果里面还有客户端，面板会拒绝，逼你先迁移。

## 6. 日常运维

| 场景 | 去哪做 |
|---|---|
| 设备丢了 / 换手机了 | 客户端列表 → 删除（自动备份）或停用（保留私钥随时恢复） |
| 改了 Endpoint / 网段 | 中转节点页保存 → 勾选"重签全部客户端配置" |
| 怀疑配置被手工改乱 | 体检（配置与运行时一致性 + 私钥权限 + 僵尸节点） |
| 改坏了想回滚 | 备份页 → 回滚（覆盖前还会再备份一次） |
| 谁动过什么 | 审计页，私钥每一次暴露都有记录 |

---

## 7. 从博客脚本迁移

| create_client.sh | remove_client.sh | wg-panel |
|---|---|---|
| `read` 客户端名 → `wg genkey` → 手动找空闲 IP → 拼配置 → `wg addconf` → `qrencode` | `wg set peer remove` → `sed` 删块（公钥含 `/` 会翻车）→ 删文件 | 新增/删除按钮；IP 冲突当场拒绝；`syncconf` 原子生效；每一步写前备份 |
| 脚本只在本机 | 同左 | 浏览器随处可管，操作留审计 |

迁移时直接跑一次「纳管既有配置」即可，脚本生成的 peer 会被原样收编（名字从
`clients/*.pub` 反查），`clients/` 目录里的密钥文件继续沿用，**客户端不需要重新导入**。

---

## 8. 连不上？按这个顺序查

1. **云控制台放行 UDP 端口了吗**？（最常见，没有之一）
2. 面板「备份 & 审计」页连接状态是否绿色、体检有没有报"接口未启动"；
3. 客户端 `Endpoint` 是否对（改过域名/端口后要重签：中转节点页 → 重签全部）；
4. 客户端 `AllowedIPs` 包含你要访问的网段吗（站点互联 → 并入客户端并重签）；
5. 被访问网关开了 `ip_forward` 和防火墙转发吗；
6. 两边局域网网段是不是冲突了。

---

*为什么登记表是权威数据源、为什么变更走 `wg syncconf` 而不用 `wg addconf`，见 [README](../../README.md)。*
