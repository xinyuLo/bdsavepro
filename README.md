# BdSavePro

百度网盘自动转存工具，网页操作。

定时抓取分享链接里的文件存到自己的网盘，支持正则过滤、按 MD5 去重、执行日志、通知推送，也可以在转存完成后自动调用 QMediaSync 刮削。

基于 [kokojacket/baidu-autosave](https://github.com/kokojacket/baidu-autosave) 修改，2026 年 9 月起维护。上游使用 AGPL-3.0，本项目沿用；修改内容记录在 [NOTICE](NOTICE)，许可条款见 [LICENSE](LICENSE)。与原作者无隶属关系，遇到问题请在本仓库提 Issue。

## 安装

镜像发布在两个地方，内容相同：

- Docker Hub：`7yueyue/bdsavepro`
- GHCR：`ghcr.io/xinyulo/bdsavepro`

`docker-compose.yml`：

```yaml
services:
  bdsavepro:
    image: 7yueyue/bdsavepro:latest
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

浏览器打开 `http://IP:5000`，默认账号 `admin`，密码 `zxcvbnm`，登录后请到系统设置修改。

## 使用

添加账号：浏览器登录百度网盘网页版，F12 打开开发者工具，在 Cookies 里找到 `BDUSS` 和 `STOKEN`，填到「用户管理」里。

创建任务：填分享链接和保存路径就能跑。可选设置：

- 定时规则，cron 格式，比如 `0 20 * * *` 是每天 20 点，留空用全局设置
- 文件过滤，正则，只转存匹配的文件
- 对比路径，去重默认和保存路径里的文件比对，可以换成别的目录
- 转存文件夹，一条分享里有多个目录时只选要的几个，可以进到子目录里选；包含子目录选否时只转存当前层级的文件
- 排除文件，先按过滤规则列出可转存的文件，勾掉不想转的，之后自动跳过
- 重命名，正则替换，百度对重命名有频率限制，慎用

任务列表里的启用开关可以临时停掉一个任务，停用后定时和手动执行都会跳过。

执行分两种：按定时自动跑，或者点「立即执行」。每次执行都有记录，任务行的「日志」按钮可以看历次结果和详情，数据存在 `config/history.db`，每个任务保留最近 10 次。

去重先按文件名比对，再按 MD5 比对，所以改过名的文件不会被重复转存。个别文件百度不返回 MD5，这类文件可以在排除窗口里用「一键勾选无MD5文件」处理。

## QMediaSync 联动

装了 [QMediaSync](https://github.com/qicfan/qmediasync) 的话，可以把它和转存任务关联起来：任务转存到新文件后自动触发对应的刮削任务，刮削完成后再触发 STRM 生成。

1. 在 QMediaSync 的系统设置里创建一个 API Key
2. 本工具左侧菜单「连接QMediaSync」，填地址、端口和 Key，测试连接
3. 创建连接：左边选一个转存任务，右边选一个刮削目录，可以再绑一个 STRM 同步目录
4. 之后这个任务有新文件转存时，会自动触发绑定的刮削

连接列表里有触发、编辑、日志、删除。日志记录每次触发的时间、来源（定时或手动）、刮削结果和 STRM 结果，每条连接保留 30 条。

两点说明：QMediaSync 按文件路径去重，已经在它数据库里的文件会被跳过，所以删掉文件重新转存同名文件不会重新刮削，需要先在它的记录页删掉对应记录；STRM 同步会先比对本地文件，没有差异就不生成。

## 备份与升级

`config/` 目录里有账号、任务和历史数据，备份这个目录即可。升级执行：

```bash
docker compose pull && docker compose up -d
```

## 常见问题

**任务失败**：先看「日志」里的执行详情，多数是分享链接失效或 cookies 过期。

**定时不执行**：检查任务是否处于启用状态，cron 格式是否正确，容器时区是否为 Asia/Shanghai。

**通知发不出去**：在系统设置里用「测试通知」验证，支持 PushPlus、Bark、钉钉、飞书、企业微信、Telegram、邮件、Webhook 等。

**QMediaSync 连不上**：地址和端口要填实际能访问到的，官方默认端口是 12333，如果做了端口映射就填映射后的端口。

## 构建

后端 Python 3.10 + Flask + APScheduler，前端 Vue 3 + Vite + Element Plus。

```bash
pip install -r requirements.txt && python web_app.py   # 后端
cd frontend && npm install && npm run dev              # 前端
docker build -t bdsavepro:latest .                     # 镜像
```

推送到 main 分支会自动构建并推送镜像，版本号取自 `frontend/package.json`。

## 更新日志

### v2.0.1（2026-09-26）

- 接入 QMediaSync：转存后自动触发刮削，结果写入日志，成功后自动触发 STRM 生成
- 修复刮削结果长时间显示等待中的问题，改为按 QMS 任务状态判定
- 连接列表支持编辑，可绑定 STRM 同步目录
- 设置页版本信息改版，移除检查更新

### v2.0.0

- 新增任务启停开关、对比路径、转存文件夹多选（含子目录开关）、排除文件清单
- 去重增加 MD5 比对，解决改名后重复转存
- 转存历史改用 SQLite 存储，记录各阶段统计
- 界面调整：仪表盘、任务表格列宽、执行详情页等
- 默认密码由 admin123 改为 zxcvbnm

上游 v1.0.0 ~ v1.1.5 的变更见[上游仓库](https://github.com/kokojacket/baidu-autosave)。

## 许可证

AGPL-3.0，与上游一致。上游 README 写的是 MIT，但仓库里的 LICENSE 文件是 AGPL-3.0，以文件为准。

上游版权归 kokojacket 所有，本项目新增部分的版权归本项目作者。依照 AGPL-3.0 第 13 条，通过网络使用本程序可以在本仓库获取源码。
