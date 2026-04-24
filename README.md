<<<<<<< HEAD
# xms
=======
# 🦆 xms - 光鸭云盘 STRM + 302 播放服务

xms 是一个基于光鸭云盘的文件管理 + STRM 生成工具，通过 302 重定向实现 Emby/Jellyfin 快速起播。

## ✨ 功能特性

- 🔐 **扫码登录** - 打开光鸭云盘 App 扫码即可登录，无需账号密码
- 📁 **文件管理** - 浏览光鸭云盘文件列表，支持多级目录
- 🎬 **STRM 生成** - 一键同步文件夹，生成 .strm 文件供媒体库扫描
- ⚡ **302 快速起播** - STRM 内容为 302 重定向地址，Emby 直链播放，起播极快
- 🎨 **美观界面** - 深色影视风格，简洁易用
- 🐳 **Docker 部署** - 一行命令启动服务

## 快速开始

### Docker 部署

```bash
git clone https://github.com/WonderMaker123/xms.git
cd xms
docker-compose up -d
```

访问 http://your-server:9528

### 配置媒体库

1. 登录光鸭云盘
2. 进入要同步的文件夹，点击"开始同步"
3. 在 Emby/Jellyfin 添加媒体库，路径指向 `strm_output_dir`
4. 媒体库扫描后即可播放

### STRM 播放原理

```
Emby 扫描 .strm 文件
  → .strm 内容: http://xms:9528/stream/{file_id}
  → xms 收到请求，向光鸭云盘获取真实直链
  → xms 返回 302 重定向到真实直链
  → Emby 直接请求真实直链，播放！
```

## 项目结构

```
xms/
├── backend/
│   ├── main.py          # FastAPI 主程序
│   ├── config.py        # 配置管理
│   ├── guangya_client.py # 光鸭云盘 API 客户端
│   ├── strm_service.py   # STRM 生成服务
│   └── routers/
│       ├── api.py       # REST API
│       └── stream.py    # 302 流服务
├── frontend/
│   ├── index.html       # Vue3 管理界面
│   ├── styles.css       # 深色主题样式
│   └── app.js           # 前端逻辑
├── media/               # 媒体目录
│   └── strm/           # STRM 输出目录
├── config/              # 配置目录
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| PORT | 9528 | 服务端口 |
| GUANGYA_ACCESS_TOKEN | - | 光鸭云盘 Access Token |
| GUANGYA_REFRESH_TOKEN | - | 光鸭云盘 Refresh Token |

## License

MIT
>>>>>>> 716fe85 (feat: initial xms project - 光鸭云盘 STRM + 302 播放服务)
