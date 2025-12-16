import uuid
from typing import Dict, Any, Optional
from .message_middleware import Message, MessageType, MultiChatQueueManager
from loguru import logger

class MessageRouter:
    """消息路由器"""
    
    def __init__(self, queue_manager: MultiChatQueueManager):
        self.queue_manager = queue_manager
        self.routing_rules: Dict[str, MessageType] = {
            'event': MessageType.EVENT,
            'command': MessageType.COMMAND,
            'query': MessageType.QUERY,
        }
    
    def add_routing_rule(self, pattern: str, msg_type: MessageType):
        """添加路由规则"""
        self.routing_rules[pattern] = msg_type
    
    async def route_message(self, 
                          chat_id: str,
                          payload: Dict[str, Any],
                          message_type: str = "query",
                          correlation_id: Optional[str] = None) -> bool:
        """路由消息到队列"""
        
        # 生成消息ID
        message_id = str(uuid.uuid4())
        
        # 确定消息类型
        msg_type = self._determine_message_type(message_type, payload)
        
        # 创建消息对象
        message = Message(
            id=message_id,
            type=msg_type,
            payload=payload,
            chat_id=chat_id,
            correlation_id=correlation_id
        )
        
        # 发送到队列管理器
        success = await self.queue_manager.put_message(message)
        
        if success:
            logger.debug(f"消息路由成功: {message_id} -> {chat_id}")
        else:
            logger.error(f"消息路由失败: {message_id} -> {chat_id}")
        
        return success
    
    def _determine_message_type(self, message_type: str, payload: Dict[str, Any]) -> MessageType:
        """确定消息类型"""
        # 根据内容智能判断消息类型
        if message_type in self.routing_rules:
            return self.routing_rules[message_type]
        
        # 默认规则
        content = payload.get('content', '').lower()
        if any(keyword in content for keyword in ['执行', '运行', '启动', '停止']):
            return MessageType.COMMAND
        elif any(keyword in content for keyword in ['事件', '通知', '提醒']):
            return MessageType.EVENT
        else:
            return MessageType.QUERY