# BdSavePro · 百度网盘自动转存

> **⚠️ 本项目是二次开发作品，不是原创项目**
>
> 本项目基于 **[@kokojacket](https://github.com/kokojacket)** 的
> **[baidu-autosave](https://github.com/kokojacket/baidu-autosave)** 修改而来。
> 原项目以 **GNU AGPL-3.0** 许可发布，本衍生版本遵循同一许可。
>
> | 项 | 内容 |
> |---|---|
> | 原作者 | [@kokojacket](https://github.com/kokojacket) |
> | 上游仓库 | https://github.com/kokojacket/baidu-autosave |
> | 本仓库性质 | 非官方衍生版本，自 2026-09 起在原项目基础上修改 |
> | 本项目许可证 | **GNU AGPL-3.0**，完整条款见 [LICENSE](LICENSE) |
> | 修改内容清单 | 见 [NOTICE](NOTICE) |
>
> 本项目与原作者**无隶属关系**，原作者不对本衍生版本提供任何支持。
> 遇到问题请在本仓库提 Issue，不要打扰原作者。
> 依 AGPL-3.0 第 13 条，通过网络使用本程序服务者，可从本仓库获取完整源码。

---

一个基于 Flask + Vue 3 的百度网盘自动转存系统。定时把分享链接里的文件转存到自己的网盘，
支持多账号、定时任务、正则过滤、排除清单、MD5 去重、通知推送，并可联动 QMediaSync 自动刮削。

## 目录

- [相比原版增加了什么](#相比原版增加了什么)
- [快速开始](#快速开始)
- [使用指南](#使用指南)
- [配置文件说明](#配置文件说明)
- [常见问题](#常见问题)
- [开发说明](#开发说明)
- [更新日志](#更新日志)
- [许可证](#许可证)

## 相比原版增加了什么

原版的功能（自动转存、多账号、定时任务、25+ 通知渠道、容量监控、正则过滤/重命名）全部保留。
在此基础上新增：

| 功能 | 说明 |
|---|---|
| **启用 / 停用任务** | 每个任务一个开关，停用后定时和手动执行都会跳过，不用删任务 |
| **对比路径** | 去重比对可以指定另一个网盘目录，而不是只能跟保存路径比 |
| **转存文件夹多选** | 分享里只转存指定的几个文件夹，支持逐层下钻、跨层级多选 |
| **包含子目录开关** | 选「否」时只转存所选文件夹**本级**的文件，不进子文件夹 |
| **排除文件清单** | 按正则筛选出可转存的文件，勾掉不想转的，之后自动跳过 |
| **MD5 去重** | 除了文件名，还会比对 MD5。**你在网盘里改了文件名也不会重复转存** |
| **转存日志** | 每次执行留档（存 SQLite），可查统计、排除清单、命中文件、当时的配置 |
| **执行监控增强** | 执行过程实时显示排除文件、分级日志、各阶段数量 |
| **QMediaSync 联动** | 转存到新文件后自动触发 QMS 的刮削任务，支持手动触发与触发日志 |

## 快速开始

### 用 docker-compose 部署（推荐）

新建 `docker-compose.yml`：

```yaml
services:
  bdsavepro:
    image: ghcr.io/xinyulo/bdsavepro:latest
    container_name: bdsavepro
    restart: unless-stopped
    ports:
      - "5000:5000"
    volumes:
      - ./config:/app/config
      - ./log:/app/log
    environment:
      - TZ=Asia/Shanghai
```

```bash
mkdir -p config log
docker compose up -d
```

访问 `http://你的IP:5000`

> **默认账号**：`admin`
> **默认密码**：`zxcvbnm`
>
> 登录后请立刻在「系统设置」里改掉密码。

### 用 docker run 部署

```bash
mkdir -p config log
docker run -d \
  --name bdsavepro \
  --restart unless-stopped \
  -p 5000:5000 \
  -v $(pwd)/config:/app/config \
  -v $(pwd)/log:/app/log \
  -e TZ=Asia/Shanghai \
  ghcr.io/xinyulo/bdsavepro:latest
```

### 目录结构

```
bdsavepro/
├── config/                      # 数据目录（必须持久化）
│   ├── config.json              # 账号、任务、通知等配置
│   ├── config.template.json     # 配置模板（首次启动用它生成 config.json）
│   └── history.db               # SQLite：转存历史 + QMS 配置与触发日志
├── log/                         # 运行日志
│   └── web_app_YYYY-MM-DD.log
├── frontend/                    # Vue 3 前端源码
├── web_app.py                   # Flask 主程序、路由
├── storage.py                   # 百度网盘 API、转存核心逻辑
├── scheduler.py                 # 定时任务调度
├── notify.py                    # 通知渠道
├── history_db.py                # SQLite 历史库
├── qms_client.py                # QMediaSync 客户端
├── Dockerfile
└── requirements.txt
```

**只有 `config/` 和 `log/` 需要持久化**，其他都在镜像里。

## 使用指南

### 1. 添加百度网盘账号

1. 浏览器登录百度网盘网页版
2. `F12` → Application/应用 → Cookies，找到 `BDUSS` 和 `STOKEN` 两个值
3. 在「用户管理」里添加账号，填入这两个值

### 2. 创建转存任务

点「添加任务」，主要字段：

| 字段 | 说明 |
|---|---|
| 任务名称 | 可选。填了分享链接后会自动抓取文件夹名填充 |
| 分享链接 | 必填。支持带提取码的完整链接 |
| 保存路径 | 必填。转存到网盘的哪个目录 |
| 文件过滤 | 可选。正则，只转存匹配的文件，如 `^[3-9]\d\..*4k.*\.mp4$` |
| 重命名 | 可选。正则替换，谨慎使用（百度对重命名有频率限制） |
| 定时规则 | 可选。cron 表达式，留空则跟随全局定时 |
| 分类 | 可选。用于归类 |

### 3. 启用 / 停用任务

任务列表里每行有个「启用」开关。关掉之后：

- 定时任务到点不会执行
- 「立即执行」按钮变灰，防止误点
- 任务保留在列表里，随时可以再打开

不用为了临时停掉一个任务而删掉它。

### 4. 对比路径

默认情况下，系统拿「保存路径」里的文件来判重。**对比路径**让你指定另一个目录来比对。

**典型场景**：你转存到 A 目录，但想跟 B 目录里的存货去重。

编辑任务 → 「对比路径」→ 点「选择」可以浏览你自己的网盘目录，也可以手动粘贴路径。
**留空 = 沿用保存路径**，行为和以前完全一致。

### 5. 转存文件夹（下钻多选）

只想转存分享里的某几个文件夹，不用整个链接照单全收：

1. 编辑任务 → 填好分享链接 → 「转存文件夹」点 **选择**
2. 弹窗里列出这个分享的文件夹
3. **点文件夹名字进下一层**（左上角「返回上级」退回）
4. **勾选框**表示选中这个文件夹
5. 可以跨层级混选，比如选第 1 层的 A、再钻进去选第 3 层的 B
6. 保存

**「包含子目录」开关**（默认「是」）：

- **是**：选中文件夹里的子文件夹及其文件，一起转存
- **否**：只转存该文件夹**本级**的文件，子文件夹全部不要

> 一个都不选 = 走原逻辑，整条分享全转存。老任务不需要改。

### 6. 排除文件

按过滤规则筛出能转存的文件，然后勾掉你不想转的那些：

1. 任务行 → 「排除」按钮
2. 系统读取分享文件，**先按你的过滤正则筛一遍**，展示能转进来的文件（带大小）
3. 勾选要排除的 → 「保存排除清单」
4. 之后每次执行都会跳过这些文件

弹窗里还有个 **「一键勾选无MD5文件」** 按钮：有些文件百度不返回 MD5，导致 MD5 去重对它们无效，
一键勾上可以避免这些文件重复转存。

> ⚠️ 排除是**按分享内路径**匹配的。如果你在网盘里改了文件名，需要重新勾选。

### 7. MD5 去重

原版只按**文件名（规范化路径）**判重，所以你把已经转存过的文件改了名，下次会被当成新文件再转一份。

现在会**额外比对 MD5**：

- 分享侧文件的 MD5 来自百度 API 返回
- 网盘侧（对比路径下）文件的 MD5 同样来自百度 API
- MD5 命中 → 跳过并记日志「MD5 已存在，跳过」
- 个别文件拿不到 MD5 → 自动回退到文件名比对，保证不漏转

**没有额外请求开销**，两边 MD5 都是现成的。

### 8. 转存日志与执行详情

每次执行（定时或手动）都会留档，存进 SQLite（`config/history.db`），**每个任务保留最近 10 次**。

任务行 → 「日志」按钮，看到列表：

- 开始时间 ~ 结束时间
- 成功 / 失败
- 转存文件数
- **详情**按钮

点「详情」能看到那一次执行的完整档案：

- **执行信息**：转存路径、对比路径、转存文件夹、包含子目录、保存文件夹、文件过滤正则
- **执行结果**：各阶段数量统计
- **正则过滤后的文件**：你的规则命中了哪些
- **排除文件**：本次被排除清单拦下的文件
- **本次实际转存**：真正转进去了哪些
- **执行日志**：按 INFO / WARN / ERROR 分级着色

> 历史记录是**快照**：它记的是"当时"的情况，之后再改排除清单或正则，不会影响旧记录。

### 9. 定时设置

- **全局定时**：适用于所有没设单独定时的任务
- **单任务定时**：每个任务独立设置

cron 表达式示例：

| 表达式 | 含义 |
|---|---|
| `*/5 * * * *` | 每 5 分钟 |
| `0 */1 * * *` | 每小时 |
| `0 20 * * *` | 每天 20:00 |
| `0 8,12,18 * * *` | 每天 8 点、12 点、18 点 |

### 10. 通知设置

支持 25+ 种渠道：PushPlus、Bark、钉钉、飞书、企业微信、Telegram、SMTP 邮件、Gotify、
ServerJ、PushDeer、自定义 Webhook 等。

配置方式：

1. 「系统设置」→ 启用通知
2. 填写对应服务的字段（如 `PUSH_PLUS_TOKEN`）
3. 点「测试通知」验证
4. 可以同时配多种

**通知延迟合并**：短时间内多个任务执行完，会把通知合并成一条发出，默认延迟 30 秒。

**自定义 Webhook**：点「添加WEBHOOK配置」一键生成所需字段，`WEBHOOK_BODY` 支持简化写法
（如 `title: "$title"content: "$content"`），保存时自动转成标准格式。

### 11. 网盘容量监控

超过阈值时通过已配置的通知渠道告警：

1. 「系统设置」→ 启用「网盘容量提醒」
2. 设置阈值（默认 90%）
3. 设置检查时间（默认每天 00:00）

### 12. 连接 QMediaSync（自动刮削）

[QMediaSync](https://github.com/qicfan/qmediasync) 是个媒体刮削工具。
本功能让转存和刮削串成一条流水线：**转存到新文件 → 自动触发对应的刮削任务**。

**先在 QMS 侧准备**：登录 QMediaSync → 系统设置 → API 密钥 → 创建一个，记下 `qms_` 开头的字符串。

**在本工具配置**：

1. 左侧菜单 → **「连接QMediaSync」**
2. **连接设置**：填地址、端口（QMS 默认 `12333`）、API Key，点「测试连接」
3. **连接列表** → 「创建连接」→ 弹窗里**左边选本工具的一个任务，右边选 QMS 的一个刮削目录** → 确认
4. 之后那个任务**转存到新文件**时，会自动触发它绑定的刮削

连接列表每行有三个操作：

- **触发**：立刻手动跑一次这条连接对应的刮削
- **日志**：查看触发记录（时间、来源、结果、消息），每条连接保留最近 30 条
- **删除**：移除这条绑定

页面顶部还有「立即触发全部」，会依次触发所有启用的连接。

**触发规则**：

| 情况 | 是否触发 |
|---|---|
| 转存到新文件 + 有绑定 | ✅ 触发 |
| 没有新文件 | ❌ 不触发（避免空跑） |
| 任务没配绑定 | ❌ 不触发 |
| 全局「自动触发」关掉 | ❌ 不触发 |
| 该条连接被停用 | ❌ 不触发 |

> QMS 的配置和连接列表存在 SQLite（`config/history.db`）里，不会被转存过程中的
> 配置回写覆盖。

## 配置文件说明

`config/config.json` 主要字段：

```jsonc
{
  "auth": {
    "users": "admin",
    "password": "zxcvbnm",      // 首次启动的默认密码，登录后请修改
    "session_timeout": 3600
  },
  "baidu": {
    "users": {},                // 百度账号（cookies）
    "current_user": null,
    "tasks": []                 // 任务列表
  },
  "cron": {
    "default_schedule": ["0 10 * * *"],
    "auto_install": true
  },
  "notify": {
    "enabled": false,
    "notification_delay": 30,
    "direct_fields": {
      "PUSH_PLUS_TOKEN": "",
      "BARK_PUSH": "",
      "DD_BOT_TOKEN": "",
      "TG_BOT_TOKEN": "",
      "SMTP_SERVER": "",
      "WEBHOOK_URL": ""
    }
  },
  "quota_alert": {
    "enabled": true,
    "threshold_percent": 90,
    "check_schedule": "0 0 * * *"
  },
  "scheduler": {
    "max_workers": 1,
    "misfire_grace_time": 3600,
    "coalesce": true,
    "max_instances": 1
  }
}
```

任务条目里新增字段：

| 字段 | 说明 |
|---|---|
| `enabled` | 是否启用，缺省视为启用 |
| `compare_path` | 对比路径，留空用保存路径 |
| `transfer_folders` | 选中的转存文件夹（分享内路径） |
| `include_subdirs` | 是否包含子目录，缺省视为是 |
| `exclude_files` | 排除文件清单 |

**另外历史与 QMS 数据不在 `config.json`，而在 `config/history.db`（SQLite）。**
这样设计是因为 `config.json` 在转存过程中会被高频回写，放历史数据容易丢。

## 常见问题

**Q：任务执行失败怎么查？**
先看「日志」按钮里的执行详情，里面有完整日志。再检查分享链接是否失效、账号 cookies 是否过期。

**Q：定时任务不执行？**
确认任务开关是「启用」状态；检查 cron 表达式格式；确认容器时区正确（`TZ=Asia/Shanghai`）。

**Q：改了文件名的文件还被重复转存？**
MD5 去重应该能拦住。如果没拦住，检查该文件是否有 MD5（百度偶尔不返回）——
可以在「排除」弹窗用「一键勾选无MD5文件」把这些文件排除掉。

**Q：转存文件夹选了却没生效？**
确认保存时任务确实保存成功；执行详情里看「转存文件夹」和「包含子目录」的值对不对。

**Q：排除文件弹窗里看不到我想排除的文件？**
排除列表是先按任务的**过滤正则**筛过一遍的。如果那个文件不匹配你的正则，它本来就不会被转存，
也就不会出现在候选里。

**Q：通知发不出去？**
用「测试通知」验证配置；检查 Token 是否有效；确认容器能访问外网。

**Q：QMS 连接测试失败？**
检查地址端口是否可达；确认 API Key 正确且未过期；地址要填**你实际能访问到的地址**
（QMS 官方默认端口 `12333`，但如果你的部署做了端口映射，就填映射后的端口）。

**Q：数据怎么备份？**
备份 `config/` 目录即可，里面有账号、任务、历史和 QMS 配置。

## 开发说明

### 技术栈

- 后端：Python 3.10 + Flask + APScheduler
- 前端：Vue 3 + Vite + Element Plus
- 存储：JSON（配置）+ SQLite（历史）

### 主要模块

| 文件 | 职责 |
|---|---|
| `web_app.py` | Flask 应用、所有 HTTP 路由、手动执行入口 |
| `storage.py` | 百度网盘 API、转存核心逻辑、过滤与去重 |
| `scheduler.py` | 定时调度、定时执行入口 |
| `history_db.py` | SQLite：转存历史、键值存储、QMS 触发日志 |
| `qms_client.py` | QMediaSync 客户端与连接匹配 |
| `notify.py` | 通知渠道实现 |
| `frontend/` | Vue 3 前端源码 |

### 本地开发

```bash
# 后端
pip install -r requirements.txt
python web_app.py

# 前端（开发模式）
cd frontend
npm install
npm run dev
```

### 自己构建镜像

```bash
git clone https://github.com/xinyuLo/bdsavepro.git
cd bdsavepro
docker build -t bdsavepro:latest .
docker run -d --name bdsavepro -p 5000:5000 \
  -v $(pwd)/config:/app/config -v $(pwd)/log:/app/log \
  -e TZ=Asia/Shanghai bdsavepro:latest
```

> 构建时如果 npm / pip 需要走镜像源，`requirements.txt` 里默认用的是阿里源，
> 前端走 `registry.npmmirror.com`。境外网络构建可以自行替换。

### 自动构建

仓库带了一套 GitHub Actions（`.github/workflows/docker-build.yml`）：

- 推送到 `main` 分支时自动构建并推送到 GHCR
- 也可以在 Actions 页面手动触发，可选是否构建 arm64
- 镜像地址：`ghcr.io/xinyulo/bdsavepro`

## 更新日志

### v2.0.0（本衍生版本）

**新增功能**

- 任务启用 / 停用开关，停用后定时与手动执行均跳过
- 对比路径：可指定独立的去重比对目录，支持网盘目录选择器
- 转存文件夹：支持逐层下钻、跨层级多选的文件夹选择，配套「包含子目录」开关
- 排除文件清单：按过滤正则筛选后多选排除，支持一键勾选无 MD5 文件
- MD5 去重：在原有文件名比对之外增加 MD5 比对，解决改名后重复转存
- 转存历史改用 SQLite 存储，记录配置快照、正则命中文件、排除清单、转存路径
- 执行监控：新增排除文件区块、分级日志、各阶段数量统计
- QMediaSync 联动：连接列表模型，转存到新文件后自动触发绑定刮削，支持手动触发与触发日志

**界面改进**

- 仪表盘：卡片可点击跳转；「系统信息」改为「已启用任务」列表
- 任务表格：重建列宽体系，修复刷新页面时列宽错位
- 操作列：按钮分组换行，全部补齐悬停提示
- 转存日志列表与详情页重做

**安全与工程**

- 默认登录密码由 `admin123` 改为 `zxcvbnm`
- 补充 `.gitignore` / `.dockerignore`，避免凭据误入版本库与镜像
- 清理仓库内的本地缓存与凭据样例文件

### 上游历史

沿用上游项目的版本记录，详见
[上游仓库](https://github.com/kokojacket/baidu-autosave)（v1.0.0 ~ v1.1.5）。

## 许可证

本项目以 **GNU Affero General Public License v3.0（AGPL-3.0）** 发布，
与上游项目保持一致。完整条款见 [LICENSE](LICENSE)。

> 说明：上游 README 的「许可证」章节写作 MIT License，但上游仓库中的 `LICENSE`
> 文件实际为 AGPL-3.0。本项目以 `LICENSE` 文件为准，继续以 AGPL-3.0 发布。

上游项目的版权归原作者 **[@kokojacket](https://github.com/kokojacket)** 所有，
本项目新增与修改部分的版权归本项目作者所有。

## 致谢

- **[@kokojacket](https://github.com/kokojacket)** —— 上游项目
  [baidu-autosave](https://github.com/kokojacket/baidu-autosave) 的作者，本项目的全部基础来自他
- [Flask](https://flask.palletsprojects.com/)
- [APScheduler](https://apscheduler.readthedocs.io/)
- [baidupcs-py](https://github.com/PeterDing/BaiduPCS-Py)
- [Element Plus](https://element-plus.org/)
- [quark-auto-save](https://github.com/Cp0204/quark-auto-save) —— 上游 README 提到的参考项目
