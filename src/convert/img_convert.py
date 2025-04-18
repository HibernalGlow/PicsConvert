import os
import json
import logging
import time
import subprocess
from pathlib import Path
from PIL import Image
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Tuple, List, Set, Union, Optional
import importlib.util
import sys
BASE_DIR = os.getenv('PROJECT_ROOT')
VIPSHOME_RELATIVE = os.getenv('VIPSHOME_PATH', 'src/packages/vips/bin')
vipshome = Path(os.path.join(BASE_DIR, VIPSHOME_RELATIVE))
if hasattr(os, 'add_dll_directory'):
    os.add_dll_directory(str(vipshome))
os.environ['PATH'] = str(vipshome) + ';' + os.environ['PATH']
# 导入pyvips
try:
    import pyvips
    VIPS_AVAILABLE = True
except ImportError:
    VIPS_AVAILABLE = False
    raise ImportError("PyVIPS库未找到，请确保已经安装: pip install pyvips")

from nodes.record.logger_config import setup_logger
# 导入压缩率跟踪器
from nodes.pics.convert.compression_tracker import compression_tracker

# 获取logger实例
# config = {
#     'script_name': 'pics.convert.img_convert',
#     "console_enabled": False,
# }
# logger, _ = setup_logger(config)
logger = logging.getLogger(__name__)

# 支持的格式常量
SUPPORTED_IMAGE_FORMATS = {'.jpg', '.jpeg', '.png', '.webp', '.bmp', '.avif', '.jxl'}
EXCLUDED_IMAGE_FORMATS = {'.gif', '.psd', '.ai', '.cdr', '.eps', '.svg', '.raw', '.cr2', '.nef', '.arw'}
# 默认转换配置
DEFAULT_CONVERSION_CONFIG = {
    'source_formats': {'.jpg', '.jpeg', '.png', '.webp', '.bmp', '.avif', '.jxl'},
    "thread_count": 1,
    'target_format': '.avif',
    'avif_config': {
        'quality': 90,
        'speed': 7,
        'lossless': False,
        'strip': True
    },
    'jxl_config': {
        'quality': 90,
        'effort': 7,
        'lossless': False,
        'strip': True
    },
    'webp_config': {
        'quality': 90,
        'reduction_effort': 4,  # vips中的method替代参数
        'lossless': False,
        'strip': True
    },
    'jpeg_config': {
        'quality': 90,
        'optimize_coding': True,
        'strip': True,
        'interlace': False
    },
    'png_config': {
        'compression': 6,
        'strip': True,
        'filter': 'none'
    }
}


class ImageConverter:
    """图片格式转换器"""
    
    def __init__(self, config: Union[Dict, str] = None):
        """初始化转换器
        
        Args:
            config: 图片转换配置，可以是字典或JSON字符串
        """
        self.config = DEFAULT_CONVERSION_CONFIG.copy()
        # 默认开启JXL无损回退
        self.enable_jxl_fallback = True
        if config:
            if isinstance(config, str):
                try:
                    config_dict = json.loads(config)
                    self._update_config(config_dict)
                except json.JSONDecodeError:
                    logger.error("JSON配置解析错误，将使用默认配置")
            elif isinstance(config, dict):
                self._update_config(config)
        
        self.thread_count = min(config.get('thread_count', 1), os.cpu_count() or 4)
        
        # 设置VIPS缓存以避免内存消耗过多
        try:
            pyvips.cache_set_max_mem(1024 * 1024 * 1024)  # 1GB缓存
            # py/vips.concurrency_set(2)  # 每个VIPS实例的线程数较少
        except Exception as e:
            logger.warning(f"设置PyVIPS缓存参数失败: {e}")
        
        # 初始化压缩率跟踪相关属性
        self._current_batch_id = None
        self._negative_threshold = 3  # 连续负压缩率阈值
        self._ratio_threshold = 4.2
        
        # 添加配置日志
        logger.info(f"[#image]图片转换配置: 目标格式={self.config['target_format']}, 线程数={self.thread_count}")
