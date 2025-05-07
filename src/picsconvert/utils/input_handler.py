import os
from typing import List, Set, Dict, Optional, Tuple
import pyperclip
from pathlib import Path
from typing import Tuple, List, Optional, Dict, Any
from loguru import logger
# 全局变量定义
SUPPORTED_ARCHIVE_FORMATS = ['.zip', '.rar', '.7z', '.cbz', '.cbr']

# config = {
#     'script_name': 'file_ops.archive_handler',
#     'console_enabled': False
# }
# logger, config_info = setup_logger(config)
class InputHandler:
    """通用输入处理类"""
    
    @staticmethod
    def get_clipboard_content() -> str:
        """
        获取剪贴板内容
        
        Returns:
            str: 剪贴板内容
        """
        try:
            return pyperclip.paste()
        except Exception as e:
            logger.error(f"[#file_ops]从剪贴板读取失败: {e}")
            return ""
            
    @staticmethod
    def get_manual_input(prompt: str = "请输入内容（输入空行结束）：") -> List[str]:
        """
        获取用户手动输入的多行内容
        
        Args:
            prompt: 提示信息
            
        Returns:
            List[str]: 输入的内容列表
        """
        print(prompt)
        lines = []
        while True:
            line = input().strip()
            if not line:
                break
            lines.append(line)
        return lines
    @staticmethod
    def path_normalizer(path: str) -> str:
        """
        规范化路径，处理引号和转义字符
        
        Args:
            path: 原始路径
            
        Returns:
            str: 规范化后的路径
        """
        # 移除首尾的引号
        path = path.strip('"\'')
        # 处理转义字符
        path = path.replace('\\\\', '\\')
        # 转换为绝对路径
        return os.path.abspath(path)
    
    @staticmethod
    def get_input_paths(
        cli_paths: Optional[List[str]] = None,
        use_clipboard: bool = True,
        allow_manual: bool = True,
        path_validator: Optional[callable] = os.path.exists,
    ) -> List[str]:
        """
        获取输入路径，支持多种输入方式
        
        Args:
            cli_paths: 命令行参数中的路径列表
            use_clipboard: 是否使用剪贴板内容
            allow_manual: 是否允许手动输入
            path_validator: 路径验证函数
            path_normalizer: 路径规范化函数
            
        Returns:
            List[str]: 有效的路径列表
        """
        paths = []
        
        # 处理命令行参数
        if cli_paths:
            paths.extend(cli_paths)
            
        # 处理剪贴板内容
        if use_clipboard and (not paths or use_clipboard):
            clipboard_content = InputHandler.get_clipboard_content()
            if clipboard_content:
                clipboard_paths = [
                    line.strip()
                    for line in clipboard_content.splitlines()
                    if line.strip()
                ]
                paths.extend(clipboard_paths)
                logger.info(f"从剪贴板读取了 {len(clipboard_paths)} 个路径")
                
        # 手动输入
        if allow_manual and not paths:
            manual_paths = InputHandler.get_manual_input("请输入路径（每行一个，输入空行结束）：")
            paths.extend(manual_paths)
            
        # 规范化路径
        if InputHandler.path_normalizer:
            paths = [InputHandler.path_normalizer(p) for p in paths]
            
        # 验证路径
        if path_validator:
            valid_paths = []
            for p in paths:
                if path_validator(p):
                    valid_paths.append(p)
                else:
                    logger.warning(f"[#file_ops]路径无效: {p}")
            return valid_paths
            
        return paths

    @staticmethod
    def get_all_file_paths(paths: Set[str], file_types: Optional[Set[str]] = None) -> List[str]:
        """将包含文件夹和文件路径的集合转换为完整的文件路径列表
        
        Args:
            paths: 包含文件夹和文件路径的集合
            file_types: 要筛选的文件类型集合，如果为None则返回所有文件
            
        Returns:
            List[str]: 完整的文件路径列表
        """
        all_files = []
        
        try:
            for path in paths:
                if not os.path.exists(path):
                    logger.warning(f"[#file_ops]路径不存在: {path}")
                    continue
                    
                if os.path.isfile(path):
                    if file_types is None or any(path.lower().endswith(ext) for ext in file_types):
                        all_files.append(path)
                elif os.path.isdir(path):
                    for root, _, files in os.walk(path):
                        for file in files:
                            file_path = os.path.join(root, file)
                            if file_types is None or any(file_path.lower().endswith(ext) for ext in file_types):
                                all_files.append(file_path)
                                
        except Exception as e:
            logger.error(f"[#file_ops]获取文件路径时出错: {e}")
            
        return all_files
    @staticmethod
    def group_input_paths(paths: List[str]) -> List[Set[str]]:
        """将输入路径分组
        
        规则:
        1. 每个目录下的压缩包作为一组(按路径排序)
        2. 连续的压缩包文件会被分到同一组
        3. 不连续的压缩包（中间有非压缩包或目录）会被分成不同组
        
        Args:
            paths: 输入路径列表
            
        Returns:
            List[Set[str]]: 分组后的路径集合列表
        """
        groups = []
        sorted_paths = sorted(paths)
        
        # 处理目录
        for path in [p for p in sorted_paths if os.path.isdir(p)]:
            archives = []
            for root, _, files in os.walk(Path(path)):
                archives.extend([os.path.join(root, f) for f in files 
                            if Path(f).suffix.lower() in SUPPORTED_ARCHIVE_FORMATS])
            if archives:
                groups.append(set(sorted(archives)))
        
        # 处理文件
        file_paths = [p for p in sorted_paths if not os.path.isdir(p)]
        current = []
        is_prev_archive = False
        
        for path in file_paths:
            is_archive = Path(path).suffix.lower() in SUPPORTED_ARCHIVE_FORMATS
            
            # 当前是压缩包但上一个不是，开始新序列
            if is_archive and not is_prev_archive:
                if current:
                    groups.append(set(current))
                    current = []
                current.append(path)
            # 当前是压缩包且上一个也是，继续序列
            elif is_archive and is_prev_archive:
                current.append(path)
                
            is_prev_archive = is_archive
        
        # 添加最后一个序列
        if current:
            groups.append(set(current))
            
        return groups