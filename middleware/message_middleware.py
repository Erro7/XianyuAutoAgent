# middleware/message_middleware.py
import asyncio
import json
import time
from enum import Enum
from typing import Dict, List, Optional, Callable, Any
from dataclasses import dataclass, field
from loguru import logger
from abc import ABC, abstractmethod

class MessagePriority(Enum):
    """消息优先级枚举"""
    LOW = 1
    NORMAL = 2
    HIGH = 3
    URGENT = 4

class MessageStatus(Enum):
    """消息状态枚举"""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRY = "retry"

@dataclass
class Message:
    """消息数据结构"""
    id: str
    chat_id: str
    user_id: str
    item_id: str
    content: str
    message_type: str = "text"
    priority: MessagePriority = MessagePriority.NORMAL
    status: MessageStatus = MessageStatus.PENDING
    timestamp: float = field(default_factory=time.time)
    retry_count: int = 0
    max_retries: int = 3
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict:
        """转换为字典格式"""
        return {
            'id': self.id,
            'chat_id': self.chat_id,
            'user_id': self.user_id,
            'item_id': self.item_id,
            'content': self.content,
            'message_type': self.message_type,
            'priority': self.priority.value,
            'status': self.status.value,
            'timestamp': self.timestamp,
            'retry_count': self.retry_count,
            'max_retries': self.max_retries,
            'metadata': self.metadata
        }

class MessageHandler(ABC):
    """消息处理器抽象基类"""
    
    @abstractmethod
    async def handle(self, message: Message) -> bool:
        """处理消息，返回是否成功"""
        pass
    
    @abstractmethod
    def can_handle(self, message: Message) -> bool:
        """判断是否能处理该消息"""
        pass

class MessageQueue:
    """消息队列实现"""
    
    def __init__(self, max_size: int = 1000):
        self.max_size = max_size
        self._queues = {
            MessagePriority.URGENT: asyncio.Queue(maxsize=max_size),
            MessagePriority.HIGH: asyncio.Queue(maxsize=max_size),
            MessagePriority.NORMAL: asyncio.Queue(maxsize=max_size),
            MessagePriority.LOW: asyncio.Queue(maxsize=max_size)
        }
        self._stats = {
            'total_messages': 0,
            'processed_messages': 0,
            'failed_messages': 0,
            'queue_sizes': {}
        }
    
    async def put(self, message: Message) -> bool:
        """添加消息到队列"""
        try:
            queue = self._queues[message.priority]
            if queue.full():
                logger.warning(f"队列已满，丢弃消息: {message.id}")
                return False
            
            await queue.put(message)
            self._stats['total_messages'] += 1
            logger.debug(f"消息已入队: {message.id}, 优先级: {message.priority.name}")
            return True
        except Exception as e:
            logger.error(f"消息入队失败: {e}")
            return False
    
    async def get(self) -> Optional[Message]:
        """按优先级获取消息"""
        # 按优先级顺序检查队列
        for priority in [MessagePriority.URGENT, MessagePriority.HIGH, 
                        MessagePriority.NORMAL, MessagePriority.LOW]:
            queue = self._queues[priority]
            if not queue.empty():
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=0.1)
                    return message
                except asyncio.TimeoutError:
                    continue
        return None
    
    def get_stats(self) -> Dict:
        """获取队列统计信息"""
        self._stats['queue_sizes'] = {
            priority.name: queue.qsize() 
            for priority, queue in self._queues.items()
        }
        return self._stats.copy()

