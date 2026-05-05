# 纸模资源采集索引 Docker 应用

这是一个面向 `mir-modeley.com` 公开页面的中文索引采集工具。它只采集页面元数据和下载跳转链接，不下载 Google Drive 等资源文件本体。

## 功能

- 按出版商目录页采集资源详情页
- 从 `/news/` 首页检测最新资源
- 发现新出版商并加入出版商索引
- SQLite 本地持久化
- 网页管理界面
- CSV / XLSX 导出
- Docker / 宝塔面板 / NAS 部署

## 本地运行

```bash
docker compose up --build
```

访问：

```text
http://服务器IP:8000
```

## 宝塔部署

1. 在服务器安装 Docker 和 Docker Compose。
2. 上传本项目到服务器目录，例如 `/www/wwwroot/mir-indexer`。
3. 在宝塔终端运行：

```bash
cd /www/wwwroot/mir-indexer
docker compose up -d --build
```

4. 在宝塔安全组/防火墙放行 `8000`，或用反向代理绑定域名。

## 命令行任务

进入容器后也可以手动触发：

```bash
python scripts/run_job.py news
python scripts/run_job.py publishers
python scripts/run_job.py full
```

## 采集策略

- 初始化：先抓 `/publ/1` 的 303 个出版商条目，再逐个进入出版商详情页，提取其中的资源详情页链接。
- 详情页：提取链接、标题、出版商、比例、文件格式、纸张幅面、文件大小、总页数/模型页数、下载链接、发布时间、分类。
- 每日增量：定时抓 `/news/` 第一页，发现新资源后抓详情；如果详情页出现新的出版商名称，则记录为待确认出版商。
- 访问礼貌：默认每次请求间隔 `2.5` 秒，失败会重试，不并发冲击源站。

## 环境变量

- `DATABASE_PATH`: SQLite 路径，默认 `/app/data/index.db`
- `REQUEST_DELAY_SECONDS`: 请求间隔秒数，默认 `2.5`
- `REQUEST_TIMEOUT_SECONDS`: 请求超时，默认 `45`
- `USER_AGENT`: 爬虫 UA
- `DAILY_CHECK_HOUR`: 每日检查小时，默认 `3`
- `DAILY_CHECK_MINUTE`: 每日检查分钟，默认 `15`

## 推送到 GitHub

```bash
git init
git add .
git commit -m "Initial mir-modeley indexer app"
git branch -M main
git remote add origin git@github.com:你的用户名/你的仓库名.git
git push -u origin main
```
