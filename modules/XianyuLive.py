import base64
import json
import asyncio
import time
import os
import websockets
from loguru import logger
from modules.XianyuApis import XianyuApis
from modules.XianyuAgent import XianyuReplyBot


from utils.xianyu_utils import generate_mid, trans_cookies, generate_device_id, decrypt
from services.context_manager import ChatContextManager
from services.heartbeat_manager import HeartbeatManager
from services.message_manager import MessageManager, BaseMessageHandler
from middleware.message_middleware import MessageType, BaseMiddleware


class XianyuLive:
    def __init__(self, cookies_str):
        self.xianyu = XianyuApis()
        self.bot = XianyuReplyBot()
        self.base_url = 'wss://wss-goofish.dingtalk.com/'
        self.cookies_str = cookies_str
        self.cookies = trans_cookies(cookies_str)
        self.xianyu.session.cookies.update(self.cookies)  # 直接使用 session.cookies.update
        self.myid = self.cookies['unb']
        self.device_id = generate_device_id(self.myid)
        self.context_manager = ChatContextManager()
        self.message_manager = MessageManager(
            max_workers=3,
            queue_max_size=100
        )
        
        # 心跳相关配置
        self.heartbeat_interval = int(os.getenv("HEARTBEAT_INTERVAL", "15"))  # 心跳间隔，默认15秒
        self.heartbeat_timeout = int(os.getenv("HEARTBEAT_TIMEOUT", "5"))     # 心跳超时，默认5秒
        self.heartbeat_task = None
        self.ws = None
        
        self.heartbeat_manager = None
        self.ws = None
        
        # Token刷新相关配置
        self.token_refresh_interval = int(os.getenv("TOKEN_REFRESH_INTERVAL", "3600"))  # Token刷新间隔，默认1小时
        self.token_retry_interval = int(os.getenv("TOKEN_RETRY_INTERVAL", "300"))       # Token重试间隔，默认5分钟
        self.last_token_refresh_time = 0
        self.current_token = None
        self.token_refresh_task = None
        self.connection_restart_flag = False  # 连接重启标志
        
        # 人工接管相关配置
        self.manual_mode_conversations = set()  # 存储处于人工接管模式的会话ID
        self.manual_mode_timeout = int(os.getenv("MANUAL_MODE_TIMEOUT", "3600"))  # 人工接管超时时间，默认1小时
        self.manual_mode_timestamps = {}  # 记录进入人工模式的时间
        
        # 消息过期时间配置
        self.message_expire_time = int(os.getenv("MESSAGE_EXPIRE_TIME", "300000"))  # 消息过期时间，默认5分钟
        
        # 人工接管关键词，从环境变量读取
        self.toggle_keywords = os.getenv("TOGGLE_KEYWORDS", "。")
        
    async def send_msg(self, ws, cid, toid, text):
        """发送消息"""
        await self.xianyu.send_msg(ws, cid, toid, self.myid, text)

    async def refresh_token(self):
        """刷新token"""
        try:
            logger.info("开始刷新token...")
            
            # 获取新token（如果Cookie失效，get_token会直接退出程序）
            token_result = self.xianyu.get_token(self.device_id)
            if 'data' in token_result and 'accessToken' in token_result['data']:
                new_token = token_result['data']['accessToken']
                self.current_token = new_token
                self.last_token_refresh_time = time.time()
                logger.info("Token刷新成功")
                return new_token
            else:
                logger.error(f"Token刷新失败: {token_result}")
                return None
                
        except Exception as e:
            logger.error(f"Token刷新异常: {str(e)}")
            return None

    async def token_refresh_loop(self):
        """Token刷新循环"""
        while True:
            try:
                current_time = time.time()
                
                # 检查是否需要刷新token
                if current_time - self.last_token_refresh_time >= self.token_refresh_interval:
                    logger.info("Token即将过期，准备刷新...")
                    
                    new_token = await self.refresh_token()
                    if new_token:
                        logger.info("Token刷新成功，准备重新建立连接...")
                        # 设置连接重启标志
                        self.connection_restart_flag = True
                        # 关闭当前WebSocket连接，触发重连
                        if self.ws:
                            await self.ws.close()
                        break
                    else:
                        logger.error("Token刷新失败，将在{}分钟后重试".format(self.token_retry_interval // 60))
                        await asyncio.sleep(self.token_retry_interval)  # 使用配置的重试间隔
                        continue
                
                # 每分钟检查一次
                await asyncio.sleep(60)
                
            except Exception as e:
                logger.error(f"Token刷新循环出错: {e}")
                await asyncio.sleep(60)

    async def init(self, ws):
        # 如果没有token或者token过期，获取新token
        if not self.current_token or (time.time() - self.last_token_refresh_time) >= self.token_refresh_interval:
            logger.info("获取初始token...")
            await self.refresh_token()
        
        if not self.current_token:
            logger.error("无法获取有效token，初始化失败")
            raise Exception("Token获取失败")
            
        msg = {
            "lwp": "/reg",
            "headers": {
                "cache-header": "app-key token ua wv",
                "app-key": "444e9908a51d1cb236a27862abc769c9",
                "token": self.current_token,
                "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36 DingTalk(2.1.5) OS(Windows/10) Browser(Chrome/133.0.0.0) DingWeb/2.1.5 IMPaaS DingWeb/2.1.5",
                "dt": "j",
                "wv": "im:3,au:3,sy:6",
                "sync": "0,0;0;0;",
                "did": self.device_id,
                "mid": generate_mid()
            }
        }
        await ws.send(json.dumps(msg))
        # 等待一段时间，确保连接注册完成
        await asyncio.sleep(1)
        msg = {"lwp": "/r/SyncStatus/ackDiff", "headers": {"mid": "5701741704675979 0"}, "body": [
            {"pipeline": "sync", "tooLong2Tag": "PNM,1", "channel": "sync", "topic": "sync", "highPts": 0,
             "pts": int(time.time() * 1000) * 1000, "seq": 0, "timestamp": int(time.time() * 1000)}]}
        await ws.send(json.dumps(msg))
        logger.info('连接注册完成')

    async def init_heartbeat_manager(self, websocket):
        """初始化心跳管理器"""
        
        self.heartbeat_manager = HeartbeatManager(
            websocket=websocket,
            heartbeat_interval=self.heartbeat_interval,
            heartbeat_timeout=self.heartbeat_timeout
        )
        
        # 设置连接丢失回调
        self.heartbeat_manager.set_connection_lost_callback(self._on_connection_lost)
        
        await self.heartbeat_manager.start()
        logger.info("心跳管理器初始化完成")

    def _setup_message_handlers(self):
        """设置消息处理器"""
        # 注册聊天消息处理器
        self.message_manager.register_handler(MessageType.QUERY, XianyuChatHandler(self))
        
        # 注册命令处理器（如人工接管切换）
        self.message_manager.register_handler(MessageType.COMMAND, XianyuCommandHandler(self))
        
        # 注册事件处理器（如订单状态变更）
        self.message_manager.register_handler(MessageType.EVENT, XianyuEventHandler(self))

    def _setup_custom_middlewares(self):
        """设置自定义中间件"""
        # 添加消息过期检查中间件
        self.message_manager.use_middleware(MessageExpiryMiddleware(self.message_expire_time))
        
        # 添加人工接管检查中间件
        self.message_manager.use_middleware(ManualModeMiddleware(self))
        
        # 添加消息去重中间件
        self.message_manager.use_middleware(DeduplicationMiddleware())

    async def _handle_order_message(self, message):
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
    
    def _determine_message_type(self, message_info):
        """确定消息类型"""
        # 检查是否为卖家控制命令
        if message_info["send_user_id"] == self.myid:
            if self.check_toggle_keywords(message_info["send_message"]):
                return "command"
            return "event"  # 卖家的其他消息作为事件处理
        
        # 用户消息默认为查询
        return "query"

    def _extract_message_info(self, message):
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
    
    async def _on_connection_lost(self):
        """连接丢失回调"""
        logger.warning("心跳管理器检测到连接丢失，准备重连...")
        self.connection_restart_flag = True
        
        # 关闭当前WebSocket连接，触发重连
        if self.ws:
            try:
                await self.ws.close()
            except Exception as e:
                logger.error(f"关闭WebSocket连接失败: {e}")

    def is_chat_message(self, message):
        """判断是否为用户聊天消息"""
        try:
            return (
                isinstance(message, dict) 
                and "1" in message 
                and isinstance(message["1"], dict)  # 确保是字典类型
                and "10" in message["1"]
                and isinstance(message["1"]["10"], dict)  # 确保是字典类型
                and "reminderContent" in message["1"]["10"]
            )
        except Exception:
            return False

    def is_sync_package(self, message_data):
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

    def is_typing_status(self, message):
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

    def is_system_message(self, message):
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

    def check_toggle_keywords(self, message):
        """检查消息是否包含切换关键词"""
        message_stripped = message.strip()
        return message_stripped in self.toggle_keywords

    def is_manual_mode(self, chat_id):
        """检查特定会话是否处于人工接管模式"""
        if chat_id not in self.manual_mode_conversations:
            return False
        
        # 检查是否超时
        current_time = time.time()
        if chat_id in self.manual_mode_timestamps:
            if current_time - self.manual_mode_timestamps[chat_id] > self.manual_mode_timeout:
                # 超时，自动退出人工模式
                self.exit_manual_mode(chat_id)
                return False
        
        return True

    def enter_manual_mode(self, chat_id):
        """进入人工接管模式"""
        self.manual_mode_conversations.add(chat_id)
        self.manual_mode_timestamps[chat_id] = time.time()

    def exit_manual_mode(self, chat_id):
        """退出人工接管模式"""
        self.manual_mode_conversations.discard(chat_id)
        if chat_id in self.manual_mode_timestamps:
            del self.manual_mode_timestamps[chat_id]

    def toggle_manual_mode(self, chat_id):
        """切换人工接管模式"""
        if self.is_manual_mode(chat_id):
            self.exit_manual_mode(chat_id)
            return "auto"
        else:
            self.enter_manual_mode(chat_id)
            return "manual"

    async def handle_message(self, message_data, websocket):
        """处理所有类型的消息 - 使用消息中间件"""
        try:
            # 发送ACK响应
            await self._send_ack(message_data, websocket)

            # 如果不是同步包消息，直接返回
            if not self.is_sync_package(message_data):
                return

            # 获取并解密数据
            message = await self._decrypt_sync_data(message_data)
            if not message:
                return
            
            # 检查订单消息
            if await self._handle_order_message(message):
                return

            # 检查输入状态消息
            if self.is_typing_status(message):
                logger.debug("用户正在输入")
                return

            # 检查是否为聊天消息
            if not self.is_chat_message(message):
                logger.debug(f"其他非聊天消息")
                return

            # 提取消息信息
            message_info = self._extract_message_info(message)
            if not message_info:
                return

            # 判断消息类型
            message_type = self._determine_message_type(message_info)
            
            # 构建消息载荷
            payload = {
                "original_message": message,
                "websocket": websocket,
                "message_info": message_info,
                "xianyu_live": self
            }

            # 发送到消息中间件
            await self.message_manager.send_message(
                chat_id=message_info["chat_id"],
                payload=payload,
                message_type=message_type,
                correlation_id=message_info.get("message_id")
            )
            
        except Exception as e:
            logger.error(f"处理消息时发生错误: {str(e)}")
            
    async def _send_ack(self, message_data, websocket):
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
        except Exception as e:
            logger.debug(f"发送ACK失败: {e}")

    async def _decrypt_sync_data(self, message_data):
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
    
    async def _messsage_proccess(self):   
        # 注册消息处理器
        self._setup_message_handlers()
        # 注册自定义中间件
        self._setup_custom_middlewares()
        # 启动消息管理器
        await self.message_manager.start()

    async def _live_proccess(self):
         while True:
            try:
                # 重置连接重启标志
                self.connection_restart_flag = False
                
                headers = {
                    "Cookie": self.cookies_str,
                    "Host": "wss-goofish.dingtalk.com",
                    "Connection": "Upgrade",
                    "Pragma": "no-cache",
                    "Cache-Control": "no-cache",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
                    "Origin": "https://www.goofish.com",
                    "Accept-Encoding": "gzip, deflate, br, zstd",
                    "Accept-Language": "zh-CN,zh;q=0.9",
                }

                async with websockets.connect(self.base_url, extra_headers=headers) as websocket:
                    self.ws = websocket
                    await self.init(websocket)
                    
                    # 初始化心跳
                    await self.init_heartbeat_manager(websocket)
                               
                    # 启动token刷新任务
                    self.token_refresh_task = asyncio.create_task(self.token_refresh_loop())
                    
                    async for message in websocket:
                        try:
                            # 检查是否需要重启连接
                            if self.connection_restart_flag:
                                logger.info("检测到连接重启标志，准备重新建立连接...")
                                break
                                
                            message_data = json.loads(message)
                            
                            # 优先处理心跳响应
                            if self.heartbeat_manager and self.heartbeat_manager.handle_heartbeat_response(message_data):
                                continue
                            
                            # 处理消息
                            await self.handle_message(message_data, websocket)
                                
                        except json.JSONDecodeError:
                            logger.error("消息解析失败")
                        except Exception as e:
                            logger.error(f"处理消息时发生错误: {str(e)}")
                            logger.debug(f"原始消息: {message}")

            except websockets.exceptions.ConnectionClosed:
                logger.warning("WebSocket连接已关闭")
                
            except Exception as e:
                logger.error(f"连接发生错误: {e}")
                
            finally:
                # 停止心跳管理器
                if self.heartbeat_manager:
                    await self.heartbeat_manager.stop()
                    self.heartbeat_manager = None
                        
                # 清理任务
                if self.token_refresh_task:
                    self.token_refresh_task.cancel()
                    try:
                        await self.token_refresh_task
                    except asyncio.CancelledError:
                        pass
                
                # 如果是主动重启，立即重连；否则等待5秒
                if self.connection_restart_flag:
                    logger.info("主动重启连接，立即重连...")
                else:
                    logger.info("等待5秒后重连...")
                    await asyncio.sleep(5)
                    
    async def main(self):
        tasks = [
            asyncio.create_task(self._live_proccess()),
            asyncio.create_task(self._messsage_proccess())
        ]
        await asyncio.gather(*tasks)
       
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
    
