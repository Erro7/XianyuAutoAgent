# middleware/message_router.py
import uuid
from typing import Dict, Any, Optional
from .message_middleware import Message, MessagePriority, MessageMiddleware
from loguru import logger

class MessageRouter:
    """消息路由器"""
    
    def __init__(self, middleware: MessageMiddleware):
        self.middleware = middleware
        self.routing_rules = {
            'urgent': MessagePriority.URGENT,
            'tech': MessagePriority.HIGH,
            'price': MessagePriority.HIGH,
            'text': MessagePriority.NORMAL,
            'system': MessagePriority.URGENT
        }
    
    def add_routing_rule(self, condition: str, priority: MessagePriority):
        """添加路由规则"""
        self.routing_rules[condition] = priority
    
    async def route_message(self, 
                          chat_id: str,
                          user_id: str, 
                          item_id: str,
                          content: str,
                          message_type: str = "text",
                          metadata: Optional[Dict[str, Any]] = None) -> bool:
        """路由消息到中间件"""
        
        # 生成消息ID
        message_id = str(uuid.uuid4())
        
        # 确定消息优先级
        priority = self._determine_priority(content, message_type)
        
        # 创建消息对象
        message = Message(
            id=message_id,
            chat_id=chat_id,
            user_id=user_id,
            item_id=item_id,
            content=content,
            message_type=message_type,
            priority=priority,
            metadata=metadata or {}
        )
        
        # 发送到中间件
        success = await self.middleware.send_message(message)
        
        if success:
            logger.debug(f"消息路由成功: {message_id}")
        else:
            logger.error(f"消息路由失败: {message_id}")
        
        return success
    
    def _determine_priority(self, content: str, message_type: str) -> MessagePriority:
        """确定消息优先级"""
        return self.routing_rules.get(message_type, MessagePriority.NORMAL)