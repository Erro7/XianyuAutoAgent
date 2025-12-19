import asyncio
import os
import sys
from loguru import logger
from dotenv import load_dotenv

from base import BaseApplication

from modules.XianyuApis import XianyuApis
from modules.XianyuAgent import XianyuReplyBot

from services.context_manager import ChatContextManager
from services.message_manager import MessageManager
from services.xianyu_live_manager import XianyuLiveManager

from utils.service_utils import serviceManager
from utils.xianyu_utils import trans_cookies, generate_device_id

class XianyuApplication(BaseApplication):
    """闲鱼自动代理应用主类"""
    
    def __init__(self):
        super().__init__()
        
    def _setup_logging(self):
        """配置日志系统"""
        log_level = os.getenv("LOG_LEVEL", "DEBUG").upper()
        logger.remove()
        logger.add(
            sys.stderr,
            level=log_level,
            format="<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>"
        )
        logger.info(f"日志级别设置为: {log_level}")
        
    def _validate_config(self) -> str:
        """验证配置"""
        cookies_str = os.getenv("COOKIES_STR")
        if not cookies_str:
            logger.error("cookies 未配置，请先配置.env文件中的COOKIES_STR")
            sys.exit(1)
        
        logger.info("配置验证通过")
        return cookies_str
    
    @serviceManager.register("context_manager", ChatContextManager)
    def create_context_manager(self):
        return ChatContextManager()
    
    @serviceManager.register("message_manager", MessageManager)
    def create_message_manager(self):
        return MessageManager(self)
    
    @serviceManager.register("xianyu_live", XianyuLiveManager)
    def create_xianyu_live(self):
        return XianyuLiveManager(self)
        
    async def register_services(self):
        self.create_context_manager()
        self.create_message_manager()
        self.create_xianyu_live()
        logger.info("服务注册完成")
        
    async def initialize(self):
        """初始化应用程序"""
        # 加载环境变量
        load_dotenv()
        
        # 配置日志
        self._setup_logging()
        
        self.api = XianyuApis()
        self.bot = XianyuReplyBot()
        
        # cookies 验证配置
        cookies_str = self._validate_config()
        self.cookies_str = cookies_str
        self.cookies = trans_cookies(cookies_str)
        self.api.session.cookies.update(self.cookies)  # 直接使用 session.cookies.update
        self.myid = self.cookies['unb']
        self.device_id = generate_device_id(self.myid)
        
        # 初始化心跳间隔和超时
        self.heartbeat_interval = int(os.getenv("HEARTBEAT_INTERVAL", "15"))  # 心跳间隔，默认15秒
        self.heartbeat_timeout = int(os.getenv("HEARTBEAT_TIMEOUT", "5"))     # 心跳超时，默认5秒
        
        
        # 初始化服务管理器
        logger.info("正在初始化服务管理器...")
        self.service_manager = serviceManager
        await self.register_services()
        await serviceManager.initialize(self)
        
        logger.info("应用程序初始化完成")
    
def run():
    """应用程序入口函数"""
    app = XianyuApplication()
    asyncio.run(app.run())

if __name__ == '__main__':
    run()
    