class MessageMiddleware:
    """消息中间件核心类"""
    
    def __init__(self, max_workers: int = 5, max_queue_size: int = 1000):
        self.max_workers = max_workers
        self.message_queue = MessageQueue(max_queue_size)
        self.handlers: List[MessageHandler] = []
        self.workers: List[asyncio.Task] = []
        self.is_running = False
        self.retry_queue = asyncio.Queue()
        
        # 统计信息
        self.stats = {
            'start_time': None,
            'processed_count': 0,
            'failed_count': 0,
            'retry_count': 0
        }
    
    def register_handler(self, handler: MessageHandler):
        """注册消息处理器"""
        self.handlers.append(handler)
        logger.info(f"已注册消息处理器: {handler.__class__.__name__}")
    
    async def send_message(self, message: Message) -> bool:
        """发送消息到队列"""
        return await self.message_queue.put(message)
    
    async def start(self):
        """启动消息中间件"""
        if self.is_running:
            logger.warning("消息中间件已在运行中")
            return
        
        self.is_running = True
        self.stats['start_time'] = time.time()
        
        # 启动工作线程
        for i in range(self.max_workers):
            worker = asyncio.create_task(self._worker(f"worker-{i}"))
            self.workers.append(worker)
        
        # 启动重试处理器
        retry_worker = asyncio.create_task(self._retry_worker())
        self.workers.append(retry_worker)
        
        logger.info(f"消息中间件已启动，工作线程数: {self.max_workers}")
    
    async def stop(self):
        """停止消息中间件"""
        if not self.is_running:
            return
        
        self.is_running = False
        
        # 取消所有工作任务
        for worker in self.workers:
            worker.cancel()
        
        # 等待所有任务完成
        await asyncio.gather(*self.workers, return_exceptions=True)
        self.workers.clear()
        
        logger.info("消息中间件已停止")
    
    async def _worker(self, worker_name: str):
        """工作线程处理消息"""
        logger.info(f"工作线程 {worker_name} 已启动")
        
        while self.is_running:
            try:
                # 获取消息
                message = await self.message_queue.get()
                if not message:
                    await asyncio.sleep(0.1)
                    continue
                
                # 更新消息状态
                message.status = MessageStatus.PROCESSING
                logger.debug(f"[{worker_name}] 开始处理消息: {message.id}")
                
                # 查找合适的处理器
                handler = self._find_handler(message)
                if not handler:
                    logger.error(f"未找到合适的处理器: {message.id}")
                    message.status = MessageStatus.FAILED
                    self.stats['failed_count'] += 1
                    continue
                
                # 处理消息
                success = await handler.handle(message)
                
                if success:
                    message.status = MessageStatus.COMPLETED
                    self.stats['processed_count'] += 1
                    logger.debug(f"[{worker_name}] 消息处理成功: {message.id}")
                else:
                    # 处理失败，考虑重试
                    await self._handle_failed_message(message)
                
            except asyncio.CancelledError:
                logger.info(f"工作线程 {worker_name} 被取消")
                break
            except Exception as e:
                logger.error(f"工作线程 {worker_name} 处理消息时出错: {e}")
                await asyncio.sleep(1)
    
    async def _retry_worker(self):
        """重试处理器"""
        logger.info("重试处理器已启动")
        
        while self.is_running:
            try:
                # 每30秒检查一次重试队列
                await asyncio.sleep(30)
                
                retry_messages = []
                while not self.retry_queue.empty():
                    try:
                        message = self.retry_queue.get_nowait()
                        retry_messages.append(message)
                    except asyncio.QueueEmpty:
                        break
                
                for message in retry_messages:
                    if time.time() - message.timestamp > 300:  # 5分钟后重试
                        message.retry_count += 1
                        message.status = MessageStatus.RETRY
                        
                        if message.retry_count <= message.max_retries:
                            await self.message_queue.put(message)
                            self.stats['retry_count'] += 1
                            logger.info(f"消息重试: {message.id}, 第{message.retry_count}次")
                        else:
                            message.status = MessageStatus.FAILED
                            self.stats['failed_count'] += 1
                            logger.error(f"消息重试次数超限，标记为失败: {message.id}")
                
            except asyncio.CancelledError:
                logger.info("重试处理器被取消")
                break
            except Exception as e:
                logger.error(f"重试处理器出错: {e}")
    
    def _find_handler(self, message: Message) -> Optional[MessageHandler]:
        """查找合适的消息处理器"""
        for handler in self.handlers:
            if handler.can_handle(message):
                return handler
        return None
    
    async def _handle_failed_message(self, message: Message):
        """处理失败的消息"""
        message.status = MessageStatus.FAILED
        
        if message.retry_count < message.max_retries:
            # 加入重试队列
            await self.retry_queue.put(message)
            logger.warning(f"消息处理失败，加入重试队列: {message.id}")
        else:
            # 超过重试次数，标记为最终失败
            self.stats['failed_count'] += 1
            logger.error(f"消息处理最终失败: {message.id}")
    
    def get_stats(self) -> Dict:
        """获取统计信息"""
        current_stats = self.stats.copy()
        current_stats.update(self.message_queue.get_stats())
        
        if current_stats['start_time']:
            current_stats['uptime'] = time.time() - current_stats['start_time']
        
        return current_stats