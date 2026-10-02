<div align="center">

<img src="frontend/public/logo.png" alt="wg-panel" width="110" />

# wg-panel

**WireGuard 云端可视化管理面板**

[![Docker Image](https://img.shields.io/badge/docker-xueayis%2Fwg--panel-2496ED?logo=docker)](https://hub.docker.com/r/xueayis/wg-panel)
[![Source](https://img.shields.io/badge/source-GitHub-181717?logo=github)](https://github.com/xueayi/wg-panel)

</div>

跑在 Docker 里，通过 SSH 纳管你的 WireGuard 中转节点，把「配节点、分虚拟 IP、发客户端配置」
做成界面。**面板不承载任何流量**——隧道始终是 客户端 ↔ 中转节点，它只是遥控器。

## 快速开始

```bash
cp docker-compose.example.yml docker-compose.yml
# 只需改两处：WGP_ADMIN_PASSWORD（初始口令）、WGP_SECRET_KEY（openssl rand -hex 32）
docker compose up -d
```

浏览器打开 `http://<部署机IP>:13010`，初始账号 **`admin` / `admin`**，然后：

**「连接设置」→ 填中转节点地址 + SSH 凭证（密钥或口令）→ 点「测试连接」** —— 就这三步。
节点相关的一切都在面板里配，不用改 compose、也不用在部署机上放私钥。

> 忘了口令？改过初始口令后顶栏会一直提醒；重置方法见教程。

## 功能

| | |
|---|---|
| 中转节点 | Endpoint、监听端口、转发网段、客户端 DNS/MTU，改完一键重签所有客户端配置 |
| 虚拟 IP | 自动分配最小空闲地址，支持静态 IP 与**多网段扩展**，占用一屏可见，删除后回收 |
| 客户端 | 新增 / 停用 / 启用 / 删除（删前自动备份）；扫码、下载 `.conf`、复制文本三件套 |
| 站点互联 | 给网关节点填「网关网段」，一键把所有站点网段并入客户端配置 |
| 安全网 | 每次写配置前自动备份（可回滚、可删单份）、一键体检（能修的直接修）、操作审计 |

## 文档

- **[从零开始接入你的中转节点](docs/guide/getting-started.md)** —— 中转节点准备 → 部署 → 纳管 → 扫码导入 → 站点互联
- [原理讲解](https://blog.xueayi.site/article/wireguard) —— WireGuard 组网与 `AllowedIPs` 的双侧语义

## 设计要点

- **登记表是权威数据**，`wg0.conf` 是它渲染出来的产物；变更一律走 `wg syncconf`（原子、不断线），写前自动备份
- **WireGuard 语义只在 [`agent/wgagent.py`](agent/wgagent.py) 实现一次**，面板侧不重写第二份渲染/同步逻辑
- 私钥与口令只落盘 600 文件，接口只回**指纹**（OpenSSH 风格 `SHA256:…`）；破坏性操作需二次确认

## 环境变量

只有两个需要关心（其余都有默认值，且能在面板里改）：

| 变量 | 说明 |
|---|---|
| `WGP_ADMIN_PASSWORD` | 初始口令，仅**第一次启动**用于初始化账号 |
| `WGP_SECRET_KEY` | 会话签名密钥，建议 `openssl rand -hex 32` |

节点地址、SSH 端口/用户、认证方式、接口名等见 `.env.example`（只是初始值，在「连接设置」里改更省事）。

## 开发

```bash
cd frontend && npm install && npm run dev          # 前端 5173，已代理 /api

cd backend && pip install -r requirements.txt
WGP_DRIVER=mock WGP_DATA_DIR=./data uvicorn app.main:app --reload
# WGP_DRIVER=mock 用内存里的假节点，不碰任何真实资源

cd backend && pytest tests -q --basetemp=/tmp/wg-panel-pytest   # 44 条，不碰真实 /etc/wireguard
```
