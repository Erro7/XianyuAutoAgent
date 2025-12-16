# services/heartbeat_manager.py
import asyncio
import time
import json
from typing import Optional, Callable
from loguru import logger
from utils.xianyu_utils import generate_mid


class HeartbeatManager:
    """独立的心跳管理器"""
    
    def __init__(self, websocket, heartbeat_interval: int = 15, heartbeat_timeout: int = 5):
        self.websocket = websocket
        self.heartbeat_interval = heartbeat_interval  # 心跳间隔
        self.heartbeat_timeout = heartbeat_timeout    # 心跳超时
        
        # 心跳状态
        self.last_heartbeat_time = 0
        self.last_heartbeat_response = 0
        self.pending_heartbeats = {}  # 待响应的心跳包 {mid: timestamp}
        
        # 任务管理
        self.heartbeat_send_task = None
        self.heartbeat_check_task = None
        self.is_running = False
        
        # 回调函数
        self.on_connection_lost: Optional[Callable] = None
        
    async def start(self):
        """启动心跳管理器"""
        if self.is_running:
            logger.warning("心跳管理器已在运行中")
            return
            
        self.is_running = True
        self.last_heartbeat_time = time.time()
        self.last_heartbeat_response = time.time()
        
        # 启动心跳发送任务
        self.heartbeat_send_task = asyncio.create_task(self._heartbeat_send_loop())
        
        # 启动心跳检查任务
        self.heartbeat_check_task = asyncio.create_task(self._heartbeat_check_loop())
        
        logger.info(f"心跳管理器已启动 (间隔: {self.heartbeat_interval}s, 超时: {self.heartbeat_timeout}s)")
    
    async def stop(self):
        """停止心跳管理器"""
        if not self.is_running:
            return
            
        self.is_running = False
        
        # 取消任务
        if self.heartbeat_send_task:
            self.heartbeat_send_task.cancel()
            try:
                await self.heartbeat_send_task
            except asyncio.CancelledError:
                pass
                
        if self.heartbeat_check_task:
            self.heartbeat_check_task.cancel()
            try:
                await self.heartbeat_check_task
            except asyncio.CancelledError:
                pass
        
        logger.info("心跳管理器已停止")
    
    async def _heartbeat_send_loop(self):
        """心跳发送循环"""
        logger.info("心跳发送任务已启动")
        
        while self.is_running:
            try:
                current_time = time.time()
                
                # 检查是否需要发送心跳
                if current_time - self.last_heartbeat_time >= self.heartbeat_interval:
                    await self._send_heartbeat()
                
                # 每秒检查一次
                await asyncio.sleep(1)
                
            except asyncio.CancelledError:
                logger.info("心跳发送任务被取消")
                break
            except Exception as e:
                logger.error(f"心跳发送循环出错: {e}")
                await asyncio.sleep(1)
    
    async def _heartbeat_check_loop(self):
        """心跳检查循环 - 独立检查连接状态"""
        logger.info("心跳检查任务已启动")
        
        while self.is_running:
            try:
                current_time = time.time()
                
                # 检查待响应的心跳包是否超时
                expired_heartbeats = []
                for mid, send_time in self.pending_heartbeats.items():
                    if current_time - send_time > self.heartbeat_timeout:
                        expired_heartbeats.append(mid)
                
                # 清理超时的心跳包
                for mid in expired_heartbeats:
                    del self.pending_heartbeats[mid]
                    logger.warning(f"心跳包超时: {mid}")
                
                # 检查整体心跳响应超时
                if (current_time - self.last_heartbeat_response) > (self.heartbeat_interval + self.heartbeat_timeout):
                    logger.error("心跳响应超时，连接可能已断开")
                    await self._handle_connection_lost()
                    break
                
                # 每秒检查一次
                await asyncio.sleep(1)
                
            except asyncio.CancelledError:
                logger.info("心跳检查任务被取消")
                break
            except Exception as e:
                logger.error(f"心跳检查循环出错: {e}")
                await asyncio.sleep(1)
    
    async def _send_heartbeat(self):
        """发送心跳包"""
        try:
            heartbeat_mid = generate_mid()
            heartbeat_msg = {
                "lwp": "/!",
                "headers": {
                    "mid": heartbeat_mid
                }
            }
            
            # 记录发送时间
            current_time = time.time()
            self.pending_heartbeats[heartbeat_mid] = current_time
            self.last_heartbeat_time = current_time
            
            # 发送心跳包
            await self.websocket.send(json.dumps(heartbeat_msg))
            logger.debug(f"心跳包已发送: {heartbeat_mid}")
            
            # 清理过多的待响应心跳包（保留最近10个）
            if len(self.pending_heartbeats) > 10:
                oldest_mids = sorted(self.pending_heartbeats.keys())[:len(self.pending_heartbeats) - 10]
                for mid in oldest_mids:
                    del self.pending_heartbeats[mid]
            
        except Exception as e:
            logger.error(f"发送心跳包失败: {e}")
            await self._handle_connection_lost()
    
    def handle_heartbeat_response(self, message_data) -> bool:
        """处理心跳响应 - 由主循环调用"""
        try:
            if not isinstance(message_data, dict):
                return False
                
            headers = message_data.get("headers", {})
            mid = headers.get("mid")
            code = message_data.get("code")
            
            # 检查是否为心跳响应
            if code == 200 and mid and mid in self.pending_heartbeats:
                # 移除已响应的心跳包
                del self.pending_heartbeats[mid]
                self.last_heartbeat_response = time.time()
                logger.debug(f"收到心跳响应: {mid}")
                return True
                
        except Exception as e:
            logger.error(f"处理心跳响应出错: {e}")
        
        return False
    
    async def _handle_connection_lost(self):
        """处理连接丢失"""
        logger.error("检测到连接丢失")
        self.is_running = False
        
        # 调用回调函数
        if self.on_connection_lost:
            try:
                await self.on_connection_lost()
            except Exception as e:
                logger.error(f"连接丢失回调执行失败: {e}")
    
    def set_connection_lost_callback(self, callback: Callable):
        """设置连接丢失回调函数"""
        self.on_connection_lost = callback
    
    def get_stats(self) -> dict:
        """获取心跳统计信息"""
        current_time = time.time()
        return {
            'is_running': self.is_running,
            'last_heartbeat_time': self.last_heartbeat_time,
            'last_heartbeat_response': self.last_heartbeat_response,
            'pending_heartbeats_count': len(self.pending_heartbeats),
            'time_since_last_response': current_time - self.last_heartbeat_response,
            'heartbeat_interval': self.heartbeat_interval,
            'heartbeat_timeout': self.heartbeat_timeout
        }