# 在ImageConverter类中添加新函数

    def _check_compression_ratio(self, original_size: int, new_size: int, result: Dict) -> bool:
        """检查压缩率，如果连续多次出现负压缩率则返回False
        
        Args:
            original_size: 原始文件大小
            new_size: 新文件大小
            result: 结果字典，用于记录错误信息
            
        Returns:
            bool: 压缩是否有效
        """
        # 计算压缩率
        compression_ratio = self._calculate_compression_ratio(original_size, new_size)
        
        # 记录压缩率
        result['compression_ratio'] = compression_ratio
        
        # 如果存在当前批次ID，使用全局跟踪器检查连续负压缩
        if self._current_batch_id:
            filename = os.path.basename(result.get('input_path', 'unknown'))
            continue_process, ratio = compression_tracker.record_compression(
                self._current_batch_id, 
                filename, 
                original_size, 
                new_size,
                self._negative_threshold,
                self._ratio_threshold
            )
            
            # 如果检测到连续负压缩率，记录错误并返回失败
            if not continue_process:
                # 获取批次统计信息用于日志
                stats = compression_tracker.get_batch_stats(self._current_batch_id)
                recent_files = []
                if stats and 'recent_stats' in stats:
                    # 获取最近3个文件的信息
                    for stat in list(stats['recent_stats'])[-3:]:
                        recent_files.append(f"{stat[0]}({stat[1]:.1f}%)")
                
                error_msg = f"压缩无效，连续{self._negative_threshold}次检测到文件变大"
                if recent_files:
                    error_msg += f"，最近文件: {', '.join(recent_files)}"
                
                result['error'] = error_msg
                return False
                
        # 如果没有使用全局跟踪器，则使用原有的单文件检测逻辑
        elif compression_ratio < 0:
            # 增加负压缩次数计数器（如果不存在则初始化为1）
            if 'negative_compression_count' not in result:
                result['negative_compression_count'] = 1
            else:
                result['negative_compression_count'] += 1
            
            # 如果连续3次负压缩，记录错误并返回失败
            if result.get('negative_compression_count', 0) >= self._negative_threshold:
                logger.warning(f"连续{self._negative_threshold}次检测到负压缩率，压缩率: {compression_ratio:.1f}")
                result['error'] = f"压缩无效，文件变大了 ({compression_ratio:.1f}%)"
                return False
        else:
            # 正常压缩，重置计数器
            if 'negative_compression_count' in result:
                del result['negative_compression_count']
        
        return True
    def _update_config(self, config_dict: Dict):
        """更新配置"""
        if 'target_format' in config_dict:
            format_str = config_dict['target_format']
            if not format_str.startswith('.'):
                format_str = '.' + format_str
            self.config['target_format'] = format_str.lower()
        
        # 更新JXL无损回退开关
        if 'enable_jxl_fallback' in config_dict:
            self.enable_jxl_fallback = bool(config_dict['enable_jxl_fallback'])
        
        # 更新各格式的配置
        for fmt in ['avif_config', 'jxl_config', 'webp_config', 'jpeg_config', 'png_config']:
            if fmt in config_dict:
                self.config[fmt].update(config_dict[fmt])
        
        # 更新其他参数
        if 'thread_count' in config_dict:
            self.thread_count = int(config_dict['thread_count'])
        
        if 'source_formats' in config_dict:
            source_formats = config_dict['source_formats']
            if isinstance(source_formats, list):
                self.config['source_formats'] = {f".{fmt.lstrip('.')}" for fmt in source_formats}
    
    def convert_image(self, input_path: str, output_path: Optional[str] = None, replace_original: bool = True) -> Dict:
        """转换单个图片文件
        
        Args:
            input_path: 输入图片路径
            output_path: 输出图片路径，如不指定则使用原路径替换扩展名
            replace_original: 是否替换原始文件
        
        Returns:
            Dict: 包含处理结果的字典
        """
        # 添加进度日志
        # logger.info(f"[#image]处理图片: {input_path}")
        
        result = {
            'input_path': input_path,
            'output_path': None,
            'success': False,
            'original_size': 0,
            'new_size': 0,
            'processing_time': 0,
            'format': self.config['target_format'].lstrip('.')
        }
        
        start_time = time.time()
        
        try:
            # 验证输入路径
            if not os.path.exists(input_path):
                logger.error(f"输入文件不存在: {input_path}")
                result['error'] = "输入文件不存在"
                return result
            
            # 获取原始文件大小
            original_size = os.path.getsize(input_path)
            result['original_size'] = original_size
            
            # 检查文件类型
            file_ext = os.path.splitext(input_path.lower())[1]
            if file_ext not in self.config['source_formats']:
                logger.error(f"不支持的文件格式: {file_ext}")
                result['error'] = f"不支持的文件格式: {file_ext}"
                return result
            
            target_ext = self.config['target_format']
            
            # 如果已经是目标格式，不需要转换
            # if file_ext == target_ext:
            #     logger.info(f"文件已经是目标格式: {input_path}")
            #     result['output_path'] = input_path
            #     result['new_size'] = original_size
            #     result['success'] = True
            #     result['processing_time'] = time.time() - start_time
            #     return result
            
            # 准备输出路径
            if not output_path:
                output_path = os.path.splitext(input_path)[0] + target_ext
            
            # 转换图片
            success = False
            if target_ext == '.jxl' and self.config['jxl_config'].get('lossless', False):
                # 直接使用无损JXL转换
                success = self._convert_to_jxl_lossless(input_path, output_path)
            else:
                # 使用VIPS转换
                success = self._convert_with_vips(input_path, output_path, target_ext)
                
                # JXL无损回退检查
                if success and self.enable_jxl_fallback and os.path.exists(output_path):
                    # 获取压缩的临时文件大小
                    temp_size = os.path.getsize(output_path)
                    compression_ratio = self._calculate_compression_ratio(original_size, temp_size)
                    
                    # 获取配置的回退压缩阈值，默认20%
                    fallback_threshold = self.config.get('jxl_fallback_threshold', 20)
                    
                    # 判断是否需要尝试JXL无损转换
                    should_try_jxl = (
                        compression_ratio < fallback_threshold and 
                        not (file_ext == '.jxl' and self._is_jxl_lossless(input_path)) and
                        not (target_ext == '.jxl' and self.config['jxl_config'].get('lossless', False))
                    )
                    
                    if should_try_jxl:
                        logger.info(f"压缩率{compression_ratio:.1f} 低于阈值{fallback_threshold}，尝试JXL无损转换: {input_path}")
                        jxl_output = os.path.splitext(input_path)[0] + '.jxl'
                        
                        # 尝试JXL无损转换
                        jxl_success = self._convert_to_jxl_lossless(input_path, jxl_output)
                        
                        if jxl_success and os.path.exists(jxl_output):
                            jxl_size = os.path.getsize(jxl_output)
                            
                            # 只有当JXL无损比VIPS转换结果更小时才替换
                            if jxl_size < temp_size:
                                # 删除VIPS生成的临时文件
                                try:
                                    os.remove(output_path)
                                except OSError:
                                    pass
                                    
                                # 使用JXL无损输出
                                output_path = jxl_output
                                logger.info(f"JXL无损转换效果更好，替换为JXL格式: {jxl_size/1024:.1f}KB vs {temp_size/1024:.1f}KB")
                            else:
                                # JXL无损效果不如VIPS，删除JXL临时文件
                                logger.info(f"JXL无损转换大小不理想，保持原格式: {jxl_size/1024:.1f}KB vs {temp_size/1024:.1f}KB")
                                try:
                                    os.remove(jxl_output)
                                except OSError:
                                    pass
                        elif os.path.exists(jxl_output):
                            # JXL无损转换失败但文件存在，删除
                            try:
                                os.remove(jxl_output)
                            except OSError:
                                pass
            
            if not success or not os.path.exists(output_path):
                logger.error(f"转换失败: {input_path}")
                result['error'] = "转换失败"
                return result
            
            # 获取新文件大小
            new_size = os.path.getsize(output_path)
            result['new_size'] = new_size
            
            # 检查压缩率，如果连续多次出现负压缩率，返回失败
            if not self._check_compression_ratio(original_size, new_size, result):
                # 失败时不替换原文件，返回错误结果
                logger.warning(f"压缩率检查失败，不替换原文件: {input_path}")
                
                # 如果输出路径不是输入路径，删除输出文件
                if input_path != output_path and os.path.exists(output_path):
                    try:
                        os.remove(output_path)
                        logger.info(f"已删除压缩失败的输出文件: {output_path}")
                    except Exception as e:
                        logger.warning(f"删除压缩失败的输出文件出错: {output_path}, 错误: {str(e)}")
                
                result['success'] = False
                return result
            
            # 替换原文件（如果需要）
            if replace_original and input_path != output_path:
                success = self._replace_original_file(input_path, output_path, new_size)
                if not success:
                    result['error'] = "替换原始文件失败"
                    return result
            
            # 更新结果
            result['output_path'] = output_path
            result['success'] = True
            
            # 记录压缩效果
            compression_ratio = self._calculate_compression_ratio(original_size, new_size)
            size_difference = original_size - new_size
            # 添加转换结果日志
            if result['success']:
                logger.info(f"[#image]转换成功: {result['output_path']},{original_size}-{new_size}={size_difference}, 压缩率: {compression_ratio:.1f}")
            
        except Exception as e:
            logger.exception(f"处理图片时出错: {input_path}")
            result['error'] = str(e)
        
        result['processing_time'] = time.time() - start_time
        
        # 强制进行垃圾回收
        import gc
        gc.collect()
        
        return result
    
    def _convert_with_vips(self, input_path: str, output_path: str, target_ext: str) -> bool:
        """使用VIPS转换图片"""
        try:
            # 检查输入和输出是否相同，如果相同，使用临时文件
            input_abs = os.path.abspath(input_path)
            output_abs = os.path.abspath(output_path)
            use_temp_file = (input_abs == output_abs)
            
            if use_temp_file:
                # 创建临时文件路径
                temp_dir = os.path.dirname(output_abs)
                temp_filename = f"temp_{int(time.time())}_{os.path.basename(output_abs)}"
                temp_output_path = os.path.join(temp_dir, temp_filename)
                logger.info(f"检测到输入和输出路径相同，使用临时文件: {temp_output_path}")
                actual_output_path = temp_output_path
            else:
                actual_output_path = output_path
            
            # 加载图片
            image = pyvips.Image.new_from_file(input_path, access="sequential")
            
            # 根据目标格式获取配置
            if target_ext == '.avif':
                # AVIF格式
                config = self.config.get('avif_config', {})
                quality = config.get('quality', 90)
                speed = config.get('speed', 7)
                lossless = config.get('lossless', False)
                
                image.write_to_file(actual_output_path, 
                                    Q=quality,
                                    speed=speed,
                                    lossless=lossless)
                
            elif target_ext == '.webp':
                # WebP格式
                config = self.config.get('webp_config', {})
                quality = config.get('quality', 90)
                effort = config.get('reduction_effort', 4)
                lossless = config.get('lossless', False)
                
                # WebP的vips参数
                params = {
                    'Q': quality,
                    'effort': effort,
                    'lossless': lossless
                }
                
                # 保存为WebP
                image.write_to_file(actual_output_path, **params)
                
            elif target_ext == '.jxl':
                # JXL格式（有损模式）
                config = self.config.get('jxl_config', {})
                quality = config.get('quality', 90)
                effort = config.get('effort', 7)
                
                # vips的jxl导出参数
                image.write_to_file(actual_output_path,
                                   Q=quality,
                                   effort=effort)
                
            elif target_ext == '.jpg' or target_ext == '.jpeg':
                # JPEG格式
                config = self.config.get('jpeg_config', {})
                quality = config.get('quality', 90)
                optimize = config.get('optimize_coding', True)
                interlace = config.get('interlace', False)
                
                # vips的jpeg导出参数
                image.write_to_file(actual_output_path,
                                   Q=quality,
                                   optimize_coding=optimize,
                                   interlace=interlace)
                
            elif target_ext == '.png':
                # PNG格式
                config = self.config.get('png_config', {})
                compression = config.get('compression', 6)
                
                # vips的png导出参数
                image.write_to_file(actual_output_path,
                                   compression=compression,
                                   filter=pyvips.enums.ForeignPngFilter.NONE)
                
            else:
                logger.error(f"不支持的VIPS目标格式: {target_ext}")
                return False
            
            # 如果使用了临时文件，将其移动到最终位置
            if use_temp_file and os.path.exists(actual_output_path):
                # 先删除原文件
                if os.path.exists(output_path):
                    try:
                        os.remove(output_path)
                    except Exception as e:
                        logger.warning(f"删除原始文件失败: {output_path}, 错误: {str(e)}")
                        return False
                
                # 移动临时文件到目标位置
                try:
                    import shutil
                    shutil.move(actual_output_path, output_path)
                except Exception as e:
                    logger.error(f"移动临时文件失败: {actual_output_path} -> {output_path}, 错误: {str(e)}")
                    return False
            
            # 强制释放VIPS图像内存
            image = None
            # 强制进行垃圾回收
            import gc
            gc.collect()
            
            return True
            
        except Exception as e:
            logger.exception(f"VIPS转换出错: {input_path} -> {output_path}, 错误: {str(e)}")
            return False
        finally:
            # 确保在任何情况下都释放内存
            try:
                image = None
                import gc
                gc.collect()
            except:
                pass
    
    def _convert_to_jxl_lossless(self, input_path: str, output_path: str) -> bool:
        """转换为JXL无损格式"""
        try:
            file_ext = os.path.splitext(input_path.lower())[1]
            unable2jxl = [".avif",".webp"]
            # 如果是AVIF格式，需要先转换为PNG作为中间格式
            if file_ext in unable2jxl:
                logger.warning(f"{file_ext}格式不支持直接转换为JXL")
                return False

            # 优先使用cjxl工具
            try:
                jxl_config = self.config.get('jxl_config', {})
                effort = jxl_config.get('effort', 7)
                
                # 使用无损模式
                cmd = ['cjxl', '-e', str(effort), '-d', '0', input_path, output_path]
                    
                result = subprocess.run(cmd, capture_output=True, text=True)
                if result.returncode == 0:
                    return True
                    
                logger.warning(f"cjxl转换失败: {input_path}")
                return False
                
            except (FileNotFoundError, subprocess.SubprocessError):
                logger.warning("未找到cjxl工具，JXL无损转换失败")
                return False
            
        except Exception:
            logger.exception(f"转换JXL无损格式出错: {input_path}")
            return False
            
    def _replace_original_file(self, input_path: str, output_path: str, new_size: int) -> bool:
        """替换原始文件
        
        Args:
            input_path: 原始文件路径
            output_path: 新文件路径
            new_size: 新文件大小
            
        Returns:
            bool: 是否成功替换
        """
        try:
            # 确保新文件有效且大小正常
            if os.path.exists(output_path) and new_size > 0:
                # 如果新文件与原文件不是同一文件，且原文件仍然存在
                if os.path.abspath(input_path) != os.path.abspath(output_path) and os.path.exists(input_path):
                    # 尝试删除原始文件
                    try:
                        os.remove(input_path)
                        logger.info(f"已删除原始文件: {input_path}")
                        return True
                    except PermissionError:
                        # 尝试使用Windows命令强制删除
                        logger.warning(f"常规删除失败，尝试强制删除: {input_path}")
                        try:
                            subprocess.run(['cmd', '/c', 'del', '/f', input_path], check=True)
                            if not os.path.exists(input_path):
                                logger.info(f"已强制删除原始文件: {input_path}")
                                # 如果需要，将新文件移动到原始位置
                                # if os.path.abspath(input_path) != os.path.abspath(output_path):
                                #     try:
                                #         os.rename(output_path, input_path)
                                #         logger.info(f"已将新文件移动到原始位置: {output_path} -> {input_path}")
                                #     except Exception as e:
                                #         logger.warning(f"移动新文件到原始位置失败: {str(e)}")
                                #         return False
                                return True
                            else:
                                logger.warning(f"强制删除失败: {input_path}")
                                return False
                        except Exception as e:
                            logger.warning(f"强制删除失败: {input_path}, 错误: {str(e)}")
                            return False
                    except OSError as e:
                        # 其他文件系统错误
                        logger.warning(f"删除原始文件失败: {input_path}, 错误: {str(e)}")
                        # 尝试等待少量时间后重试一次
                        try:
                            time.sleep(0.5)
                            os.remove(input_path)
                            logger.info(f"重试删除成功: {input_path}")
                            return True
                        except Exception:
                            return False
            return False
        except Exception as e:
            logger.warning(f"替换原始文件失败: {input_path}, 错误: {str(e)}")
            return False    
    def _calculate_compression_ratio(self, original_size: int, new_size: int) -> float:
        """计算压缩率
        
        Args:
            original_size: 原始文件大小
            new_size: 新文件大小
            
        Returns:
            float: 压缩率（百分比）
        """
        return ((original_size - new_size) / original_size * 100) if original_size > 0 else 0
    
    def _is_jxl_lossless(self, file_path: str) -> bool:
        """检查JXL文件是否为无损格式"""
        try:
            # 使用djxl工具检查文件信息
            result = subprocess.run(['djxl', '--info', file_path], capture_output=True, text=True)
            if result.returncode == 0:
                # 检查输出中是否包含无损相关信息
                return 'Lossless' in result.stdout and 'true' in result.stdout.lower()
            return False
        except (FileNotFoundError, subprocess.SubprocessError):
            return False
    
    def convert_directory(self, input_dir: str, output_dir: Optional[str] = None, recursive: bool = True, replace_original: bool = False, archive_path: Optional[str] = None) -> Dict: # 新增 archive_path 参数
        """转换目录中的所有图片
        
        Args:
            input_dir: 输入目录路径
            output_dir: 输出目录路径，如不指定则使用原目录
            recursive: 是否递归处理子目录
            replace_original: 是否替换原始文件（仅当output_dir未指定时生效）
            archive_path: (可选) 关联的压缩包路径，用于黑名单功能
        
        Returns:
            Dict: 包含处理结果的字典
        """
        start_time = time.time()
        
        # 创建新的批次用于跟踪压缩率，传入 archive_path
        batch_archive_path = archive_path if archive_path else input_dir # 如果没有提供压缩包路径，使用输入目录作为标识
        self._current_batch_id = compression_tracker.start_batch(batch_archive_path)
        
        # 收集需要处理的图片文件
        image_files = []
        
        if recursive:
            for root, _, files in os.walk(input_dir):
                for file in files:
                    file_path = os.path.join(root, file)
                    file_ext = os.path.splitext(file.lower())[1]
                    if file_ext in self.config['source_formats']:
                        image_files.append(file_path)
                        
                        # 如果指定了输出目录，计算相对路径
                        if output_dir:
                            rel_path = os.path.relpath(root, input_dir)
                            target_dir = os.path.join(output_dir, rel_path)
                            if not os.path.exists(target_dir):
                                os.makedirs(target_dir)
        else:
            for file in os.listdir(input_dir):
                file_path = os.path.join(input_dir, file)
                if os.path.isfile(file_path):
                    file_ext = os.path.splitext(file.lower())[1]
                    if file_ext in self.config['source_formats']:
                        image_files.append(file_path)
        
        # 如果输出目录与输入目录相同，且要求替换原始文件
        replace_files = replace_original and (not output_dir or output_dir == input_dir)
        
        # 初始化结果数据结构
        result = {
            'total': len(image_files),
            'success': 0,
            'failed': 0,
            'skipped': 0,
            'total_original_size': 0,
            'total_new_size': 0,
            'processing_time': 0,
            'results': []
        }
        
        logger.info(f"[#image]开始处理目录: {input_dir}，共{len(image_files)}个文件")
        
        # 调用批量处理，传入replace_original参数
        with ThreadPoolExecutor(max_workers=self.thread_count) as executor:
            futures = []
            for input_path in image_files:
                if output_dir:
                    rel_path = os.path.relpath(os.path.dirname(input_path), input_dir)
                    target_dir = os.path.join(output_dir, rel_path)
                    if not os.path.exists(target_dir):
                        os.makedirs(target_dir)
                    output_path = os.path.join(
                        target_dir, 
                        os.path.basename(os.path.splitext(input_path)[0]) + self.config['target_format']
                    )
                else:
                    output_path = None
                    
                futures.append(executor.submit(
                    self.convert_image, 
                    input_path, 
                    output_path, 
                    replace_files
                ))
            
            completed = 0
            total = len(image_files)
            
            for future in as_completed(futures):
                # 检查是否应该提前终止批处理（如发现连续多次负压缩）
                if compression_tracker.should_stop_batch(self._current_batch_id):
                    # 取消所有未完成的任务
                    cancel_count = 0
                    for f in futures:
                        if not f.done() and not f.running():
                            f.cancel()
                            cancel_count += 1
                    
                    logger.warning(f"[#image]批量处理因连续负压缩率而提前终止，取消了{cancel_count}个未开始的任务")
                    # 继续处理已完成的任务，但不再提交新任务
                
                try:
                    image_result = future.result()
                    result['results'].append(image_result)
                    
                    completed += 1
                    # 添加进度条显示
                    logger.info(f"[@progress]处理进度: [{completed}/{total}] {completed/total*100:.1f}%")
                    
                    if image_result['success']:
                        # 判断条件更改：只有在路径相同且文件大小未变化时才算作skipped
                        if (image_result['input_path'] == image_result['output_path'] and 
                            image_result['original_size'] == image_result['new_size']):
                            result['skipped'] += 1
                        else:
                            # 路径不同，或者虽然路径相同但大小变化了，都算作成功
                            result['success'] += 1
                        
                        result['total_original_size'] += image_result['original_size']
                        result['total_new_size'] += image_result['new_size']
                    else:
                        result['failed'] += 1
                except Exception as e:
                    logger.exception(f"处理批量任务时出错")
                    result['failed'] += 1
        
        # 处理完成后清理批次数据
        if self._current_batch_id:
            # 获取批次统计信息用于日志
            stats = compression_tracker.get_batch_stats(self._current_batch_id)
            if stats and stats.get('should_stop', False):
                logger.warning(f"[#image]批次因连续负压缩率而中止，共处理：{stats.get('total_count', 0)}个文件")
            
            # 清理批次数据
            compression_tracker.cleanup_batch(self._current_batch_id)
            self._current_batch_id = None
        
        result['processing_time'] = time.time() - start_time
        
        # 计算总体压缩比
        size_reduction = result['total_original_size'] - result['total_new_size']
        compression_ratio = (size_reduction / result['total_original_size'] * 100) if result['total_original_size'] > 0 else 0
        
        logger.info(f"批量处理完成: 共{result['total']}个文件, 成功{result['success']}个, "
                    f"跳过{result['skipped']}个, 失败{result['failed']}个, "
                    f"压缩比{compression_ratio:.1f}%, 耗时{result['processing_time']:.1f}秒")
        
        return result




