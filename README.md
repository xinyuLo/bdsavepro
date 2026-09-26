# BdSavePro

百度网盘自动转存工具，带网页界面。

定时把别人分享的链接里的文件转存到你自己的网盘，支持正则过滤、去重、通知，
还能在转存到新文件后自动叫 QMediaSync 去刮削。

> **本项目是二次开发作品，不是原创。**
> 基于 **[@kokojacket](https://github.com/kokojacket)** 的
> [baidu-autosave](https://github.com/kokojacket/baidu-autosave) 修改而来，自 2026-09 起修改。
> 上游以 **AGPL-3.0** 发布，本项目沿用同一许可；改了什么见 [NOTICE](NOTICE)，完整条款见 [LICENSE](LICENSE)。
> 本仓库与原作者无隶属关系，原作者不提供任何支持——有问题请在本仓库提 Issue，别去打扰他。

---

## 一、装

两个镜像仓库内容完全一样，选一个：

| 仓库 | 地址 |
|---|---|
| Docker Hub | `7yueyue/bdsavepro:latest` |
| GHCR | `ghcr.io/xinyulo/bdsavepro:latest` |

新建 `docker-compose.yml`：

```yaml
services:
  bdsavepro:
    image: 7yueyue/bdsavepro:latest
    container_name: bdsavepro
    restart: unless-stopped
    ports:
      - "5000:5000"
    volumes:
      - ./config:/app/config     # 配置、历史、QMS 数据（必须保留）
      - ./log:/app/log           # 运行日志
    environment:
      - TZ=Asia/Shanghai
```

```bash
mkdir -p config log
docker compose up -d
```

打开 `http://你的IP:5000`。

- 默认账号 `admin`，默认密码 `zxcvbnm`
- **登录后第一件事：去「系统设置」改密码**

不用 compose 也行：

```bash
docker run -d --name bdsavepro --restart unless-stopped \
  -p 5000:5000 -v $(pwd)/config:/app/config -v $(pwd)/log:/app/log \
  -e TZ=Asia/Shanghai 7yueyue/bdsavepro:latest
```

## 二、用

### 1. 先加百度账号

「用户管理」→ 添加账号，填 `BDUSS` 和 `STOKEN` 两个值。
获取方法：浏览器登录百度网盘网页版 → `F12` → Application → Cookies → 找到这两个。

### 2. 再建转存任务

「添加任务」，填三样就能跑：

| 字段 | 说明 |
|---|---|
| 分享链接 | 必填，支持带提取码的完整链接 |
| 保存路径 | 必填，转存到你网盘的哪个目录 |
| 定时规则 | 可选，cron 表达式；留空则用全局定时 |

还有几个可选字段，下面单独说。

### 3. 转存怎么跑

- **到点自动跑**：按 cron 规则，例如 `0 20 * * *` = 每天 20:00
- **手动跑**：任务行点「立即执行」

cron 写法：`*/5 * * * *` 每 5 分钟、`0 */1 * * *` 每小时、`0 8,12,18 * * *` 每天 8/12/18 点。

## 三、几个实用功能

| 功能 | 在哪 | 干什么用的 |
|---|---|---|
| **启用 / 停用** | 任务列表的开关 | 临时不想跑就关掉，不用删任务；关掉后定时和手动都跳过 |
| **对比路径** | 编辑任务 | 默认拿「保存路径」里的文件判重，这里可以换成别的目录来比。留空 = 沿用保存路径 |
| **转存文件夹** | 编辑任务 | 一条分享里有好几部剧，只存你要的。点文件夹名进下一层，勾选框选中，可跨层混选 |
| **包含子目录** | 转存文件夹旁边 | 选「否」= 只存这个文件夹**本级**的文件，里面的子文件夹全部不要 |
| **排除文件** | 任务行的「排除」按钮 | 系统先按过滤正则筛出能转的文件，你勾掉不想转的，以后自动跳过 |
| **MD5 去重** | 自动生效 | 不光比文件名，还比 MD5，所以**你在网盘里改了名字也不会重复转一份** |
| **转存日志** | 任务行的「日志」按钮 | 每次执行都留档，可看统计、当时填的配置、命中了哪些文件 |
| **容量提醒** | 系统设置 | 网盘用量超过阈值（默认 90%）时通过通知渠道告警 |

几个容易踩的点：

- **排除是按分享内路径匹配的**，你在网盘里改了文件名，需要重新勾选。
- **排除弹窗里看不到某个文件**，是因为它没通过你的过滤正则——那种文件本来就不会被转存。
- **过滤正则**写的是转存哪类文件，例如 `^[3-9]\d\..*4k.*\.mp4$` 表示第 30~99 集的 4K mp4。
- **重命名**要谨慎用，百度对重命名有频率限制。

## 四、联动 QMediaSync（可选）

[QMediaSync](https://github.com/qicfan/qmediasync) 是媒体刮削工具。
配上以后：**转存到新文件 → 自动触发刮削 → 刮削成功 → 自动生成 STRM**，一条流水线。

**先在 QMS 那边**：登录 QMediaSync → 系统设置 → API 密钥 → 创建一个，记下 `qms_` 开头的字符串。

**再到这里**：

1. 左侧菜单 →「连接QMediaSync」
2. 填地址、端口、API Key，点「测试连接」
3. 「创建连接」→ 左边选本工具一个任务，右边选 QMS 一个刮削目录 →（可选）再绑一个 STRM 同步目录 → 确认
4. 完事。那个任务以后转存到新文件，就会自动触发它绑定的刮削

连接列表每行有「触发 / 编辑 / 日志 / 删除」：

- **触发**：立刻手动跑一次这条连接的刮削
- **日志**：看每次触发的时间、来源（自动还是手动）、刮削结果、STRM 结果；每条连接保留最近 30 条

**什么时候会触发**：任务是启用的 + 这次**确实转存到了新文件** + 配了绑定 + 连接的「自动触发」开着。
没转存到新文件就不会触发，避免空跑。

> 两个正常现象，别当成 bug：
> 1. QMS 对**已经在它数据库里**的文件会直接跳过（日志写「已在数据库中，跳过」）。所以你把文件删了
>    再转存同样的文件名，QMS 不会重新刮削。想让它重跑，去 QMS 的刮削记录页把对应记录删掉。
> 2. STRM 同步是**先比对、有差异才生成**。本地 .strm 已经存在时，它会报成功但不重新生成。

## 五、数据、备份、升级

- 需要保留的只有两个目录：`config/`（账号、任务、历史、QMS 数据）和 `log/`
- `config/config.json` 首次启动自动生成；**改配置请优先在网页上改**，手改容易在下次执行时被覆盖
- **备份**：拷走 `config/` 就够
- **升级**：`docker compose pull && docker compose up -d`
- 历史数据存在 `config/history.db`（SQLite），每个任务留最近 10 次执行记录

## 六、常见问题

**任务失败怎么查？**
点任务行的「日志」→「详情」，里面有那一次的完整日志；再确认分享链接有没有失效、账号 cookies 有没有过期。

**定时不执行？**
看任务开关是不是「启用」；检查 cron 写法；确认容器时区（`TZ=Asia/Shanghai`）。

**改了文件名还是重复转存？**
MD5 去重本来能拦住。少数文件百度不返回 MD5，那就去「排除」弹窗点「一键勾选无MD5文件」把它们排除。

**通知发不出去？**
「系统设置」里点「测试通知」；检查 Token 是否有效、容器能不能上外网。支持 25+ 渠道（PushPlus、Bark、钉钉、飞书、企业微信、Telegram、邮件、自定义 Webhook…），短时间内多次执行会合并成一条发。

**QMS 连不上？**
地址要填**你实际能访问到的**地址。QMS 官方默认端口是 `12333`，但如果你部署时做了端口映射，就填映射后的那个端口。

**想自己改代码？**
后端 Python 3.10 + Flask + APScheduler，前端 Vue 3 + Vite + Element Plus。

```bash
pip install -r requirements.txt && python web_app.py    # 后端
cd frontend && npm install && npm run dev               # 前端开发模式
docker build -t bdsavepro:latest .                      # 自己构建镜像
```

推到 `main` 分支会自动构建并同时推送两个镜像仓库，标签为 `latest` / `v<版本号>` / `sha-<短提交>`。

## 七、更新日志

### v2.0.1（2026-09-26）

- 增加 QMediaSync 联动：转存到新文件后延迟 10 秒触发刮削，等任务跑完把结果写进日志，
  有实际处理过文件时再自动触发 STRM 生成
- 刮削结果判定改为看 QMS 任务状态，不再长时间停在前端显示「等待中」
- 连接列表支持编辑，可绑定 STRM 同步目录
- 设置页版本信息改版，移除「检查更新」

### v2.0.0

- 任务启用 / 停用开关
- 对比路径（带网盘目录选择器）
- 转存文件夹下钻多选 + 包含子目录开关
- 排除文件清单（支持一键勾选无 MD5 文件）
- MD5 去重：改名也不会重复转存
- 转存历史改用 SQLite，记录配置快照与各阶段统计；日志列表与详情页重做
- 仪表盘、任务表格列宽、操作列按钮等界面改进
- 默认密码由 `admin123` 改为 `zxcvbnm`

上游 v1.0.0 ~ v1.1.5 的历史见[上游仓库](https://github.com/kokojacket/baidu-autosave)。

## 八、许可证

本项目以 **GNU AGPL-3.0** 发布，与上游一致，完整条款见 [LICENSE](LICENSE)。

> 上游 README 的「许可证」章节写的是 MIT，但上游仓库的 `LICENSE` 文件实际是 AGPL-3.0。
> 本项目以 `LICENSE` 文件为准。

上游版权归 **[@kokojacket](https://github.com/kokojacket)** 所有，本项目新增与修改部分的版权归本项目作者所有。
依 AGPL-3.0 第 13 条，通过网络使用本程序服务者，可从本仓库获取完整源码。

感谢上游作者 [@kokojacket](https://github.com/kokojacket)，以及 Flask、APScheduler、
[BaiduPCS-Py](https://github.com/PeterDing/BaiduPCS-Py)、[Element Plus](https://element-plus.org/)、
[quark-auto-save](https://github.com/Cp0204/quark-auto-save) 等开源项目。
