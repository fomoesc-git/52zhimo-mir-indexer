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

## 宝塔 Docker 部署

宝塔面板如果拉取海外镜像失败，推荐用“离线镜像包上传”的方式部署。整个过程不需要阿里云账号，也不需要服务器能访问 GitHub Container Registry。

### 方案 A：手动上传镜像包，国内最稳

方法 1：在你自己的电脑或 NAS 上打包。

在一台可以正常构建 Docker 镜像的电脑或 NAS 上执行：

```bash
git clone https://github.com/fomoesc-git/52zhimo-mir-indexer.git
cd 52zhimo-mir-indexer
chmod +x scripts/build_image_archive.sh
./scripts/build_image_archive.sh
```

如果你的宝塔服务器是常见 x86_64 架构，默认即可。若服务器是 ARM 架构，可以这样打包：

```bash
PLATFORM=linux/arm64 ./scripts/build_image_archive.sh
```

打包完成后会得到：

```text
dist/52zhimo-mir-indexer.tar.gz
```

把这个文件上传到宝塔服务器，例如：

```text
/www/wwwroot/52zhimo-mir-indexer/52zhimo-mir-indexer.tar.gz
```

然后在宝塔终端或 SSH 中导入镜像：

```bash
cd /www/wwwroot/52zhimo-mir-indexer
gzip -dc 52zhimo-mir-indexer.tar.gz | docker load
```

创建数据目录：

```bash
mkdir -p /www/wwwroot/52zhimo-mir-indexer/data
```

运行容器：

```bash
docker run -d \
  --name 52zhimo-mir-indexer \
  --restart unless-stopped \
  -p 8000:8000 \
  -e TZ=Asia/Shanghai \
  -e DATABASE_PATH=/app/data/index.db \
  -e ADMIN_USERNAME=admin \
  -e ADMIN_PASSWORD="请改成一个强密码" \
  -e SECRET_KEY="请改成一串随机字符" \
  -e REQUEST_DELAY_SECONDS=2.5 \
  -e REQUEST_TIMEOUT_SECONDS=45 \
  -e USER_AGENT="52zhimo public index bot; contact: https://52zhimo.cn" \
  -e DAILY_CHECK_HOUR=3 \
  -e DAILY_CHECK_MINUTE=15 \
  -v /www/wwwroot/52zhimo-mir-indexer/data:/app/data \
  52zhimo-mir-indexer:latest
```

如果宝塔 Docker 面板不支持 `docker load`，通常可以在宝塔左侧的“终端”或服务器 SSH 里执行上面的导入命令。导入成功后，宝塔 Docker 面板的本地镜像列表里会出现 `52zhimo-mir-indexer:latest`，之后就可以只用面板的 `docker run` 能力启动。

方法 2：从 GitHub Actions 下载自动打包文件。

每次推送 `main` 后，GitHub Actions 会自动生成一个离线镜像包：

```text
52zhimo-mir-indexer-linux-amd64.tar.gz
```

下载位置：

```text
GitHub 仓库 -> Actions -> Build Docker image -> 最近一次运行 -> Artifacts
```

下载后上传到宝塔服务器，再执行：

```bash
gzip -dc 52zhimo-mir-indexer-linux-amd64.tar.gz | docker load
```

然后使用上面的 `docker run` 命令启动。

默认管理员账号是 `admin`。强烈建议运行容器时设置 `ADMIN_PASSWORD` 和 `SECRET_KEY`，不要使用默认密码。

离线包升级：

```bash
gzip -dc 52zhimo-mir-indexer.tar.gz | docker load
docker stop 52zhimo-mir-indexer
docker rm 52zhimo-mir-indexer
docker run -d \
  --name 52zhimo-mir-indexer \
  --restart unless-stopped \
  -p 8000:8000 \
  -e TZ=Asia/Shanghai \
  -e DATABASE_PATH=/app/data/index.db \
  -e ADMIN_USERNAME=admin \
  -e ADMIN_PASSWORD="请改成一个强密码" \
  -e SECRET_KEY="请改成一串随机字符" \
  -e REQUEST_DELAY_SECONDS=2.5 \
  -e REQUEST_TIMEOUT_SECONDS=45 \
  -e USER_AGENT="52zhimo public index bot; contact: https://52zhimo.cn" \
  -e DAILY_CHECK_HOUR=3 \
  -e DAILY_CHECK_MINUTE=15 \
  -v /www/wwwroot/52zhimo-mir-indexer/data:/app/data \
  52zhimo-mir-indexer:latest
```

### 方案 B：GitHub GHCR

先拉取镜像：

```bash
docker pull ghcr.io/fomoesc-git/52zhimo-mir-indexer:latest
```

创建数据目录：

```bash
mkdir -p /www/wwwroot/52zhimo-mir-indexer/data
```

运行容器：

```bash
docker run -d \
  --name 52zhimo-mir-indexer \
  --restart unless-stopped \
  -p 8000:8000 \
  -e TZ=Asia/Shanghai \
  -e DATABASE_PATH=/app/data/index.db \
  -e ADMIN_USERNAME=admin \
  -e ADMIN_PASSWORD="请改成一个强密码" \
  -e SECRET_KEY="请改成一串随机字符" \
  -e REQUEST_DELAY_SECONDS=2.5 \
  -e REQUEST_TIMEOUT_SECONDS=45 \
  -e USER_AGENT="52zhimo public index bot; contact: https://52zhimo.cn" \
  -e DAILY_CHECK_HOUR=3 \
  -e DAILY_CHECK_MINUTE=15 \
  -v /www/wwwroot/52zhimo-mir-indexer/data:/app/data \
  ghcr.io/fomoesc-git/52zhimo-mir-indexer:latest
```

访问：

```text
http://服务器IP:8000
```

更新镜像：

```bash
docker pull ghcr.io/fomoesc-git/52zhimo-mir-indexer:latest
docker stop 52zhimo-mir-indexer
docker rm 52zhimo-mir-indexer
docker run -d \
  --name 52zhimo-mir-indexer \
  --restart unless-stopped \
  -p 8000:8000 \
  -e TZ=Asia/Shanghai \
  -e DATABASE_PATH=/app/data/index.db \
  -e ADMIN_USERNAME=admin \
  -e ADMIN_PASSWORD="请改成一个强密码" \
  -e SECRET_KEY="请改成一串随机字符" \
  -e REQUEST_DELAY_SECONDS=2.5 \
  -e REQUEST_TIMEOUT_SECONDS=45 \
  -e USER_AGENT="52zhimo public index bot; contact: https://52zhimo.cn" \
  -e DAILY_CHECK_HOUR=3 \
  -e DAILY_CHECK_MINUTE=15 \
  -v /www/wwwroot/52zhimo-mir-indexer/data:/app/data \
  ghcr.io/fomoesc-git/52zhimo-mir-indexer:latest
```

## 本地运行

```bash
docker compose up --build
```

访问：

```text
http://服务器IP:8000
```

## 宝塔源码构建部署

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
- `ADMIN_USERNAME`: 管理员账号，默认 `admin`
- `ADMIN_PASSWORD`: 管理员密码，默认 `admin123456`，部署时务必修改
- `SECRET_KEY`: 登录 cookie 签名密钥，部署时务必修改

## 推送到 GitHub

```bash
git init
git add .
git commit -m "Initial mir-modeley indexer app"
git branch -M main
git remote add origin git@github.com:你的用户名/你的仓库名.git
git push -u origin main
```
