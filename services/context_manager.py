from base import BaseService

from modules.ChatContext import ChatContext

class ChatContextManager(BaseService, ChatContext):
    """聊天上下文管理器"""
    
    def __init__(self):
        BaseService.__init__(self, "context_manager")
        ChatContext.__init__(self)
       