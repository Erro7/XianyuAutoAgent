# middleware/handlers.py
import json
from typing import Dict, Any
from .message_middleware import MessageHandler, Message, MessageStatus
from modules.XianyuAgent import XianyuReplyBot
from services.context_manager import ChatContextManager
from loguru import logger

class ChatMessageHandler(MessageHandler):
    """聊天消息处理器"""
    def __init__(self, reply_bot: XianyuReplyBot, context_manager: ChatContextManager, xianyu_live=None):
        self.reply_bot = reply_bot
        self.context_manager = context_manager
        self.xianyu_live = xianyu_live
    
    def can_handle(self, message: Message) -> bool:
        """判断是否能处理聊天消息"""
        return message.message_type in ["text", "price", "tech", "urgent"]
    
    async def handle(self, message: Message) -> bool:
        """处理聊天消息"""
        try:
            logger.info(f"中间件处理消息: {message.id} (类型: {message.message_type})")
            
            # 获取对话历史
            context = self.context_manager.get_context_by_chat(message.chat_id)
            
            # 获取商品信息
            item_info = message.metadata.get('item_info')
            if item_info:
                item_desc = f"商品名称: {item_info.get('title', '未知')}\n"
                item_desc += f"价格: {item_info.get('soldPrice', '未知')}元\n"
                item_desc += f"描述: {item_info.get('desc', '无描述')}"
            else:
                item_desc = "商品信息获取失败"
            
            # 生成回复
            reply = self.reply_bot.generate_reply(
                user_msg=message.content,
                item_desc=item_desc,
                context=context
            )
            
            # 检查是否为价格意图，如果是则增加议价次数
            if self.reply_bot.last_intent == "price":
                self.context_manager.increment_bargain_count_by_chat(message.chat_id)
                bargain_count = self.context_manager.get_bargain_count_by_chat(message.chat_id)
                user_name = message.metadata.get('user_name', message.user_id)
                logger.info(f"用户 {user_name} 对商品 {message.item_id} 的议价次数: {bargain_count}")
            
            # 保存AI回复到上下文
            seller_id = message.metadata.get('seller_id', 'system')
            self.context_manager.add_message_by_chat(
                message.chat_id, seller_id, message.item_id, "assistant", reply
            )
            
            # 发送回复
            websocket = message.metadata.get('websocket')
            if websocket and self.xianyu_live:
                await self.xianyu_live.send_msg(websocket, message.chat_id, message.user_id, reply)
                logger.info(f"机器人回复 (中间件): {reply}")
            
            return True
            
        except Exception as e:
            logger.error(f"处理聊天消息失败: {message.id}, 错误: {e}")
            return False

class SystemMessageHandler(MessageHandler):
    """系统消息处理器"""
    
    def can_handle(self, message: Message) -> bool:
        return message.message_type == "system"
    
    async def handle(self, message: Message) -> bool:
        try:
            # 处理系统消息逻辑
            logger.info(f"处理系统消息: {message.content}")
            return True
        except Exception as e:
            logger.error(f"处理系统消息失败: {e}")
            return False

class NotificationHandler(MessageHandler):
    """通知消息处理器"""
    
    def can_handle(self, message: Message) -> bool:
        return message.message_type == "notification"
    
    async def handle(self, message: Message) -> bool:
        try:
            # 处理通知消息逻辑
            logger.info(f"处理通知消息: {message.content}")
            return True
        except Exception as e:
            logger.error(f"处理通知消息失败: {e}")
            return False