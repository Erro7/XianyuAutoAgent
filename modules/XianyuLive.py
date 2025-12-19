import json
import asyncio
import time
import os
import websockets
from typing import Any
from loguru import logger
from base import BaseApplication, BaseLive, LiveEvent

from modules.XianyuApis import XianyuApis
from modules.XianyuAgent import XianyuReplyBot


from utils.xianyu_utils import generate_mid
from utils.message_utils import XianyuMessageUtils


class XianyuLive(BaseLive):
    def __init__(self, app: BaseApplication):
        super().__init__()
        
        self.xianyu: XianyuApis = app.api
        self.bot: XianyuReplyBot = app.bot
        self.base_url = app.base_url
        self.cookies_str = app.cookies_str
        self.myid = app.myid
        self.device_id = app.device_id
        
        # 心跳相关配置
        self.ws = None
        
        self.heartbeat_manager = None
        self.ws = None
        
        # Token刷新相关配置
        self.token_refresh_interval = app.token_refresh_interval
        self.token_retry_interval = app.token_retry_interval
        self.last_token_refresh_time = 0
        self.current_token = None
        self.token_refresh_task = None
        self.connection_restart_flag = False  # 连接重启标志
        
        # 人工接管关键词，从环境变量读取
        self.toggle_keywords = app.toggle_keywords
        
        # 消息工具类
        self.message_utils = XianyuMessageUtils()

    async def init(self, ws: websockets.WebSocketClientProtocol):
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

    async def connect(self) -> bool:
        """建立 WebSocket 连接"""
        try:
            # 如果没有token或者token过期，获取新token
            if not self.current_token or (time.time() - self.last_token_refresh_time) >= self.token_refresh_interval:
                logger.info("获取初始token...")
                await self.refresh_token()
            
            if not self.current_token:
                logger.error("无法获取有效token，连接失败")
                return False
            
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

            self.ws = await websockets.connect(self.base_url, extra_headers=headers)
            await self.init(self.ws)
            self.emit(LiveEvent.CONNECTED.value, self)
            
            logger.info("WebSocket连接建立成功")
            return True
            
        except Exception as e:
            self.emit(LiveEvent.CONNECTION_FAILED.value, self)
            self.logger.error(f"连接失败: {e}")
            return False
    
    async def disconnect(self) -> bool:
        """断开连接"""
        try:
            # 清理任务
            if self.token_refresh_task:
                self.token_refresh_task.cancel()
                try:
                    await self.token_refresh_task
                except asyncio.CancelledError:
                    pass
            
            # 关闭WebSocket连接
            if self.ws:
                await self.ws.close()
                self.emit(LiveEvent.DISCONNECTED, self)
                self.ws = None
            
            logger.info("WebSocket连接已断开")
            return True
            
        except Exception as e:
            self.logger.error(f"断开连接失败: {e}")
            self.emit(LiveEvent.DISCONNECTION_FAILED, self)
            return False
    
    async def on_receive(self, raw_message: Any) -> bool:
        try:
            # 解析 JSON
            if isinstance(raw_message, str):
                message_data = json.loads(raw_message)
            else:
                message_data = raw_message
            
            # 发送ACK响应
            await self.message_utils.send_ack(message_data, self.ws)

            self.emit(LiveEvent.RECEVICE.value, message_data)
            
        except Exception as e:
            self.logger.error(f"消息接收失败: {str(e)}")
            return False
    
    async def run_loop(self):
        """运行主循环 - 只负责接收消息"""
        while self.is_running:
            try:
                # 重置连接重启标志
                self.connection_restart_flag = False
                
                # 启动token刷新任务
                self.token_refresh_task = asyncio.create_task(self.token_refresh_loop())
                
                # 消息接收循环
                async for raw_message in self.ws:
                    if not self.is_running:
                        break
                    
                    # 检查是否需要重启连接
                    if self.connection_restart_flag:
                        logger.info("检测到连接重启标志，准备重新建立连接...")
                        break
          
                    # 将消息交给 on_receive 处理
                    await self.on_receive(raw_message)
                
                # 如果是主动重启，立即重连；否则等待5秒
                if self.connection_restart_flag:
                    logger.info("主动重启连接，立即重连...")
                    if await self.connect():
                        continue
                else:
                    logger.info("等待5秒后重连...")
                    await asyncio.sleep(5)
                    if await self.connect():
                        continue
                
                break  # 连接失败，退出循环
                
            except websockets.exceptions.ConnectionClosed:
                logger.warning("WebSocket连接已关闭")
                await asyncio.sleep(5)
                if not await self.connect():
                    break
                
            except Exception as e:
                logger.error(f"运行循环异常: {e}")
                await asyncio.sleep(5)
                if not await self.connect():
                    break

