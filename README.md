# RailBuddy - 城市轨道交通招标信息监控服务

自动监控城市轨道交通市场招标计划、招标公告、中标公告及开通运营信息，定时抓取并通过邮件推送到指定邮箱。

## 功能特性

- **多源抓取**：21 个数据源（12 个网站 + 9 个公众号），覆盖主要城市地铁公司和行业平台
- **四大分类**：招标计划、招标公告、中标公告、开通运营信息，智能自动分类
- **Web 管理面板**：可视化配置数据源、邮箱、调度，无需手动编辑 YAML
- **定时调度**：每天 8:00 / 12:00 自动抓取两次（可配置）
- **智能去重**：基于 URL+标题 MD5 哈希去重，避免重复推送
- **增量抓取**：记录每个源的上次抓取时间，只抓取新内容
- **断点续抓**：程序停止后重启，自动补偿空挡期间遗漏的内容
- **详情提取**：支持抓取详情页正文内容，不仅限于标题
- **标题清洗**：自动去除 `[设备]`/`[服务]` 等前缀标签和项目符号
- **HTML 邮件**：格式化表格、可点击链接、按分类分组展示
- **容错机制**：单个数据源失败不影响其他源，自动重试
- **SSL 兼容**：支持跳过证书验证，兼容部分证书问题网站
- **Windows 服务**：支持通过 NSSM 注册为系统服务，开机自启

## 快速开始

### 1. 环境要求

- Python 3.9+
- Windows / Linux / macOS

### 2. 安装

**Windows:**
```bash
# 双击运行 install.bat，或手动执行：
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

**Linux/macOS:**
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. 配置

编辑 `config.yaml`，修改以下关键配置：

```yaml
# 邮箱配置（必填）
email:
  smtp_server: "smtp.qq.com"      # QQ邮箱SMTP服务器
  smtp_port: 465
  use_ssl: true
  sender: "your_email@qq.com"     # 发件邮箱
  password: "your_auth_code"      # 邮箱授权码（非登录密码）
  receiver: "target@qq.com"       # 收件邮箱

# 调度配置
schedule:
  times: ["08:00", "12:00"]       # 每天抓取时间
  timezone: "Asia/Shanghai"
  fetch_on_start: true            # 启动时立即抓取一次
```

> **QQ邮箱授权码获取**：登录 QQ邮箱 → 设置 → 账户 → POP3/SMTP服务 → 开启 → 生成授权码

### 4. 添加数据源

有两种方式添加数据源：

**方式一：Web 管理面板（推荐）**

```bash
python -m railbuddy --web
```
浏览器访问 `http://localhost:5210`，在「网站数据源」和「公众号数据源」页面可视化添加、编辑、删除数据源。

**方式二：手动编辑 config.yaml**

在 `config.yaml` 的 `sources` 中添加新网站：

```yaml
sources:
  - name: "深圳地铁集团"
    type: "website"
    enabled: true
    url: "https://www.szmcob.com/"
    list_selector: ".news-list li"      # 列表页条目CSS选择器
    title_selector: "a"                 # 标题元素
    link_selector: "a"                  # 链接元素
    date_selector: ".date"              # 日期元素
    link_prefix: "https://www.szmcob.com"
    keywords: ["招标", "采购", "地铁"]
    fetch_detail: false                 # 是否抓取详情页
    timeout: 15
    request_interval: 2
```

### 已配置数据源（21 个）

系统已预配置以下数据源，开箱即用：

**网站数据源（12 个）：**

| 序号 | 名称 | URL | 状态 |
|------|------|-----|------|
| 1 | 深圳地铁招采平台 | https://www.szmcob.com/ | ✅ 已验证 |
| 2 | 广州地铁招标网 | https://www.gzmtr.com/ | ✅ 已验证 |
| 3 | 轨道交通网-招标信息 | http://www.rail-transit.com/zhaobiao/ | ✅ 已验证 |
| 4 | 轨道交通网-中标信息 | http://www.rail-transit.com/zhongbiao/ | ✅ 已验证 |
| 5 | 轨道交通网-行业新闻 | http://www.rail-transit.com/news/ | ✅ 已验证 |
| 6 | 天津轨道交通招标采购 | https://www.tjmetro.cn/ | ✅ 已验证 |
| 7 | 苏州轨道交通招采平台 | https://www.sz-mtr.com/ | ✅ 已验证 |
| 8 | 宁波轨道交通招标 | https://www.nbmt.com/ | ✅ 已验证 |
| 9 | 杭州地铁招采平台 | https://www.hzmetro.com/ | ✅ 已验证 |
| 10 | 南京地铁招标中心 | http://www.njmetro.com.cn/ | ✅ 已验证 (跳过SSL验证) |
| 11 | 郑州地铁采购公告 | https://www.zzmetro.com/ | ✅ 已验证 |
| 12 | 郑州地铁中标公示 | https://www.zzmetro.com/ | ✅ 已验证 |

**公众号数据源（9 个）：**

| 序号 | 名称 | 抓取方式 |
|------|------|----------|
| 1 | 轨道世界 | 搜狗搜索 |
| 2 | 轨道交通网 | 搜狗搜索 |
| 3 | 轨道城市 | 搜狗搜索 |
| 4 | 轨道市场 | 搜狗搜索 |
| 5 | 乙方宝 | 搜狗搜索 |
| 6 | 轨道纵横 | 搜狗搜索 |
| 7 | 都市轨道交通网 | 搜狗搜索 |
| 8 | 现代城市轨道交通 | 搜狗搜索 |
| 9 | 铁路与城轨 | 搜狗搜索 |

