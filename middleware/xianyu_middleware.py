
import time
from typing import Any
from loguru import logger
from base import BaseLive as XianyuLive

from modules.MessageProcessor import MessageType, BaseMiddleware

# 自定义中间件
class MessageExpiryMiddleware(BaseMiddleware):
    """消息过期检查中间件"""
    
    def __init__(self, expire_time):
        self.expire_time = expire_time

    async def __call__(self, message, next_handler):
        payload = message.payload
        message_info = payload.get("message_info")
        
        if message_info and "create_time" in message_info:
            if (time.time() * 1000 - message_info["create_time"]) > self.expire_time:
                logger.debug(f"消息已过期，跳过处理: {message.id}")
                return {"status": "expired", "message_id": message.id}
        
        return await next_handler(message)


class ManualModeMiddleware(BaseMiddleware):
    """人工接管模式检查中间件"""
    
    def __init__(self, xianyu_live: XianyuLive):
        self.xianyu_live = xianyu_live

    async def __call__(self, message, next_handler):
        # 只对用户查询消息进行人工模式检查
        if message.type == MessageType.QUERY:
            payload = message.payload
            message_info = payload.get("message_info")
            
            if message_info and self.xianyu_live.is_manual_mode(message_info["chat_id"]):
                logger.info(f"🔴 会话 {message_info['chat_id']} 处于人工接管模式，跳过自动回复")
                return {"status": "manual_mode", "chat_id": message_info["chat_id"]}
        
        return await next_handler(message)


class DeduplicationMiddleware(BaseMiddleware):
    """消息去重中间件"""
    
    def __init__(self):
        self.processed_messages = set()
        self.cleanup_interval = 300  # 5分钟清理一次

    async def __call__(self, message, next_handler):
        # 生成消息指纹
        payload = message.payload
        message_info = payload.get("message_info")
        
        if message_info:
            fingerprint = f"{message_info['chat_id']}_{message_info['send_user_id']}_{message_info['create_time']}"
            
            if fingerprint in self.processed_messages:
                logger.debug(f"重复消息，跳过处理: {fingerprint}")
                return {"status": "duplicate", "fingerprint": fingerprint}
            
            self.processed_messages.add(fingerprint)
            
            # 简单的清理策略：限制集合大小
            if len(self.processed_messages) > 10000:
                # 清理一半的旧记录
                old_messages = list(self.processed_messages)[:5000]
                for msg in old_messages:
                    self.processed_messages.discard(msg)
        
        return await next_handler(message)
    