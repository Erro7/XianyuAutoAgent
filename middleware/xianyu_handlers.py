
import time
from typing import Any
from loguru import logger
from base import BaseLive as XianyuLive


from modules.MessageProcessor import MessageType, BaseMessageHandler


class XianyuChatHandler(BaseMessageHandler):
    """闲鱼聊天消息处理器"""
    
    def __init__(self, xianyu_live: XianyuLive):
        self.xianyu_live = xianyu_live

    async def handle(self, message):
        """处理用户聊天消息"""
        try:
            payload = message.payload
            message_info = payload["message_info"]
            websocket = payload["websocket"]
            
            # 时效性验证
            if (time.time() * 1000 - message_info["create_time"]) > self.xianyu_live.message_expire_time:
                logger.debug("过期消息丢弃")
                return {"status": "expired"}

            logger.info(f"用户: {message_info['send_user_name']} (ID: {message_info['send_user_id']}), "
                       f"商品: {message_info['item_id']}, 会话: {message_info['chat_id']}, "
                       f"消息: {message_info['send_message']}")

            # 添加用户消息到上下文
            self.xianyu_live.context_manager.add_message_by_chat(
                message_info["chat_id"], 
                message_info["send_user_id"], 
                message_info["item_id"], 
                "user", 
                message_info["send_message"]
            )

            # 获取商品信息
            item_info = await self._get_item_info(message_info["item_id"])
            if not item_info:
                return {"status": "error", "message": "无法获取商品信息"}

            # 生成回复
            item_description = f"{item_info['desc']};当前商品售卖价格为:{str(item_info['soldPrice'])}"
            context = self.xianyu_live.context_manager.get_context_by_chat(message_info["chat_id"])
            
            bot_reply = self.xianyu_live.bot.generate_reply(
                message_info["send_message"],
                item_description,
                context=context
            )

            # 处理议价逻辑
            if self.xianyu_live.bot.last_intent == "price":
                self.xianyu_live.context_manager.increment_bargain_count_by_chat(message_info["chat_id"])
                bargain_count = self.xianyu_live.context_manager.get_bargain_count_by_chat(message_info["chat_id"])
                logger.info(f"用户 {message_info['send_user_name']} 对商品 {message_info['item_id']} 的议价次数: {bargain_count}")

            # 添加机器人回复到上下文
            self.xianyu_live.context_manager.add_message_by_chat(
                message_info["chat_id"], 
                self.xianyu_live.myid, 
                message_info["item_id"], 
                "assistant", 
                bot_reply
            )

            # 发送回复
            logger.info(f"机器人回复: {bot_reply}")
            await self.xianyu_live.send_msg(
                websocket, 
                message_info["chat_id"], 
                message_info["send_user_id"], 
                bot_reply
            )

            return {
                "status": "success",
                "reply": bot_reply,
                "intent": self.xianyu_live.bot.last_intent
            }

        except Exception as e:
            logger.error(f"处理聊天消息失败: {e}")
            return {"status": "error", "message": str(e)}

    def can_handle(self, message):
        return message.type == MessageType.QUERY

    async def _get_item_info(self, item_id):
        """获取商品信息"""
        item_info = self.xianyu_live.context_manager.get_item_info(item_id)
        if not item_info:
            logger.info(f"从API获取商品信息: {item_id}")
            api_result = self.xianyu_live.xianyu.get_item_info(item_id)
            if 'data' in api_result and 'itemDO' in api_result['data']:
                item_info = api_result['data']['itemDO']
                self.xianyu_live.context_manager.save_item_info(item_id, item_info)
            else:
                logger.warning(f"获取商品信息失败: {api_result}")
                return None
        return item_info


class XianyuCommandHandler(BaseMessageHandler):
    """闲鱼命令处理器"""
    
    def __init__(self, xianyu_live: XianyuLive):
        self.xianyu_live = xianyu_live

    async def handle(self, message):
        """处理命令消息"""
        try:
            payload = message.payload
            message_info = payload["message_info"]
            
            # 处理人工接管切换命令
            if self.xianyu_live.check_toggle_keywords(message_info["send_message"]):
                mode = self.xianyu_live.toggle_manual_mode(message_info["chat_id"])
                if mode == "manual":
                    logger.info(f"🔴 已接管会话 {message_info['chat_id']} (商品: {message_info['item_id']})")
                else:
                    logger.info(f"🟢 已恢复会话 {message_info['chat_id']} 的自动回复 (商品: {message_info['item_id']})")
                
                return {"status": "success", "mode": mode}

            # 记录卖家人工回复
            self.xianyu_live.context_manager.add_message_by_chat(
                message_info["chat_id"], 
                self.xianyu_live.myid, 
                message_info["item_id"], 
                "assistant", 
                message_info["send_message"]
            )
            
            logger.info(f"卖家人工回复 (会话: {message_info['chat_id']}, 商品: {message_info['item_id']}): {message_info['send_message']}")
            
            return {"status": "success", "type": "manual_reply"}

        except Exception as e:
            logger.error(f"处理命令失败: {e}")
            return {"status": "error", "message": str(e)}

    def can_handle(self, message):
        return message.type == MessageType.COMMAND


class XianyuEventHandler(BaseMessageHandler):
    """闲鱼事件处理器"""
    
    def __init__(self, xianyu_live: XianyuLive):
        self.xianyu_live = xianyu_live

    async def handle(self, message):
        """处理事件消息"""
        try:
            payload = message.payload
            original_message = payload["original_message"]
            
            # 处理订单状态事件
            if '3' in original_message and 'redReminder' in original_message['3']:
                reminder = original_message['3']['redReminder']
                user_id = original_message['1'].split('@')[0]
                user_url = f'https://www.goofish.com/personal?userId={user_id}'
                
                if reminder == '等待买家付款':
                    logger.info(f'等待买家 {user_url} 付款')
                elif reminder == '交易关闭':
                    logger.info(f'买家 {user_url} 交易关闭')
                elif reminder == '等待卖家发货':
                    logger.info(f'交易成功 {user_url} 等待卖家发货')
                
                return {"status": "success", "event_type": "order_status", "reminder": reminder}

            return {"status": "success", "event_type": "unknown"}

        except Exception as e:
            logger.error(f"处理事件失败: {e}")
            return {"status": "error", "message": str(e)}

    def can_handle(self, message):
        return message.type == MessageType.EVENT