> 注：成都、武汉、重庆地铁官网因 JS 渲染或连接问题暂列为待验证（`enabled: false`），后续可通过 Web 面板启用。

### 5. 运行

```bash
# 启动 Web 管理面板（可视化配置数据源、邮箱、调度等）
python -m railbuddy --web

# 测试邮件发送
python -m railbuddy --test-email

# 执行一次抓取（不启动定时调度）
python -m railbuddy --once

# 查看状态
python -m railbuddy --status

# 启动服务（定时调度模式）
python -m railbuddy
```

### 6. 注册为 Windows 服务

使用 `install_service.bat` 一键安装两个 Windows 服务：抓取调度服务 + Web 管理面板服务。

**前置条件：** 安装 [NSSM](https://nssm.cc/download)，将 `nssm.exe` 放到系统 PATH 或 `C:\Windows\System32\`

```bash
# 安装并启动两个服务
install_service.bat

# 其他操作
install_service.bat uninstall    # 停止并删除两个服务
install_service.bat start        # 启动两个服务
install_service.bat stop         # 停止两个服务
install_service.bat restart      # 重启两个服务
install_service.bat status       # 查看服务状态
```

安装后会在 Windows 服务中注册：

| 服务名 | 说明 | 命令 |
|--------|------|------|
| RailBuddy | 抓取调度服务（定时抓取+邮件推送） | `python -m railbuddy` |
| RailBuddyWeb | Web 管理面板（http://localhost:5210） | `python -m railbuddy --web --port 5210` |

两个服务均设置为开机自启、异常自动重启（10 秒后）。日志文件位于 `logs/` 目录下。

## 项目结构

```
railbuddy/
├── config.yaml              # 配置文件
├── config.example.yaml      # 配置模板
├── requirements.txt         # Python 依赖
├── install.bat              # 环境安装脚本
├── install_service.bat      # Windows 服务安装脚本
├── railbuddy/               # 主程序包
│   ├── __main__.py          # 命令行入口
│   ├── app.py               # 主应用（业务编排）
│   ├── config.py            # 配置管理
│   ├── database.py          # SQLite 状态管理
│   ├── models.py            # 数据模型
│   ├── scheduler.py         # 定时调度器
│   ├── mailer.py            # 邮件发送器
│   ├── fetchers/            # 抓取器
│   │   ├── base.py          # 抽象基类
│   │   ├── website.py       # 网站抓取器
│   │   ├── wechat.py        # 公众号抓取器
│   │   └── registry.py      # 抓取器管理器
│   ├── web/                 # Web 管理面板
│   │   ├── server.py        # Flask API 服务器
│   │   └── templates/
│   │       └── index.html   # 单页管理界面
│   └── utils/               # 工具模块
│       ├── logger.py        # 日志配置
│       └── text.py          # 文本处理
├── data/                    # 数据目录（SQLite 数据库）
├── logs/                    # 日志目录
└── tests/                   # 测试
```

## 断点续抓机制

| 场景 | 处理方式 |
|------|----------|
| 正常运行 | 每次抓取记录 `last_fetch_time`，下次只抓取增量内容 |
| 程序重启 | 读取数据库，从上次位置继续，不重复抓取 |
| 程序停止数小时 | 启动时自动补偿抓取（`fetch_on_start: true`） |
| 重复 URL | 基于 URL+标题 MD5 去重，已发送的不再推送 |
| 邮件发送失败 | 条目保持未发送状态，下次自动重试 |
| 单源失败 | 不影响其他源，记录错误，下次重试 |
| 状态数据过大 | 自动清理超过 90 天的已发送记录 |

## 配置公众号抓取

微信公众号没有公开 API，提供两种方式：

1. **搜狗搜索（sogou）**：通过 `weixin.sogou.com` 搜索，无需额外服务但可能不稳定
2. **RSS 订阅（推荐）**：使用 WeRSS、feeddd 等服务将公众号转为 RSS 源

```yaml
wechat_sources:
  - name: "中国城市轨道交通协会"
    type: "wechat"
    enabled: true
    fetch_method: "rss"                    # 推荐 rss
    rss_url: "https://your-rss.com/feed"  # RSS 订阅地址
    keywords: ["招标", "采购", "中标"]
```

## 技术栈

- Python 3.9+
- Flask - Web 管理面板
- APScheduler - 定时任务调度
- requests + BeautifulSoup4 - 网页抓取
- SQLite - 状态持久化
- smtplib - 邮件发送
- PyYAML - 配置管理

## Web 管理面板

启动 Web 管理面板：

```bash
python -m railbuddy --web
# 指定端口
python -m railbuddy --web --port 8080
```

浏览器访问 `http://localhost:5210`，功能包括：

- **仪表盘**：系统状态总览、数据源抓取状态、最近发送记录
- **网站数据源**：添加/编辑/删除网站源，配置 CSS 选择器和关键词
- **公众号数据源**：添加/编辑/删除公众号源，配置搜狗/RSS抓取方式
- **抓取记录**：分页浏览所有抓取到的条目，支持按来源/状态/关键词筛选
- **发送日志**：查看邮件发送历史
- **邮箱设置**：配置 SMTP 服务器、发件邮箱、收件邮箱，支持发送测试邮件
- **调度设置**：修改定时抓取时间和时区
- **手动操作**：一键立即抓取、一键测试邮件
