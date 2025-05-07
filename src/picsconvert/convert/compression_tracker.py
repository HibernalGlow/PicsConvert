"""
压缩率跟踪器模块 - 用于在多线程环境下共享压缩状态信息
"""
import threading
import time
from collections import deque
import logging
from typing import Dict, List, Tuple, Optional
import json # 新增导入
from pathlib import Path # 新增导入

# 获取logger实例
logger = logging.getLogger(__name__)

# 定义黑名单文件路径 (可以根据需要调整)
# 定义黑名单文件路径 (在脚本所在目录下)
BLACKLIST_FILE_PATH = Path(__file__).resolve().parent / 'compression_blacklist.json'
# 确保目录存在 (虽然脚本运行时目录应已存在，但这行无害)
BLACKLIST_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)


class CompressionStateManager:
    """压缩状态管理器，用于在多线程间共享状态"""
    
    _instance = None
    _lock = threading.Lock()
    _file_lock = threading.Lock() # 新增文件锁，用于读写黑名单文件
    
    @classmethod
    def get_instance(cls):
        """获取单例实例"""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance
    
    def __init__(self):
        """初始化状态管理器"""
        self._lock = threading.Lock()
        self._batch_data: Dict[str, Dict] = {}  # 批次数据
        self._current_batch_id: Optional[str] = None
        
    def start_batch(self, archive_path: str) -> str: # 新增 archive_path 参数
        """开始一个新批次，返回批次ID"""
        with self._lock:
            batch_id = f"batch_{int(time.time())}_{id(threading.current_thread())}"
            self._current_batch_id = batch_id
            self._batch_data[batch_id] = {
                'archive_path': archive_path, # 存储压缩包路径
                'compression_stats': deque(maxlen=50),  # 保存最近50个压缩结果
                'negative_count': 0,
                'total_count': 0,
                'should_stop': False,
                'start_time': time.time(),
                'consecutive_negative': 0  # 连续负压缩次数
            }
            logger.info(f"[#tracker]创建新批次: {batch_id} (文件: {archive_path})")
            return batch_id
            
    def record_compression(self, batch_id: str, filename: str, 
                           original_size: int, new_size: int, 
                           negative_threshold: int = 3, # 连续次数阈值
                           ratio_threshold: float = 0.0) -> Tuple[bool, float]: # 新增：压缩率判断阈值
        """记录一次压缩结果，返回是否应该继续和压缩率
        
        Args:
            batch_id: 批次ID
            filename: 文件名
            original_size: 原文件大小
            new_size: 新文件大小
            negative_threshold: 连续低于 ratio_threshold 的次数阈值，达到则停止，默认3次
            ratio_threshold: 压缩率判断阈值，低于此值视为效果不佳，默认为 0.0 (%)
            
        Returns:
            Tuple[bool, float]: (是否继续处理, 压缩率)
        """
        if not batch_id or batch_id not in self._batch_data:
            logger.warning(f"[#tracker]尝试记录未知的批次ID: {batch_id}")
            return True, 0  # 批次不存在，默认继续
            
        ratio = ((original_size - new_size) / original_size * 100) if original_size > 0 else 0
        
        with self._lock:
            # 再次检查批次是否存在，因为在等待锁期间可能已被清理
            if batch_id not in self._batch_data:
                 logger.warning(f"[#tracker]尝试记录已被清理的批次ID: {batch_id}")
                 return True, 0
                 
            batch = self._batch_data[batch_id]
            batch['total_count'] += 1
            batch['compression_stats'].append((filename, ratio, time.time()))
            
            # 使用新的 ratio_threshold 进行判断
            if ratio < ratio_threshold:
                batch['consecutive_negative'] += 1
                
                # 检查是否达到连续次数阈值 (negative_threshold)
                if batch['consecutive_negative'] >= negative_threshold:
                    if not batch['should_stop']: # 确保只执行一次停止逻辑
                        batch['should_stop'] = True
                        # 获取最近 negative_threshold 次的记录
                        recent_files = [f"{s[0]}({s[1]:.1f}%)" for s in list(batch['compression_stats'])[-negative_threshold:]]
                        logger.warning(f"[#tracker]检测到连续{negative_threshold}次压缩率低于 {ratio_threshold:.1f}%，停止批次 {batch_id}。最近文件: {', '.join(recent_files)}")
                        # 将关联的压缩包路径添加到黑名单
                        archive_path_to_blacklist = batch.get('archive_path')
                        if archive_path_to_blacklist:
                            self._add_to_blacklist(archive_path_to_blacklist)
                        else:
                            logger.warning(f"[#tracker]批次 {batch_id} 缺少 archive_path，无法添加到黑名单。")
                    return False, ratio # 返回停止信号
            else:
                # 压缩率达到或超过 ratio_threshold，重置连续计数
                batch['consecutive_negative'] = 0
                
            return not batch['should_stop'], ratio
    
    def should_stop_batch(self, batch_id: str) -> bool:
        """检查是否应该停止批处理"""
        if not batch_id or batch_id not in self._batch_data:
            return False
            
        with self._lock:
            return self._batch_data[batch_id]['should_stop']
    
    def get_current_batch_id(self) -> Optional[str]:
        """获取当前批次ID"""
        with self._lock:
            return self._current_batch_id
            
    def get_batch_stats(self, batch_id: str) -> Dict:
        """获取批次统计信息"""
        if not batch_id or batch_id not in self._batch_data:
            return {}
            
        with self._lock:
            batch = self._batch_data[batch_id]
            return {
                'total_count': batch['total_count'],
                'consecutive_negative': batch['consecutive_negative'],
                'recent_stats': list(batch['compression_stats']),
                'should_stop': batch['should_stop'],
                'duration': time.time() - batch['start_time']
            }
            
    def cleanup_batch(self, batch_id: str) -> None:
        """清理批次数据"""
        with self._lock:
            if batch_id in self._batch_data:
                batch_info = self._batch_data[batch_id]
                logger.info(f"[#tracker]清理批次: {batch_id}, 文件: {batch_info.get('archive_path', 'N/A')}, 总处理: {batch_info['total_count']}个文件")
                del self._batch_data[batch_id]
                if self._current_batch_id == batch_id:
                    self._current_batch_id = None # 如果清理的是当前批次，重置当前批次ID
                
    def get_all_batch_ids(self) -> List[str]:
        """获取所有批次ID"""
        with self._lock:
            return list(self._batch_data.keys())

    def _add_to_blacklist(self, archive_path: str) -> None:
        """将指定的压缩包路径添加到黑名单JSON文件中（线程安全）"""
        with self._file_lock: # 使用文件锁确保文件访问的原子性
            try:
                blacklist_data = set()
                if BLACKLIST_FILE_PATH.exists() and BLACKLIST_FILE_PATH.stat().st_size > 0:
                    try:
                        with open(BLACKLIST_FILE_PATH, 'r', encoding='utf-8') as f:
                            # 读取为列表，然后转换为集合以去重
                            blacklist_data = set(json.load(f))
                    except json.JSONDecodeError:
                        logger.error(f"[#tracker]黑名单文件 {BLACKLIST_FILE_PATH} 格式错误，将重新创建。")
                    except Exception as e:
                         logger.error(f"[#tracker]读取黑名单文件 {BLACKLIST_FILE_PATH} 时出错: {e}")
                         # 如果读取失败，可以选择不添加或覆盖，这里选择继续尝试写入

                if archive_path not in blacklist_data:
                    blacklist_data.add(archive_path)
                    try:
                        with open(BLACKLIST_FILE_PATH, 'w', encoding='utf-8') as f:
                            # 将集合转换回列表进行JSON序列化
                            json.dump(list(blacklist_data), f, ensure_ascii=False, indent=4)
                        logger.info(f"[#tracker]已将压缩包添加到黑名单: {archive_path}")
                    except Exception as e:
                        logger.error(f"[#tracker]写入黑名单文件 {BLACKLIST_FILE_PATH} 时出错: {e}")
                else:
                    logger.info(f"[#tracker]压缩包已存在于黑名单中: {archive_path}")

            except Exception as e:
                logger.error(f"[#tracker]处理黑名单文件时发生意外错误: {e}")


# 创建全局单例实例
compression_tracker = CompressionStateManager.get_instance()