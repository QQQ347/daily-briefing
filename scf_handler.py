# scf_handler.py - 腾讯云函数入口
import json
import sys
import traceback

# 导入 daily_briefing 中的 main 函数
from daily_briefing import main as run_briefing

def main_handler(event, context):
    """
    腾讯云函数定时触发器会调用这个函数。
    """
    print("云函数触发，开始生成简报...")
    try:
        run_briefing()
        print("简报生成完成。")
        return {
            "statusCode": 200,
            "body": json.dumps({"message": "简报生成成功"})
        }
    except Exception as e:
        print(f"简报生成失败: {e}")
        traceback.print_exc()
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)})
        }
