import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from loguru import logger
from typing import Dict, Any, Optional, List
from datetime import datetime
import uuid
import queue
import time

from base import BaseApplication

from modules.MessageProcessor import (
    MessageProcessor, Message, MessageType,
    LoggingMiddleware, ValidationMiddleware, RateLimitMiddleware,
    BaseMessageHandler
)
from modules.XianyuApis import XianyuApis
from modules.XianyuAgent import XianyuReplyBot
from modules.XianyuManualMode import XianyuManualMode

from middleware.xianyu_handlers import XianyuChatHandler, XianyuCommandHandler, XianyuEventHandler
from middleware.xianyu_middleware import MessageExpiryMiddleware, ManualModeMiddleware, DeduplicationMiddleware

from services.context_manager import ChatContextManager

from utils.message_utils import XianyuMessageUtils

class ThreadedMessageManager:
    """线程的消息管理器"""
    
    def __init__(self,
                 max_workers: int = 3,
                 queue_max_size: int = 100):

        # 初始化组件
        self.message_processor = MessageProcessor()
        
        # 使用线程安全的队列
        self.message_queue = queue.Queue(maxsize=queue_max_size)
        self.chat_queues: Dict[str, queue.Queue] = {}
        
        # 线程池管理
        self.max_workers = max_workers
        self.thread_pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="msg-worker")
        self.worker_threads: List[threading.Thread] = []
        self.is_running = False
        self._stop_event = threading.Event()
        
        # 线程锁
        self._queue_lock = threading.Lock()
        
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
    
    def put_message(self,
                    chat_id: str,
                    payload: Dict[str, Any],
                    message_type: str = "query",
                    correlation_id: Optional[str] = None) -> bool:
        """发送消息到队列"""
        try:
            self.stats['total_received'] += 1
            
            # 创建 Message 对象
            message = Message(
                id=str(uuid.uuid4()),
                type=self._determine_message_type(message_type, payload),
                payload=payload,
                chat_id=chat_id,
                correlation_id=correlation_id
            )
            
            return self._put_message(message)
            
        except Exception as e:
            self.logger.debug(f"消息发送异常: {e}")
            return False
    
    def _put_message(self, message: Message) -> bool:
        """将消息放入队列"""
        try:
            # 使用线程安全的队列
            self.message_queue.put(message, block=False)
            return True
        except queue.Full:
            self.logger.debug(f"队列已满，丢弃消息: {message.chat_id}")
            return False
        except Exception as e:
            self.logger.error(f"消息处理失败: {e}")
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
    
    def _thread_worker(self, worker_name: str):
        """线程工作函数"""
        self.logger.info(f"消息处理线程 {worker_name} 已启动 (线程ID: {threading.get_ident()})")
        
        while not self._stop_event.is_set():
            try:
                # 从队列获取消息，设置超时避免阻塞
                try:
                    message: Message = self.message_queue.get(timeout=1.0)
                except queue.Empty:
                    continue
                
                self.logger.debug(f"[{worker_name}] 开始处理消息: {message.id} from chat: {message.chat_id} (线程: {threading.get_ident()})")
                
                try:
                    # 在线程中处理消息 - 需要在新的事件循环中运行异步代码
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    try:
                        result = loop.run_until_complete(self.message_processor.process(message))
                        self.stats['processed_count'] += 1
                        self.logger.debug(f"[{worker_name}] 消息处理成功: {message.id}: {result}")
                    finally:
                        loop.close()
                    
                except Exception as e:
                    self.stats['failed_count'] += 1
                    self.logger.error(f"[{worker_name}] 消息处理失败: {message.id}, 错误: {e}")
                finally:
                    # 标记任务完成
                    self.message_queue.task_done()
                
            except Exception as e:
                self.logger.error(f"线程工作函数 {worker_name} 出错: {e}")
                time.sleep(1)
        
        self.logger.info(f"消息处理线程 {worker_name} 已停止")

    def start(self):
        """启动消息管理器"""
        if self.is_running:
            self.logger.warning("消息管理器已在运行中")
            return
        
        self.is_running = True
        self._stop_event.clear()
        self.stats['start_time'] = datetime.utcnow()
        
        # 创建并启动工作线程
        for i in range(self.max_workers):
            worker_name = f"thread-worker-{i}"
            worker_thread = threading.Thread(
                target=self._thread_worker,
                args=(worker_name,),
                name=worker_name,
                daemon=True  # 设置为守护线程
            )
            worker_thread.start()
            self.worker_threads.append(worker_thread)
        
        self.logger.info(f"线程消息管理器已启动，工作线程数: {self.max_workers}")
    
    def stop(self, timeout: float = 10.0):
        """停止消息管理器"""
        if not self.is_running:
            return
        
        self.logger.info("正在停止消息管理器...")
        self.is_running = False
        
        # 设置停止事件
        self._stop_event.set()
        
        # 等待所有线程完成
        for worker_thread in self.worker_threads:
            worker_thread.join(timeout=timeout)
            if worker_thread.is_alive():
                self.logger.warning(f"线程 {worker_thread.name} 未能在超时时间内停止")
        
        # 清理线程列表
        self.worker_threads.clear()
        
        # 关闭线程池
        self.thread_pool.shutdown(wait=True)
        
        self.logger.info("线程消息管理器已停止")
    
    def get_stats(self) -> Dict:
        """获取统计信息"""
        current_stats = self.stats.copy()
        
        if current_stats['start_time']:
            current_stats['uptime'] = (datetime.utcnow() - current_stats['start_time']).total_seconds()
        
        current_stats['queue_size'] = self.message_queue.qsize()
        current_stats['active_workers'] = len([t for t in self.worker_threads if t.is_alive()])
        current_stats['thread_info'] = [
            {
                'name': t.name,
                'ident': t.ident,
                'is_alive': t.is_alive()
            }
            for t in self.worker_threads
        ]
        
        return current_stats

