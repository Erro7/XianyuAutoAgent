import asyncio
from loguru import logger
from typing import Dict, Any, Optional, List
from datetime import datetime
import uuid

from middleware.message_middleware import (
    MessageProcessor, Message, MessageType,
    LoggingMiddleware, ValidationMiddleware, RateLimitMiddleware,
    BaseMessageHandler
)

class QueueMessageManager:
    """队列驱动的消息管理器"""
    
    def __init__(self,
                 max_workers: int = 3,
                 queue_max_size: int = 100):

        # 初始化组件
        self.message_processor = MessageProcessor()
        
        # 纯队列驱动
        self.message_queue = asyncio.Queue(maxsize=queue_max_size)
        
        # 工作线程管理
        self.max_workers = max_workers
        self.workers: List[asyncio.Task] = []
        self.is_running = False
        
        # 统计信息
        self.stats = {
            'start_time': None,
            'processed_count': 0,
            'failed_count': 0,
            'total_received': 0,
        }
        
        self.logger = logger
        
        # 注册默认中间件
        self._setup_default_middlewares()
    
    def _setup_default_middlewares(self):
        """设置默认中间件"""
        self.message_processor.use_middleware(ValidationMiddleware())
        self.message_processor.use_middleware(LoggingMiddleware())
        self.message_processor.use_middleware(RateLimitMiddleware(max_requests_per_minute=100))
    
    def register_handler(self, msg_type: MessageType, handler: BaseMessageHandler):
        """注册消息处理器"""
        self.message_processor.register_handler(msg_type, handler)
    
    def use_middleware(self, middleware):
        """添加自定义中间件"""
        self.message_processor.use_middleware(middleware)
    
    def send_message(self, 
                    chat_id: str,
                    payload: Dict[str, Any],
                    message_type: str = "query",
                    correlation_id: Optional[str] = None,
                    priority: str = "normal") -> bool:
        """队列驱动的消息发送"""
        try:
            self.stats['total_received'] += 1
            
            # 直接创建 Message 对象
            message = Message(
                id=str(uuid.uuid4()),
                type=self._determine_message_type(message_type, payload),
                payload=payload,
                chat_id=chat_id,
                correlation_id=correlation_id
            )
            
            try:
                self.message_queue.put_nowait(message)
                return True
            except asyncio.QueueFull:
                self.logger.debug(f"队列已满，丢弃消息: {chat_id}")
                return False
            
        except Exception as e:
            self.logger.debug(f"消息发送异常: {e}")
            return False
    
    def _determine_message_type(self, message_type: str, payload: Dict[str, Any]) -> MessageType:
        """确定消息类型"""
        type_mapping = {
            'event': MessageType.EVENT,
            'command': MessageType.COMMAND,
            'query': MessageType.QUERY,
        }
        
        if message_type in type_mapping:
            return type_mapping[message_type]
        
        # 智能判断
        content = str(payload.get('message_info', {}).get('send_message', '')).lower()
        if any(keyword in content for keyword in ['执行', '运行', '启动', '停止']):
            return MessageType.COMMAND
        elif any(keyword in content for keyword in ['事件', '通知', '提醒']):
            return MessageType.EVENT
        else:
            return MessageType.QUERY
    
    async def _queue_worker(self, worker_name: str):
        """纯队列工作线程 - queue.get() 自动等待新消息"""
        self.logger.info(f"队列工作线程 {worker_name} 已启动")
        
        while self.is_running:
            try:
                try:
                    message = await asyncio.wait_for(self.message_queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                
                self.logger.debug(f"[{worker_name}] 开始处理消息: {message.id}")
                
                try:
                    result = await self.message_processor.process(message)
                    self.stats['processed_count'] += 1
                    self.logger.debug(f"[{worker_name}] 消息处理成功: {message.id}: {result}")
                    
                except Exception as e:
                    self.stats['failed_count'] += 1
                    self.logger.error(f"[{worker_name}] 消息处理失败: {message.id}, 错误: {e}")
                
            except asyncio.CancelledError:
                self.logger.info(f"纯队列工作线程 {worker_name} 被取消")
                break
            except Exception as e:
                self.logger.error(f"纯队列工作线程 {worker_name} 出错: {e}")
                await asyncio.sleep(1)
    
    async def start(self):
        """启动队列消息管理器"""
        if self.is_running:
            self.logger.warning("消息管理器已在运行中")
            return
        
        self.is_running = True
        self.stats['start_time'] = datetime.utcnow()
        
        # 🔥 简化：只启动队列工作线程，queue.get() 会自动等待
        for i in range(self.max_workers):
            worker = asyncio.create_task(self._queue_worker(f"queue-worker-{i}"))
            self.workers.append(worker)
        
        self.logger.info(f"队列消息管理器已启动，工作线程数: {self.max_workers}")
    
    async def stop(self):
        """停止消息管理器"""
        if not self.is_running:
            return
        
        self.is_running = False
        
        # 取消所有工作任务
        for worker in self.workers:
            worker.cancel()
        
        # 等待所有任务完成
        await asyncio.gather(*self.workers, return_exceptions=True)
        self.workers.clear()
        
        self.logger.info("纯队列消息管理器已停止")
    
    def get_stats(self) -> Dict:
        """获取统计信息"""
        current_stats = self.stats.copy()
        
        if current_stats['start_time']:
            current_stats['uptime'] = (datetime.utcnow() - current_stats['start_time']).total_seconds()
        
        current_stats['queue_size'] = self.message_queue.qsize()
        current_stats['active_workers'] = len([w for w in self.workers if not w.done()])
        
        return current_stats

# 保持兼容性
class MessageManager(QueueMessageManager):
    """消息管理器"""
    pass