if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='图片格式转换工具')
    parser.add_argument('paths', nargs='+', help='要处理的图片路径或目录')
    parser.add_argument('--config', '-c', help='JSON配置文件路径')
    parser.add_argument('--output', '-o', help='输出目录')
    parser.add_argument('--format', '-f', choices=['avif', 'webp', 'jxl', 'jpg', 'png'], default='avif',
                        help='图片转换目标格式 (默认: avif)')
    parser.add_argument('--quality', '-q', type=int, default=90,
                        help='图片转换质量 (1-100, 默认: 90)')
    parser.add_argument('--lossless', '-l', action='store_true',
                        help='启用无损压缩模式')
    parser.add_argument('--threads', '-t', type=int, default=os.cpu_count(),
                        help=f'线程数 (默认: {os.cpu_count()})')
    parser.add_argument('--recursive', '-r', action='store_true',
                        help='递归处理子目录')
    
    args = parser.parse_args()
    
    # 准备配置
    config = {}
    
    # 读取配置文件
    if args.config:
        try:
            with open(args.config, 'r', encoding='utf-8') as f:
                file_config = json.loads(f.read())
                config.update(file_config)
        except Exception as e:
            logger.error(f"读取配置文件失败: {e}")
    
    # 命令行参数覆盖配置文件
    config['target_format'] = f".{args.format}"
    config['thread_count'] = args.threads
    
    # 更新格式配置
    format_config = f"{args.format}_config"
    if format_config not in config:
        config[format_config] = {}
    
    config[format_config]['quality'] = args.quality
    if args.lossless:
        config[format_config]['lossless'] = True
    
    # 创建转换器
    converter = ImageConverter(config)
    
    # 处理输入路径
    results = []
    
    for path in args.paths:
        if os.path.isdir(path):
            # 处理目录
            result = converter.convert_directory(path, args.output, args.recursive)
            results.append(result)
        elif os.path.isfile(path):
            # 处理单个文件
            if args.output:
                output_path = os.path.join(
                    args.output,
                    os.path.basename(os.path.splitext(path)[0]) + config['target_format']
                )
                if not os.path.exists(args.output):
                    os.makedirs(args.output)
            else:
                output_path = None
                
            result = converter.convert_image(path, output_path)
            results.append(result)
        else:
            logger.error(f"路径不存在: {path}")
    
    # 保存结果统计
    # result_file = f"image_converter_results_{time.strftime('%Y%m%d_%H%M%S')}.json"
    # with open(result_file, 'w', encoding='utf-8') as f:
    #     json.dump(results, f, indent=2)
    # logger.info(f"转换结果已保存到: {result_file}")