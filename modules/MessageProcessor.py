import asyncio
from typing import Dict, List, Callable, Any, Optional
from dataclasses import dataclass, asdict
from enum import Enum
from datetime import datetime
import logging
from abc import ABC, abstractmethod

# 消息类型
class MessageType(Enum):
    EVENT = "event"
    COMMAND = "command"
    QUERY = "query"

@dataclass
class Message:
    id: str
    type: MessageType
    payload: Dict[str, Any]
    chat_id: str  # 新增：会话ID
    timestamp: datetime = None
    correlation_id: str = None
    
    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.utcnow()
    
    def to_dict(self) -> Dict:
        """转换为字典格式"""
        return {
            'id': self.id,
            'type': self.type.value,
            'payload': self.payload,
            'chat_id': self.chat_id,
            'timestamp': self.timestamp.isoformat(),
            'correlation_id': self.correlation_id
        }

class BaseMiddleware(ABC):
    """中间件抽象基类"""
    
    @abstractmethod
    async def __call__(self, message: Message, next_handler: Callable) -> Any:
        """中间件处理方法"""
        pass

class BaseMessageHandler(ABC):
    """消息处理器抽象基类"""
    
    @abstractmethod
    async def handle(self, message: Message) -> Any:
        """处理消息"""
        pass
    
    @abstractmethod
    def can_handle(self, message: Message) -> bool:
        """判断是否能处理该消息"""
        pass

class ChatQueue:
    """单个会话的消息队列"""
    
    def __init__(self, chat_id: str, max_size: int = 100):
        self.chat_id = chat_id
        self.queue = asyncio.Queue(maxsize=max_size)
        self.processing = False
        self.last_activity = datetime.utcnow()
    
    async def put(self, message: Message) -> bool:
        """添加消息到队列"""
        try:
            if self.queue.full():
                return False
            await self.queue.put(message)
            self.last_activity = datetime.utcnow()
            return True
        except Exception:
            return False
    
    async def get(self) -> Optional[Message]:
        """获取消息"""
        try:
            message = await asyncio.wait_for(self.queue.get(), timeout=0.1)
            return message
        except asyncio.TimeoutError:
            return None
    
    def is_empty(self) -> bool:
        return self.queue.empty()
    
    def size(self) -> int:
        return self.queue.qsize()

class MultiChatQueueManager:
    """多会话队列管理器"""
    
    def __init__(self, max_chat_queues: int = 1000, queue_max_size: int = 100):
        self.chat_queues: Dict[str, ChatQueue] = {}
        self.max_chat_queues = max_chat_queues
        self.queue_max_size = queue_max_size
        self._lock = asyncio.Lock()
    
    async def get_or_create_queue(self, chat_id: str) -> ChatQueue:
        """获取或创建会话队列"""
        async with self._lock:
            if chat_id not in self.chat_queues:
                # 如果队列数量超限，清理最旧的队列
                if len(self.chat_queues) >= self.max_chat_queues:
                    await self._cleanup_old_queues()
                
                self.chat_queues[chat_id] = ChatQueue(chat_id, self.queue_max_size)
            
            return self.chat_queues[chat_id]
    
    async def put_message(self, message: Message) -> bool:
        """添加消息到对应会话队列"""
        queue = await self.get_or_create_queue(message.chat_id)
        return await queue.put(message)
    
    async def get_next_message(self) -> Optional[Message]:
        """轮询获取下一个消息"""
        for chat_queue in list(self.chat_queues.values()):
            if not chat_queue.is_empty():
                message = await chat_queue.get()
                if message:
                    return message
        return None
    
    async def _cleanup_old_queues(self):
        """清理旧的空队列"""
        current_time = datetime.utcnow()
        to_remove = []
        
        for chat_id, queue in self.chat_queues.items():
            # 清理5分钟内无活动且为空的队列
            if (queue.is_empty() and 
                (current_time - queue.last_activity).total_seconds() > 300):
                to_remove.append(chat_id)
        
        for chat_id in to_remove:
            del self.chat_queues[chat_id]
    
    def get_stats(self) -> Dict:
        """获取统计信息"""
        return {
            'total_queues': len(self.chat_queues),
            'queue_sizes': {chat_id: queue.size() for chat_id, queue in self.chat_queues.items()},
            'total_messages': sum(queue.size() for queue in self.chat_queues.values())
        }

class MessageProcessor:
    """消息处理器"""
    
    def __init__(self):
        self.handlers: Dict[MessageType, List[BaseMessageHandler]] = {}
        self.middlewares: List[BaseMiddleware] = []
        self.logger = logging.getLogger(__name__)
    
    def use_middleware(self, middleware: BaseMiddleware):
        """添加中间件"""
        self.middlewares.append(middleware)
        self.logger.info(f"已注册中间件: {middleware.__class__.__name__}")
    
    def register_handler(self, msg_type: MessageType, handler: BaseMessageHandler):
        """注册消息处理器"""
        if msg_type not in self.handlers:
            self.handlers[msg_type] = []
        self.handlers[msg_type].append(handler)
        self.logger.info(f"已注册处理器: {handler.__class__.__name__} for {msg_type.value}")
    
    async def process(self, message: Message):
        """处理消息"""
        async def chain(msg, idx=0):
            if idx >= len(self.middlewares):
                # 执行实际处理器
                handlers = self.handlers.get(msg.type, [])
                results = []
                for handler in handlers:
                    try:
                        if handler.can_handle(msg):
                            result = await handler.handle(msg)
                            results.append(result)
                    except Exception as e:
                        self.logger.error(f"Handler error: {e}")
                return results
            
            middleware = self.middlewares[idx]
            return await middleware(msg, lambda m: chain(m, idx + 1))
        
        return await chain(message)

# 中间件实现示例
class LoggingMiddleware(BaseMiddleware):
    """日志中间件"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
    
    async def __call__(self, message: Message, next_handler: Callable) -> Any:
        self.logger.info(f"Processing message: {message.id} in chat: {message.chat_id}")
        start_time = datetime.utcnow()
        
        try:
            result = await next_handler(message)
            duration = (datetime.utcnow() - start_time).total_seconds()
            self.logger.info(f"Message processed: {message.id}, duration: {duration:.3f}s")
            return result
        except Exception as e:
            self.logger.error(f"Message processing failed: {message.id}, error: {e}")
            raise

class ValidationMiddleware(BaseMiddleware):
    """验证中间件"""
    
    async def __call__(self, message: Message, next_handler: Callable) -> Any:
        if not message.payload:
            raise ValueError("Payload is empty")
        
        if not message.chat_id:
            raise ValueError("Chat ID is required")
        
        return await next_handler(message)

class RateLimitMiddleware(BaseMiddleware):
    """限流中间件"""
    
    def __init__(self, max_requests_per_minute: int = 60):
        self.max_requests = max_requests_per_minute
        self.request_counts: Dict[str, List[datetime]] = {}
    
    async def __call__(self, message: Message, next_handler: Callable) -> Any:
        current_time = datetime.utcnow()
        chat_id = message.chat_id
        
        # 清理过期记录
        if chat_id in self.request_counts:
            self.request_counts[chat_id] = [
                req_time for req_time in self.request_counts[chat_id]
                if (current_time - req_time).total_seconds() < 60
            ]
        else:
            self.request_counts[chat_id] = []
        
        # 检查限流
        if len(self.request_counts[chat_id]) >= self.max_requests:
            raise Exception(f"Rate limit exceeded for chat {chat_id}")
        
        # 记录请求
        self.request_counts[chat_id].append(current_time)
        
        return await next_handler(message)