# 异步包装器，用于在异步环境中使用线程消息管理器
class AsyncThreadedMessageManager:
    """异步线程消息管理器包装器"""
    
    def __init__(self, *args, **kwargs):
        self.threaded_manager = ThreadedMessageManager(*args, **kwargs)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="async-wrapper")
    
    def register_handler(self, msg_type: MessageType, handler: BaseMessageHandler):
        """注册消息处理器"""
        self.threaded_manager.register_handler(msg_type, handler)
    
    def use_middleware(self, middleware):
        """添加自定义中间件"""
        self.threaded_manager.use_middleware(middleware)
    
    async def put_message(self, 
                      chat_id: str,
                      payload: Dict[str, Any],
                      message_type: str = "query",
                      correlation_id: Optional[str] = None) -> bool:
      """异步发送消息"""
      loop = asyncio.get_event_loop()
      return await loop.run_in_executor(
          self._executor, 
          lambda: self.threaded_manager.put_message(
              chat_id=chat_id,
              payload=payload,
              message_type=message_type,
              correlation_id=correlation_id
          )
      )
    
    async def start(self):
        """异步启动"""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(self._executor, self.threaded_manager.start)
    
    async def stop(self):
        """异步停止"""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(self._executor, self.threaded_manager.stop)
        self._executor.shutdown(wait=True)
    
    def get_stats(self) -> Dict:
        """获取统计信息"""
        return self.threaded_manager.get_stats()

class MessageManager(AsyncThreadedMessageManager):
    """消息管理器"""
    
    def __init__(self, app: BaseApplication, message_expire_time: int = 60 * 60):
        super().__init__()
        if app is None:
            raise ValueError("app is required")
        
        self.message_expire_time = message_expire_time
        self.app = app
        self.api: XianyuApis = app.api
        self.bot: XianyuReplyBot = app.bot
        self.manual_mode = XianyuManualMode()
        self._setup_custom_middlewares()
        self._setup_message_handlers()

    def _setup_custom_middlewares(self):
        """设置自定义中间件"""
        
        # 添加消息过期检查中间件
        self.use_middleware(MessageExpiryMiddleware(expire_time=self.message_expire_time))
        
        # 添加人工接管检查中间件
        self.use_middleware(ManualModeMiddleware(manual_mode=self.manual_mode))
        
        # 添加消息去重中间件
        self.use_middleware(DeduplicationMiddleware())
        
    def _setup_message_handlers(self):
        """设置消息处理器"""
        context_manager = self.app.service_manager.get_service('context_manager', ChatContextManager)
        # 注册聊天消息处理器
        self.register_handler(MessageType.QUERY, XianyuChatHandler(
            context_manager=context_manager, 
            api_service=self.api,
            bot_service=self.bot,
            message_expire_time=self.message_expire_time, 
            myid=self.app.myid
        ))
        
        # 注册命令处理器（如人工接管切换）
        self.register_handler(MessageType.COMMAND, XianyuCommandHandler(
            context_manager=context_manager,
            manual_mode=self.manual_mode,
            toggle_keywords=self.app.toggle_keywords,
            myid=self.app.myid
        ))
        
        # 注册事件处理器（如订单状态变更）
        self.register_handler(MessageType.EVENT, XianyuEventHandler())

    async def handle_message(self, message: dict) -> bool:
        """处理消息"""
        
        # 如果不是同步包消息，直接返回
        if not XianyuMessageUtils.is_sync_package(message):
            return True

        # 获取并解密数据
        message = await XianyuMessageUtils.decrypt_sync_data(message)
        if not message:
            return True
            
        # 检查订单消息
        if await XianyuMessageUtils.handle_order_message(message):
            return True

        # 检查输入状态消息
        if XianyuMessageUtils.is_typing_status(message):
            logger.debug("用户正在输入")
            return True

        # 检查是否为聊天消息
        if not XianyuMessageUtils.is_chat_message(message):
            logger.debug("其他非聊天消息")
            return True

        # 提取消息信息
        message_info = XianyuMessageUtils.extract_message_info(message)
        if not message_info:
            return True

        # 判断消息类型
        message_type = XianyuMessageUtils.determine_message_type(
            message_info, self.app.myid, self.app.toggle_keywords
        )
        
        # 构建消息载荷
        payload = {
            "original_message": message,
            "message_info": message_info,
        }
        
        return await self.put_message(
            chat_id=message_info['chat_id'],
            payload=payload,
            message_type=message_type,
            correlation_id=message_info.get("message_id")
        )
