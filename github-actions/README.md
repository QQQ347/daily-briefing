# 每日简报 GitHub Actions 版

每天北京时间 08:00 自动运行，完全在 GitHub 云端执行，**不依赖本机开机**。

---

## 一、需要准备什么

### 1. DeepSeek API Key（必需）
- 地址：https://platform.deepseek.com/api_keys
- 注册后充值 10 元，够用约 1-2 个月（每天约 ¥0.2-0.5）
- 格式：`sk-xxxxxxxx`

### 2. Tavily Search API Key（必需，免费）
- 地址：https://app.tavily.com/home
- 免费额度每月 1000 次搜索，每天约消耗 30 次，完全够用
- 格式：`tvly-xxxxxxxx`

### 3. QQ 邮箱 SMTP 授权码（可选，用于接收邮件）
- QQ 邮箱 → 设置 → 账户 → POP3/IMAP/SMTP 服务 → 开启并获取授权码
- **注意：授权码≠QQ 密码**

---

## 二、部署步骤

### 步骤 1：创建 GitHub 仓库

1. 登录 https://github.com，点击右上角 `+` → `New repository`
2. 仓库名填 `daily-briefing`（或任意名字）
3. 选 **Private**（私有，保护 API 密钥）
4. 点击 Create repository

### 步骤 2：上传文件

把本目录下的三个文件上传到仓库根目录：
- `daily_briefing.py`
- `requirements.txt`
- `.github/workflows/daily-briefing.yml`

**方法 A（网页上传）**：
1. 仓库主页 → `Add file` → `Upload files`
2. 把 `daily_briefing.py` 和 `requirements.txt` 拖入上传
3. 再重复上传 `.github/workflows/daily-briefing.yml`（路径必须保持）

**方法 B（git 命令）**：
```bash
git clone https://github.com/你的用户名/daily-briefing.git
# 把文件复制进去
git add .
git commit -m "init"
git push
```

### 步骤 3：配置 Secrets（密钥）

1. 仓库主页 → `Settings` → `Secrets and variables` → `Actions`
2. 点击 `New repository secret`，依次添加：

| Secret 名称 | 填写内容 |
|------------|---------|
| `DEEPSEEK_API_KEY` | sk-你的DeepSeek密钥 |
| `TAVILY_API_KEY` | tvly-你的Tavily密钥 |
| `EMAIL_SENDER` | 你的QQ号@qq.com |
| `EMAIL_PASSWORD` | QQ邮箱SMTP授权码 |
| `EMAIL_RECEIVER` | 接收简报的邮箱（可以和发件相同） |

> 如果不需要邮件推送，只添加前两个即可。

### 步骤 4：测试运行

1. 仓库主页 → `Actions` 标签页
2. 左侧选 `每日全球动态简报`
3. 右侧点 `Run workflow` → `Run workflow`
4. 等待 5-10 分钟，看日志是否成功

**查看生成的简报**：
- Actions 运行完成后，点进去找 `Artifacts` 区域
- 下载 `briefing-xxxxx` 压缩包，里面是 HTML 文件

---

## 三、日常使用

### 自动运行
每天北京时间 08:00 自动触发，无需任何操作。

### 手动运行（需要时）
`Actions` → `每日全球动态简报` → `Run workflow`

可选参数：
- `date`：指定日期（默认今天），格式 `2026-05-27`
- `no_email`：勾选则不发邮件

### 接收邮件
如果配置了邮箱，每天简报生成后会直接发到你的邮箱，不需要登录 GitHub。

---

## 四、费用

| 项目 | 费用 |
|------|------|
| GitHub Actions | **免费**（公共仓库无限分钟，私有仓库每月 2000 分钟免费） |
| DeepSeek API | 约 ¥10/月 |
| Tavily Search | **免费**（1000次/月免费额度） |
| QQ 邮箱 | **免费** |
| **合计** | **约 ¥10/月** |

---

## 五、常见问题

**Q: Actions 提示 "workflow does not exist"**
- 检查 `.github/workflows/daily-briefing.yml` 文件路径是否正确

**Q: 日志报 "DEEPSEEK_API_KEY 未设置"**
- 检查 Settings → Secrets 里是否正确添加了密钥（名称区分大小写）

**Q: 搜索到的新闻为空**
- 检查 `TAVILY_API_KEY` 是否正确
- 查看 Actions 日志中的具体报错

**Q: 邮件没收到**
- 检查垃圾邮件箱
- 确认 `EMAIL_PASSWORD` 填的是 SMTP 授权码（不是 QQ 密码）
- 在 Actions 手动触发一次，查看日志

**Q: 想改成其他时间运行**
- 编辑 `.github/workflows/daily-briefing.yml` 中的 cron 表达式
- GitHub Actions 用 UTC 时间：北京时间 = UTC + 8
- 例如北京时间 07:30 → UTC 23:30 → `cron: "30 23 * * *"`
