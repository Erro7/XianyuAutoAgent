# utils/message_utils.py
import base64
import json
import time
from typing import Dict, Any, Optional
from loguru import logger

from utils.xianyu_utils import generate_mid, decrypt

class XianyuMessageUtils:
    """闲鱼消息处理工具类"""
    
    @staticmethod
    async def send_ack(message_data: Dict, websocket) -> bool:
        """发送ACK响应"""
        try:
            if "headers" in message_data and isinstance(message_data, dict) and "mid" in message_data["headers"]:
                ack = {
                    "code": 200,
                    "headers": {
                        "mid": message_data["headers"].get('mid', generate_mid()),
                        "sid": message_data["headers"].get("sid", "")
                    }
                }
                # 复制其他可能的header字段
                for key in ["app-key", "ua", "dt"]:
                    if key in message_data["headers"]:
                        ack["headers"][key] = message_data["headers"][key]
                await websocket.send(json.dumps(ack))
                return True
        except Exception as e:
            logger.debug(f"发送ACK失败: {e}")
            return False
    
    @staticmethod
    def is_sync_package(message_data: Dict) -> bool:
        """判断是否为同步包消息"""
        try:
            return (
                isinstance(message_data, dict)
                and "body" in message_data
                and "syncPushPackage" in message_data["body"]
                and "data" in message_data["body"]["syncPushPackage"]
                and len(message_data["body"]["syncPushPackage"]["data"]) > 0
            )
        except Exception:
            return False
    
    @staticmethod
    async def decrypt_sync_data(message_data: Dict) -> Optional[Dict]:
        """解密同步数据"""
        try:
            sync_data = message_data["body"]["syncPushPackage"]["data"][0]
            
            if "data" not in sync_data:
                logger.debug("同步包中无data字段")
                return None

            data = sync_data["data"]
            try:
                # 尝试直接解码
                data = base64.b64decode(data).decode("utf-8")
                message = json.loads(data)
                return message
            except Exception:
                # 需要解密
                try:
                    decrypted_data = decrypt(data)
                    message = json.loads(decrypted_data)
                    return message
                except Exception as e:
                    logger.error(f"消息解密失败: {e}")
                    return None
        except Exception as e:
            logger.error(f"解密同步数据失败: {e}")
            return None
    
    @staticmethod
    def is_chat_message(message: Dict) -> bool:
        """判断是否为用户聊天消息"""
        try:
            return (
                isinstance(message, dict) 
                and "1" in message 
                and isinstance(message["1"], dict)
                and "10" in message["1"]
                and isinstance(message["1"]["10"], dict)
                and "reminderContent" in message["1"]["10"]
            )
        except Exception:
            return False
    
    @staticmethod
    def is_typing_status(message: Dict) -> bool:
        """判断是否为用户正在输入状态消息"""
        try:
            return (
                isinstance(message, dict)
                and "1" in message
                and isinstance(message["1"], list)
                and len(message["1"]) > 0
                and isinstance(message["1"][0], dict)
                and "1" in message["1"][0]
                and isinstance(message["1"][0]["1"], str)
                and "@goofish" in message["1"][0]["1"]
            )
        except Exception:
            return False
    
    @staticmethod
    def is_system_message(message: Dict) -> bool:
        """判断是否为系统消息"""
        try:
            return (
                isinstance(message, dict)
                and "3" in message
                and isinstance(message["3"], dict)
                and "needPush" in message["3"]
                and message["3"]["needPush"] == "false"
            )
        except Exception:
            return False
    
    @staticmethod
    def extract_message_info(message: Dict) -> Optional[Dict[str, Any]]:
        """提取消息信息"""
        try:
            create_time = int(message["1"]["5"])
            send_user_name = message["1"]["10"]["reminderTitle"]
            send_user_id = message["1"]["10"]["senderUserId"]
            send_message = message["1"]["10"]["reminderContent"]
            
            # 获取商品ID和会话ID
            url_info = message["1"]["10"]["reminderUrl"]
            item_id = url_info.split("itemId=")[1].split("&")[0] if "itemId=" in url_info else None
            chat_id = message["1"]["2"].split('@')[0]
            
            if not item_id:
                logger.warning("无法获取商品ID")
                return None

            return {
                "create_time": create_time,
                "send_user_name": send_user_name,
                "send_user_id": send_user_id,
                "send_message": send_message,
                "item_id": item_id,
                "chat_id": chat_id,
                "message_id": message["1"].get("1", "")
            }
        except Exception as e:
            logger.error(f"提取消息信息失败: {e}")
            return None
    
    @staticmethod
    def determine_message_type(message_info: Dict, seller_id: str, toggle_keywords: str) -> str:
        """确定消息类型"""
        # 检查是否为卖家控制命令
        if message_info["send_user_id"] == seller_id:
            message_stripped = message_info["send_message"].strip()
            if message_stripped in toggle_keywords:
                return "command"
            return "event"  # 卖家的其他消息作为事件处理
        
        # 用户消息默认为查询
        return "query"
    
    @staticmethod
    async def handle_order_message(message: Dict) -> bool:
        """处理订单消息"""
        try:
            if '3' in message and 'redReminder' in message['3']:
                user_id = message['1'].split('@')[0]
                user_url = f'https://www.goofish.com/personal?userId={user_id}'
                reminder = message['3']['redReminder']
                
                if reminder == '等待买家付款':
                    logger.info(f'等待买家 {user_url} 付款')
                elif reminder == '交易关闭':
                    logger.info(f'买家 {user_url} 交易关闭')
                elif reminder == '等待卖家发货':
                    logger.info(f'交易成功 {user_url} 等待卖家发货')
                
                return True
        except Exception:
            pass
        return False
