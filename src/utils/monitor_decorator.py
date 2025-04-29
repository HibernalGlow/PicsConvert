from functools import wraps
import os
import time
import threading
import logging
import keyboard
from enum import Enum
from pathlib import Path
from typing import List, Set, Dict, Any, Callable

logger = logging.getLogger(__name__)

class ArchiveMonitor:
    """压缩包监控处理类"""
    def __init__(self, logger=None):
        self.running = False
        self.occupied_files: Set[str] = set()
        self.processed_files: Set[str] = set()
        self.monitor_thread = None
        self.logger = logger or logging.getLogger(__name__)

    def start_monitor(self, 
                     directories: List[str],
                     process_func: callable,
                     interval_minutes: int = 10,
                     filter_params: Dict[str, Any] = None):
        """启动监控
        
        Args:
            directories: 要监控的目录列表
            process_func: 处理单个文件的函数
            interval_minutes: 检查间隔(分钟)
            filter_params: 过滤参数
        """
        self.running = True
        self.monitor_thread = threading.Thread(
            target=self._monitor_loop,
            args=(directories, process_func, interval_minutes, filter_params)
        )
        self.monitor_thread.daemon = True
        self.monitor_thread.start()
        
    def stop_monitor(self):
        """停止监控"""
        self.running = False
        if self.monitor_thread:
            self.monitor_thread.join()
            
    def _monitor_loop(self, directories, process_func, interval_minutes, filter_params):
        """监控循环"""
        round_count = 0
        
        while self.running:
            try:
                round_count += 1
                logger.info(f"[#status]🔄 开始第 {round_count} 轮扫描...")
                
                # 获取需要处理的文件
                files = self._get_pending_files(directories)
                if not files:
                    logger.info("[#status]⏸️ 当前没有需要处理的文件，继续监控...")
                else:
                    # 处理文件
                    for file_path in files:
                        if not self.running:
                            break
                        try:
                            process_func(file_path, filter_params)
                            self.processed_files.add(file_path)
                        except Exception as e:
                            logger.info(f"[#status]❌ 处理失败: {file_path} - {str(e)}")
                
                # 等待下一轮
                wait_minutes = min(interval_minutes, round_count)
                logger.info(f"[#status]⏳ 等待 {wait_minutes} 分钟后开始下一轮...")
                
                for remaining in range(wait_minutes * 60, 0, -1):
                    if not self.running:
                        break
                    mins, secs = divmod(remaining, 60)
                    # 使用进度条面板显示倒计时
                    percentage = 100 - (remaining / (wait_minutes * 60) * 100)
                    logger.info(f"[@status]等待下一轮: {mins:02d}:{secs:02d} {percentage:.1f}%")
                    time.sleep(1)
                # print("\r" + " " * 30 + "\r", end='', flush=True)  # 清除倒计时行
                    
            except Exception as e:
                logger.info(f"❌ 监控出错: {str(e)}")
                # 出错后等待一段时间再继续
                time.sleep(interval_minutes * 60)
                
    def _get_pending_files(self, directories: List[str]) -> List[str]:
        """获取待处理的文件列表"""
        pending_files = []
        
        for directory in directories:
            path = Path(directory)
            if path.is_file():
                if self._should_process_file(str(path)):
                    pending_files.append(str(path))
            else:
                for file_path in path.rglob("*"):
                    if file_path.is_file() and self._should_process_file(str(file_path)):
                        pending_files.append(str(file_path))
                        
        return pending_files
        
    def _should_process_file(self, file_path: str) -> bool:
        """判断文件是否需要处理"""
        # 已处理的文件跳过
        if file_path in self.processed_files:
            return False
            
        # 检查文件是否被占用
        try:
            with open(file_path, "rb") as f:
                f.read(1)
            return True
        except (IOError, PermissionError):
            self.occupied_files.add(file_path)
            return False

def infinite_monitor(interval_minutes: int = -1):
    """无限监控模式的装饰器
    
    Args:
        interval_minutes: 检查间隔(分钟), -1表示不启用监控模式
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # 使用运行时传入的 interval_minutes 参数
            runtime_interval = kwargs.get('interval_minutes', interval_minutes)
            
            # 如果不是无限模式，直接执行一次后返回
            if runtime_interval <= 0:
                return func(*args, **kwargs)
            
            logger.info(f"[#status]🚀 启动无限循环模式，最大间隔 {runtime_interval} 分钟...")
            
            try:
                # 首次执行
                func(*args, **kwargs)
                
                # 进入循环模式
                round_count = 1
                while True:
                    # 使用渐进式等待时间，最大不超过设定的间隔
                    wait_minutes = min(runtime_interval, round_count)
                    logger.info(f"[#status]⏳ 等待 {wait_minutes} 分钟后开始第 {round_count + 1} 轮...")
                    
                    for remaining in range(wait_minutes * 60, 0, -1):
                        mins, secs = divmod(remaining, 60)
                        # 使用进度条面板显示倒计时
                        percentage = 100 - (remaining / (wait_minutes * 60) * 100)
                        logger.info(f"[@status]等待下一轮: {mins:02d}:{secs:02d} {percentage:.1f}%")
                        time.sleep(1)
                    
                    round_count += 1
                    logger.info(f"[#status]🔄 开始第 {round_count} 轮处理...")
                    # 再次执行函数
                    func(*args, **kwargs)
                    
            except KeyboardInterrupt:
                logger.info("[#status]👋 用户中断，程序退出...")
            except Exception as e:
                logger.info(f"[#status]❌ 监控模式出错: {str(e)}")
                
        return wrapper
    return decorator

# 添加无限模式枚举类
class InfiniteMode(Enum):
    NONE = "none"  # 不使用无限模式
    KEYBOARD = "keyboard"  # 键盘触发模式
    TIMER = "timer"  # 定时触发模式

def run_infinite_mode(function: Callable, args_dict=None, mode=InfiniteMode.NONE, interval=60, trigger_key='f2'):
    """通用无限运行模式
    
    Args:
        function: 要重复执行的函数
        args_dict: 函数参数字典
        mode: 无限模式类型(NONE, KEYBOARD, TIMER)
        interval: 定时器间隔(秒)
        trigger_key: 触发键(默认为F2)
    """
    logger.info("\n进入无限模式...")
    
    # 准备参数
    if args_dict is None:
        args_dict = {}
    
    # 先执行一次操作
    function(**args_dict)
    
    if mode == InfiniteMode.KEYBOARD:
        logger.info(f"按{trigger_key}键重新执行操作，按Ctrl+C退出")
        
        def on_key_pressed(e):
            if e.name == trigger_key:
                logger.info(f"\n\n检测到{trigger_key}按键，重新执行操作...")
                function(**args_dict)
        
        # 注册按键事件
        keyboard.on_press(on_key_pressed)
        
        # 保持程序运行
        try:
            while True:
                time.sleep(0.1)
        except KeyboardInterrupt:
            logger.info("\n检测到Ctrl+C，程序退出")
            keyboard.unhook_all()
            
    elif mode == InfiniteMode.TIMER:
        logger.info(f"每 {interval} 秒自动执行一次，按Ctrl+C退出")
        
        def timer_task():
            while True:
                time.sleep(interval)
                logger.info("\n\n定时触发，重新执行操作...")
                function(**args_dict)
        
        # 启动定时器线程
        timer_thread = threading.Thread(target=timer_task, daemon=True)
        timer_thread.start()
        
        # 保持主线程运行
        try:
            while True:
                time.sleep(0.1)
        except KeyboardInterrupt:
            logger.info("\n检测到Ctrl+C，程序退出")
