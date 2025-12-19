import os
import asyncio
from enum import Enum
from pyee import EventEmitter
from abc import ABC, abstractmethod
from typing import TypeVar, Type, Dict, Any, Optional
from loguru import logger

class LiveEvent(Enum):
    # 连接相关事件
    CONNECTED = "connected"
    CONNECTION_LOST = "connection_lost"
    CONNECTION_FAILED = "connection_failed"
    DISCONNECTED = "disconnected"
    DISCONNECTION_FAILED = "disconnection_failed"
    RECONNECTING = "reconnecting"
    RECONNECTED = "reconnected"
    RECONNECT_FAILED = "reconnect_failed"
    
    # 消息
    RECEVICE = "recevice"
    
    # 错误事件
    ERROR = "error"

class BaseLive(EventEmitter, ABC):
    """Live 抽象基类 - 定义所有 Live 实现的通用接口"""
    
    def __init__(self):
        super().__init__()
        
        self.is_running = False
        self.logger = logger
    
    @abstractmethod
    async def connect(self) -> bool:
        """建立连接"""
        pass
    
    @abstractmethod
    async def disconnect(self) -> bool:
        """断开连接"""
        pass
    
    @abstractmethod
    async def run_loop(self):
        """运行主循环 - 子类实现具体的消息接收逻辑"""
        pass
    
    @abstractmethod
    async def on_receive(self, raw_message: Any) -> bool:
        """
        消息接收处理 - Live 的核心职责
        只负责接收原始消息并交给 MessageManager 处理
        
        Args:
            raw_message: 原始消息数据
            
        Returns:
            处理是否成功
        """
        pass
    
    async def start(self):
        """启动 Live 服务"""
        if self.is_running:
            self.logger.warning(f"{self.__class__.__name__} 已在运行中")
            return
            
        self.is_running = True
        
        try:
            if await self.connect():
                asyncio.create_task(self.run_loop())
        except Exception as e:
            self.logger.error(f"{self.__class__.__name__} 运行异常: {e}")
    
    async def stop(self):
        """停止 Live 服务"""
        if not self.is_running:
            return
            
        self.is_running = False
        await self.disconnect()
   
class BaseService(ABC):
    """服务基类 - 定义所有服务的通用接口"""
    
    def __init__(self, name: str):
        self.name = name
        self.is_running = False
        self.logger = logger
        
    @classmethod
    def _set_manager(self, manager):
        self.service_manager: Optional[BaseServiceManager] = manager
        
    async def initialize(self):
        """初始化服务"""
        pass
        
    async def start(self):
        """启动服务"""
        self.is_running = True
        pass
    
    async def stop(self):
        """停止服务"""
        self.is_running = False
        pass
        
    def get_stats(self) -> Dict[str, Any]:
        """获取服务统计信息"""
        return {
            "name": self.name,
            "is_running": self.is_running,
            "type": self.__class__.__name__
        }

# 泛型类型变量
T = TypeVar('T', bound=BaseService)
class BaseServiceManager(ABC):
    """服务管理器抽象基类"""
    
    def __init__(self):
        self.services: Dict[str, T] = {}
        self.is_initialized = False
        
    @abstractmethod
    async def initialize(self):
        """初始化服务管理器"""
        pass
    
    @abstractmethod
    def register_service(self, name: str, service: T, service_type: Type[T]) -> None:
        """注册服务"""
        pass
    
    @abstractmethod
    def get_service(self, name: str, service_type: Type[T]) -> Optional[T]:
        """获取服务实例"""
        pass
    
    @abstractmethod
    async def start_all(self):
        """启动所有服务"""
        pass
    
    @abstractmethod
    async def stop_all(self):
        """停止所有服务"""
        pass
    
class BaseApplication(ABC):
    """应用程序抽象基类"""
    
    def __init__(self):
        self.is_running = False
        self.logger = logger
        self.service_manager: Optional[BaseServiceManager] = None
        self.api = None
        self.bot = None
        
        self.cookies_str: Optional[str] = None
        self.cookies: Optional[Dict[str, str]] = None
        self.device_id: Optional[str] = None
        self.myid: Optional[str] = None 
        
        self.base_url = 'wss://wss-goofish.dingtalk.com/'
        
        # 心跳相关配置
        self.heartbeat_interval = int(os.getenv("HEARTBEAT_INTERVAL", "15"))  # 心跳间隔，默认15秒
        self.heartbeat_timeout = int(os.getenv("HEARTBEAT_TIMEOUT", "5"))     # 心跳超时，默认5秒
        
        
        # Token刷新相关配置
        self.token_refresh_interval = int(os.getenv("TOKEN_REFRESH_INTERVAL", "3600"))  # Token刷新间隔，默认1小时
        self.token_retry_interval = int(os.getenv("TOKEN_RETRY_INTERVAL", "300"))       # Token重试间隔，默认5分钟
        
        # 人工接管相关配置
        self.manual_mode_timeout = int(os.getenv("MANUAL_MODE_TIMEOUT", "3600"))  # 人工接管超时时间，默认1小时
        
        # 消息过期时间配置
        self.message_expire_time = int(os.getenv("MESSAGE_EXPIRE_TIME", "300000"))  # 消息过期时间，默认5分钟
        
        self.toggle_keywords = os.getenv("TOGGLE_KEYWORDS", "。")
        
    @abstractmethod
    async def initialize(self):
        """初始化应用程序"""
        pass
    
    @abstractmethod
    async def register_services(self):
        """注册所有服务 - 子类实现具体的服务注册逻辑"""
        pass
    
    @abstractmethod
    async def start_services(self):
        """启动所有服务"""
        pass
    
    async def start_services(self):
        """启动所有服务"""
        logger.info("正在启动服务...")
        
        # 启动服务管理器中的所有服务
        await self.service_manager.start_all()
        
        logger.info("所有服务启动完成")
        
    async def stop_services(self):
        """启动所有服务"""
        logger.info("正在停止服务...")
        
        # 停止服务管理器中的所有服务
        await self.service_manager.stop_all()
        
        logger.info("所有服务停止完成")
    
    async def start(self):
        """启动应用程序"""
        if self.is_running:
            self.logger.warning("应用程序已在运行中")
            return
            
        self.is_running = True
        self._shutdown_event = asyncio.Event()
        self.logger.info("🚀 启动应用程序")
        
        try:
            # 启动服务
            await self.start_services()
            
        except Exception as e:
            self.logger.error(f"应用程序启动失败: {e}")
            await self.stop()
            raise
    
    async def stop(self):
        """停止应用程序"""
        if not self.is_running:
            return
        
        self.logger.info("正在停止应用程序...")
        self.is_running = False
        self._shutdown_event.set()
        
        try:
            # 停止服务
            await self.stop_services()
        except Exception as e:
            self.logger.error(f"应用程序停止失败: {e}")
            
        self.logger.info("应用程序已停止")
    
    async def run(self):
        """运行应用程序主循环"""
        try:
            await self.initialize()
            await self.start()
            
            # 保持运行直到收到停止信号
            await self._shutdown_event.wait()
                
        except KeyboardInterrupt:
            self.logger.info("收到停止信号")
        except Exception as e:
            self.logger.error(f"应用程序运行异常: {e}")
        except asyncio.CancelledError:
            pass
        finally:
            self.logger.info("正在停止应用程序...")
            await self.stop()
    
