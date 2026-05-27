#!/bin/bash
# ===================================================
# 每日简报云部署脚本
# 在云服务器 (Ubuntu 22.04+) 上运行
# ===================================================

set -e

echo "===== 每日简报云部署 ====="

# 1. 安装 Python 依赖
echo "[1/5] 安装 Python 依赖..."
sudo apt update -qq
sudo apt install -y python3 python3-pip python3-venv 2>/dev/null

# 创建虚拟环境
python3 -m venv ~/briefing-venv
source ~/briefing-venv/bin/activate
pip install -r requirements.txt

# 2. 创建输出目录
echo "[2/5] 创建输出目录..."
mkdir -p ~/briefings

# 3. 检查配置文件
echo "[3/5] 检查配置文件..."
if [ ! -f ~/briefing-cloud/config.json ]; then
    echo ""
    echo "⚠️  请先配置 API 密钥!"
    echo ""
    echo "步骤:"
    echo "  1. 获取 DeepSeek API Key: https://platform.deepseek.com/api_keys"
    echo "  2. 获取 Tavily API Key: https://app.tavily.com/home"
    echo "  3. 获取 QQ邮箱 SMTP 授权码: QQ邮箱设置 → 账户 → POP3/SMTP 服务"
    echo ""
    echo "然后编辑: ~/briefing-cloud/config.json"
    echo "  cp ~/briefing-cloud/config.example.json ~/briefing-cloud/config.json"
    echo "  nano ~/briefing-cloud/config.json"
    echo ""
    exit 1
fi

# 4. 测试运行
echo "[4/5] 测试运行..."
python3 daily_briefing.py --no-email || {
    echo ""
    echo "⚠️  测试运行失败, 请检查 config.json 中的 API 密钥是否正确"
    exit 1
}

# 5. 设置定时任务
echo "[5/5] 设置定时任务 (每天早上8:00执行)..."
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_PYTHON="$HOME/briefing-venv/bin/python"
CRON_JOB="0 8 * * * cd $SCRIPT_DIR && $VENV_PYTHON $SCRIPT_DIR/daily_briefing.py >> $HOME/briefing-cron.log 2>&1"

# 添加 crontab (避免重复)
(crontab -l 2>/dev/null | grep -v "daily_briefing.py"; echo "$CRON_JOB") | crontab -

echo ""
echo "========================================"
echo "  ✅ 部署完成!"
echo "========================================"
echo ""
echo "定时任务: 每天早上 8:00 (北京时间)"
echo "日志文件: ~/briefing-cron.log"
echo "简报目录: ~/briefings/"
echo ""
echo "手动运行: cd ~/briefing-cloud && ~/briefing-venv/bin/python daily_briefing.py"
echo "查看日志: tail -f ~/briefing-cron.log"
echo ""
