# 每日简报云部署指南

## 架构概览

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│  Tavily API   │────▶│  Python 脚本  │────▶│  DeepSeek API │
│  (网页搜索)    │     │  (编排逻辑)    │     │  (AI 写作)    │
└──────────────┘     └──────┬───────┘     └──────────────┘
                            │
                    ┌───────▼───────┐
                    │  本地 HTML 文件 │
                    │   QQ邮箱推送   │
                    └───────────────┘
```

脚本每天自动完成: 双语搜索 → 新闻收集 → AI 写作 → HTML 生成 → 邮件推送

---

## 第一步: 准备账号和密钥

### 1.1 DeepSeek API (必需)
- 访问: https://platform.deepseek.com
- 注册后获取 API Key (sk-xxx)
- 充值 10 元够用一个月 (每天约 0.3 元)
- 模型: `deepseek-v4-flash` (便宜且快)

### 1.2 Tavily Search API (必需)
- 访问: https://app.tavily.com/home
- 注册免费额度 (每月 1000 次搜索)
- 获取 API Key (tvly-xxx)
- 每天约消耗 30-40 次搜索, 免费额度完全够用

### 1.3 QQ邮箱 SMTP (可选, 用于邮件推送)
- QQ邮箱 → 设置 → 账户 → POP3/IMAP/SMTP 服务
- 开启 SMTP 服务, 获取授权码 (不是 QQ 密码!)

---

## 第二步: 购买云服务器

### 推荐配置
| 平台 | 产品 | 价格 | 推荐理由 |
|------|------|------|----------|
| 腾讯云 | 轻量应用服务器 | ¥50/月 | 国内访问快, 适合 QQ 邮箱 |
| 阿里云 | ECS 共享型 | ¥50/月 | 稳定, 生态好 |
| 华为云 | 云耀云服务器 | ¥40/月 | 性价比高 |

### 最低配置
- CPU: 1核
- 内存: 2GB
- 系统: Ubuntu 22.04 LTS
- 带宽: 1Mbps

---

## 第三步: 上传文件到服务器

将 `cloud/` 目录下所有文件上传到服务器:

```bash
# 在本地执行 (替换为你的服务器 IP)
scp -r cloud/ root@你的服务器IP:~/briefing-cloud/
```

或在服务器上手动创建文件, 将 `daily_briefing.py`, `requirements.txt`, `config.example.json`, `deploy.sh` 放到 `~/briefing-cloud/` 目录。

---

## 第四步: 配置密钥

```bash
# SSH 登录服务器
ssh root@你的服务器IP

# 进入目录
cd ~/briefing-cloud

# 复制配置文件
cp config.example.json config.json

# 编辑配置
nano config.json
```

填入你的三个密钥:
```json
{
  "deepseek": {
    "api_key": "sk-你的DeepSeek密钥",
    ...
  },
  "search": {
    "tavily_api_key": "tvly-你的Tavily密钥"
  },
  "email": {
    "sender": "你的QQ号@qq.com",
    "password": "QQ邮箱SMTP授权码",
    "receiver": "接收邮箱@qq.com"
  }
}
```

---

## 第五步: 一键部署

```bash
cd ~/briefing-cloud
chmod +x deploy.sh
bash deploy.sh
```

部署脚本会自动:
1. 安装 Python 依赖
2. 创建输出目录
3. 检查配置
4. 测试运行一次
5. 设置每天早上 8:00 的定时任务

---

## 日常使用

```bash
# 手动运行一次
cd ~/briefing-cloud && ~/briefing-venv/bin/python daily_briefing.py

# 只看搜索、不生成 (排查问题用)
cd ~/briefing-cloud && ~/briefing-venv/bin/python daily_briefing.py --search-only

# 不发送邮件
cd ~/briefing-cloud && ~/briefing-venv/bin/python daily_briefing.py --no-email

# 查看定时任务日志
tail -f ~/briefing-cron.log

# 查看生成的简报
ls ~/briefings/

# 修改定时时间 (改为每天 9:30)
crontab -e
```

---

## 费用估算

| 项目 | 月费用 |
|------|--------|
| 云服务器 | ¥50 |
| DeepSeek API | ¥10 |
| Tavily API | ¥0 (免费额度) |
| QQ邮箱 | ¥0 |
| **合计** | **约 ¥60/月** |

---

## 故障排查

**Q: 搜索不到任何新闻?**
- 检查 Tavily API Key 是否正确
- 运行 `python daily_briefing.py --search-only` 看具体报错

**Q: 生成的简报是英文的?**
- 检查 DeepSeek API Key 是否正确
- 检查 prompt 是否被正确传递

**Q: 邮件没收到?**
- 确认 QQ邮箱 SMTP 授权码 (不是密码!)
- 检查垃圾邮件箱
- 运行 `python daily_briefing.py` 看报错

**Q: 定时任务没执行?**
- 检查时区: `timedatectl` 确认是 Asia/Shanghai
- 检查 cron 日志: `grep CRON /var/log/syslog | tail`
