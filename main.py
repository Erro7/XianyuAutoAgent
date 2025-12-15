import asyncio
import os
import sys
from loguru import logger
from dotenv import load_dotenv

from modules.XianyuLive import XianyuLive

def run():
    # 加载环境变量
    load_dotenv()
    
    # 配置日志级别
    log_level = os.getenv("LOG_LEVEL", "DEBUG").upper()
    logger.remove()  # 移除默认handler
    logger.add(
        sys.stderr,
        level=log_level,
        format="<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>"
    )
    logger.info(f"日志级别设置为: {log_level}")
    
    cookies_str = os.getenv("COOKIES_STR")
    
    if not cookies_str:
        logger.error("cookies 未配置，请先配置.env文件中的COOKIES_STR")
        exit(1)
    
    logger.info(f"cookies：{cookies_str}")
    
    xianyuLive = XianyuLive(cookies_str)
    # 常驻进程
    asyncio.run(xianyuLive.main())

if __name__ == '__main__':
    run()
