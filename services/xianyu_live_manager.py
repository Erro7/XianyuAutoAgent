
import asyncio

from base import BaseApplication, BaseService, LiveEvent

from modules.XianyuLive import XianyuLive
from modules.XianyuHeartbeat import XianyuHeartbeat

from services.message_manager import MessageManager

class XianyuLiveManager(BaseService):
    """主要服务"""
    def __init__(self, app: BaseApplication):
        super().__init__(XianyuLiveManager.__name__)
        self.app = app
        self.live = XianyuLive(app)
        self.heartbeat: XianyuHeartbeat = None
        self.message_service: MessageManager = app.service_manager.get_service('message_manager', MessageManager)
        
    async def initialize(self):
        self._register_core_handlers()
        
    def _register_core_handlers(self):
        """注册核心事件处理器"""
        
        @self.live.on(LiveEvent.RECEVICE.value)
        def on_receive(payload: dict):
            self.heartbeat.handle_heartbeat_response(payload)
            asyncio.create_task(self.message_service.handle_message(payload))
        
        @self.live.on(LiveEvent.CONNECTED.value)
        def on_connected(payload: XianyuLive):
            self.logger.info("连接成功")
            self.heartbeat = XianyuHeartbeat(websocket=payload.ws)
            self.heartbeat.set_connection_lost_callback(self.live.disconnect)
            asyncio.create_task(self.heartbeat.start())
            
        @self.live.on(LiveEvent.DISCONNECTED.value)
        def on_disconnected(payload: XianyuLive):
            asyncio.create_task(self.heartbeat.stop())
            self.heartbeat = None
            self.logger.info("连接断开")
        
    async def start(self):
       await self.live.start()
        
    async def stop(self):
       await self.live.stop()
       

        
