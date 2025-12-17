import asyncio
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, Callable
from loguru import logger

class BaseLive(ABC):
    """Live 抽象基类 - 定义所有 Live 实现的通用接口"""
    
    def __init__(self):
        self.is_running = False
        self.logger = logger
        self.message_manager = None
        
    def set_message_manager(self, message_manager):
        """设置消息管理器"""
        self.message_manager = message_manager
    
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
    
    @abstractmethod
    async def connect(self) -> bool:
        """建立连接"""
        pass
    
    @abstractmethod
    async def disconnect(self) -> bool:
        """断开连接"""
        pass
    
    async def start(self):
        """启动 Live 服务"""
        if self.is_running:
            self.logger.warning(f"{self.__class__.__name__} 已在运行中")
            return
            
        self.is_running = True
        self.logger.info(f"启动 {self.__class__.__name__}")
        
        try:
            if await self.connect():
                await self._run_loop()
        except Exception as e:
            self.logger.error(f"{self.__class__.__name__} 运行异常: {e}")
        finally:
            await self.stop()
    
    async def stop(self):
        """停止 Live 服务"""
        if not self.is_running:
            return
            
        self.is_running = False
        self.logger.info(f"停止 {self.__class__.__name__}")
        await self.disconnect()
    
    @abstractmethod
    async def _run_loop(self):
        """运行主循环 - 子类实现具体的消息接收逻辑"""
        pass