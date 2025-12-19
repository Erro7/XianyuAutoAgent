import asyncio
from typing import Dict, List, Optional, TypeVar, Type, cast, Any
from loguru import logger
from base import BaseService, BaseServiceManager, BaseApplication


# 定义泛型类型变量
T = TypeVar('T', bound=BaseService)

class ServiceManager(BaseServiceManager):
    """服务管理器"""
    
    def __init__(self):
        super().__init__()
        self.service_types: Dict[str, Type] = {}  # 存储服务类型信息
        self.service_order: List[str] = []
        
    async def initialize(self, app: BaseApplication):
        """初始化所有服务"""
        self.app = app
        self.is_initialized = True
        
        for name in self.service_order:
            service = self.services[name]
            if isinstance(service, BaseService):
                await service.initialize()
                
        logger.info("服务管理器初始化完成")
        
    def register_service(self, name: str, service: T, service_type: Type[T]) -> None:
        """
        注册服务
        
        Args:
            name: 服务名称
            service: 服务实例
            service_type: 服务类型，用于泛型返回
        """
        self.services[name] = service
        self.service_types[name] = service_type
        if name not in self.service_order:
            self.service_order.append(name)
        if isinstance(service, BaseService):
            service._set_manager(self)
            
        logger.debug(f"服务已注册: {name} -> {service_type.__name__}")
        
    def get_service(self, name: str, service_type: Type[T]) -> Optional[T]:
        """
        获取服务实例 (泛型返回)
        
        Args:
            name: 服务名称
            service_type: 期望的服务类型
            
        Returns:
            服务实例，如果不存在或类型不匹配返回 None
            
        Example:
            # 获取上下文管理器
            context_manager = service_manager.get_service("context_manager", ChatContextManager)
            
            # 获取消息管理器
            message_manager = service_manager.get_service("message_manager", ThreadedMessageManager)
        """
        service = self.services.get(name)
        if service is None:
            logger.warning(f"服务不存在: {name}")
            return None
            
        # 类型检查
        expected_type = self.service_types.get(name)
        if expected_type and not issubclass(expected_type, service_type):
            logger.error(f"服务类型不匹配: {name}, 期望 {service_type.__name__}, 实际 {expected_type.__name__}")
            return None
            
        return cast(service_type, service) # type: ignore
    
    def get_service_unsafe(self, name: str) -> Any:
        """
        获取服务实例 (不安全版本，不进行类型检查)
        
        Args:
            name: 服务名称
            
        Returns:
            服务实例，如果不存在返回 None
        """
        return self.services.get(name)
    
    def has_service(self, name: str) -> bool:
        """检查服务是否存在"""
        return name in self.services
    
    def get_service_type(self, name: str) -> Optional[Type]:
        """获取服务的注册类型"""
        return self.service_types.get(name)
    
    def list_services(self) -> List[str]:
        """列出所有已注册的服务名称"""
        return list(self.services.keys())
        
    async def start_all(self):
        """启动所有服务"""
        logger.info("启动所有服务...")
        
        for service_name in self.service_order:
            service = self.services[service_name]
            
            # 检查服务是否有 start 方法
            if hasattr(service, 'start'):
                logger.info(f"启动服务: {service_name}")
                try:
                    if asyncio.iscoroutinefunction(service.start):
                        await service.start()
                    else:
                        service.start()
                    logger.info(f"✅ 服务 {service_name} 启动完成")
                except Exception as e:
                    logger.error(f"启动服务 {service_name} 失败: {e}")
                    raise
                
        logger.info("所有服务启动完成")
        
    async def stop_all(self):
        """停止所有服务"""
        logger.info("停止所有服务...")
        
        # 按相反顺序停止服务
        for service_name in reversed(self.service_order):
            service = self.services[service_name]
            
            # 检查服务是否有 stop 方法
            if hasattr(service, 'stop'):
                logger.info(f"停止服务: {service_name}")
                try:
                    if asyncio.iscoroutinefunction(service.stop):
                        await service.stop()
                    else:
                        service.stop()
                    logger.info(f"✅ 服务 {service_name} 停止完成")
                except Exception as e:
                    logger.error(f"停止服务 {service_name} 时出错: {e}")
                    
        logger.info("所有服务停止完成")
        
    def get_stats(self) -> Dict[str, Any]:
        """获取所有服务的统计信息"""
        stats = {
            "service_count": len(self.services),
            "service_list": list(self.services.keys()),
            "is_initialized": self.is_initialized,
            "services": {}
        }
        
        for name, service in self.services.items():
            service_stats = {"type": self.service_types.get(name, type(service)).__name__}
            
            if hasattr(service, 'get_stats'):
                try:
                    service_stats.update(service.get_stats())
                except Exception as e:
                    service_stats["stats_error"] = str(e)
            else:
                service_stats["status"] = "no_stats_available"
                
            stats["services"][name] = service_stats
            
        return stats

# 便利函数和装饰器
class ServiceDecorator(ServiceManager):
    """服务注册表"""
    
    def __init__(self):
        super().__init__()
        
    def register(self, name: str, service_type: Type[T]):
        """
        装饰器：自动注册服务
        
        Example:
            registry = ServiceRegistry(service_manager)
            
            @registry.register("my_service", MyServiceClass)
            def create_my_service():
                return MyServiceClass()
        """
        def decorator(factory_func):
            def wrapper(*args, **kwargs):
                service_instance = factory_func(*args, **kwargs)
                self.register_service(name, service_instance, service_type)
                return service_instance
            return wrapper
        return decorator


serviceManager = ServiceDecorator()