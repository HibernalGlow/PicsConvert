import os
import json
import shutil
import zipfile
import tempfile
import logging
import time
import subprocess
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

# 导入我们的img_convert模块
from picsconvert.convert.img_convert import ImageConverter
from loguru import logger
from picsconvert.utils.archive_image_analyzer import ArchiveImageAnalyzer


# 支持的格式常量
SUPPORTED_ARCHIVE_FORMATS = {'.zip', '.cbz', '.cbr'}
VIDEO_FORMATS = {'.mp4', '.avi', '.mkv', '.wmv', '.flv', '.webm', '.mov', '.m4v', '.mpg', '.mpeg'}
AUDIO_FORMATS = {'.mp3', '.wav', '.flac', '.m4a', '.ogg', '.aac', '.wma', '.opus'}
# EXCLUDED_IMAGE_FORMATS = {'.gif','.jxl','.avif','.webp', '.psd', '.ai', '.cdr', '.eps', '.svg', '.raw', '.cr2', '.nef', '.arw', '.zip'}
EXCLUDED_IMAGE_FORMATS = {'.jxl','.avif'}

class ArchiveConverter:
    """压缩包图片转换器"""
    
    def __init__(self, config=None):
        """初始化转换器
        
        Args:
            config: 图片转换配置字典
                - target_format: 目标格式
                - quality: 压缩质量
                - thread_count: 线程数
                - min_width: 最小宽度
        """
        self.config = config or {}
        
        # 准备ImageConverter的配置
        converter_config = {
            'target_format': self.config.get('target_format', 'avif'),
            'thread_count': self.config.get('thread_count', 4),
            'enable_jxl_fallback': self.config.get('enable_jxl_fallback', False),
            f"{self.config.get('target_format', 'avif')}_config": {
                'quality': self.config.get('quality', 90),
                'lossless': self.config.get('lossless', False),
            }
        }
        
        # 创建ImageConverter实例
        self.image_converter = ImageConverter(converter_config)
        
        # 添加配置日志
        logger.info(f"[#image]转换配置: 目标格式={converter_config.get('target_format')}, 参数={converter_config}")
        
        # 设置线程数
        self.thread_count = self.config.get('thread_count', min(4, os.cpu_count() or 4))
        self.temp_directories = []

        # 添加连续负压缩率检测相关的属性
        self.check_negative_compression_rate = False  # 默认不开启检测
        self.negative_compression_limit = 3  # 默认连续3次则停止
        self.negative_compression_count = 0  # 计数器
        self.stopped_by_negative_compression = False  # 标记是否因负压缩率而停止
    
    def convert_archive(self, archive_path):
        """转换单个压缩包中的图片
        
        Args:
            archive_path: 压缩包路径
            
        Returns:
            bool: 是否成功处理
            dict: 处理结果统计
        """
        start_time = time.time()
        stats = {
            'archive_path': archive_path,
            'processed_images': 0,
            'skipped_images': 0,
            'original_size': 0,
            'converted_size': 0,
            'success': False,
            'error': None  # 添加错误信息字段
        }
        
        logger.info(f"[#archive]开始处理压缩包: {archive_path}")
        
        # 验证压缩包
        # is_valid, image_count = self._validate_archive(archive_path)
        is_valid = True
        if not is_valid:
            logger.info(f"[#archive]压缩包不需要处理或不包含图片: {archive_path}")
            stats['error'] = "压缩包不需要处理或不包含图片"
            # 添加失败记录到原压缩包
            # self._save_conversion_record(archive_path, stats, success=False)
            return False, stats
        
        # 检查是否应该跳过转换
        if self._should_skip_conversion(archive_path):
            stats['skipped_due_to_config'] = True
            stats['error'] = "使用相同配置已处理过"
            logger.info(f"[#archive]跳过处理: {archive_path} - 使用相同配置已处理过")
            # 不需要添加记录，因为已经存在
            return False, stats
        
        # 准备环境
        temp_dir, backup_path, new_archive_path = self._prepare_archive(archive_path)
        if not temp_dir:
            stats['error'] = "准备环境失败"
            # 添加失败记录到原压缩包
            return False, stats
        
        try:
            # 解压文件
            if not self._extract_archive(archive_path, temp_dir):
                logger.error(f"解压失败: {archive_path}")
                stats['error'] = "解压失败"
                # 添加失败记录到原压缩包
                # self._save_conversion_record(archive_path, stats, success=False)
                return False, stats
            
            # 处理图片 - 使用img_convert模块
            process_result = self._process_images_with_converter(temp_dir,archive_path)
            if not process_result:
                logger.info(f"没有需要处理的图片: {archive_path}")
                stats['error'] = "没有需要处理的图片"
                # 添加失败记录到原压缩包
                # self._save_conversion_record(archive_path, stats, success=False)
                return False, stats
                
            processed_count, skipped_count, original_size, converted_size = process_result
            
            if processed_count == 0:
                logger.info(f"没有需要处理的图片: {archive_path}")
                stats['error'] = "没有需要处理的图片"
                # 添加失败记录到原压缩包
                # self._save_conversion_record(archive_path, stats, success=False)
                return False, stats
                
            # 更新统计信息
            stats.update({
                'processed_images': processed_count,
                'skipped_images': skipped_count,
                'original_size': original_size / 1024 / 1024,  # MB
                'converted_size': converted_size / 1024 / 1024,  # MB
                'processing_time': time.time() - start_time
            })
            
            # 创建转换记录文件并放到临时目录中
            import hashlib
            archive_filename = os.path.basename(archive_path)
            md5_hash = hashlib.md5(archive_filename.encode()).hexdigest()
            convert_filename = f"{md5_hash}.convert"
            convert_file_path = os.path.join(temp_dir, convert_filename)
            
            # 构建转换记录
            record = {
                'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'filename': archive_filename,
                'config': {
                    'target_format': self.config.get('target_format', 'avif'),
                    'quality': self.config.get('quality', 90),
                    'lossless': self.config.get('lossless', False),
                    'min_width': self.config.get('min_width', -1)
                },
                'stats': {
                    'processed_images': stats.get('processed_images', 0),
                    'skipped_images': stats.get('skipped_images', 0),
                    'original_size_mb': stats.get('original_size', 0),
                    'converted_size_mb': stats.get('converted_size', 0),
                    'processing_time': stats.get('processing_time', 0)
                }
            }
            
            # 计算压缩率
            original_size = stats.get('original_size', 0)
            converted_size = stats.get('converted_size', 0)
            compression_ratio = ((original_size - converted_size) / original_size * 100) if original_size > 0 else 0
            record['compression_ratio'] = compression_ratio
            
            # 直接在临时目录中创建转换记录文件
            with open(convert_file_path, 'w', encoding='utf-8') as f:
                json.dump(record, f, indent=2)
                
            logger.info(f"[#file]已创建转换记录文件: {convert_filename}")
            
            # 创建新压缩包
            if not self._create_new_archive(temp_dir, new_archive_path):
                logger.error(f"创建新压缩包失败: {archive_path}")
                stats['error'] = "创建新压缩包失败"
                # 添加失败记录到原压缩包
                # self._save_conversion_record(archive_path, stats, success=False)
                return False, stats
                
            # 替换原始压缩包
            success = self._replace_archive(archive_path, new_archive_path, backup_path)
            
            # 更新成功状态
            stats['success'] = success
            
            if not success:
                stats['error'] = "替换原始压缩包失败"
                # 添加失败记录到原压缩包
                # self._save_conversion_record(archive_path, stats, success=False)
            
            logger.info(f"处理完成: {archive_path}, 处理了{processed_count}张图片, "
                       f"原始大小: {stats['original_size']:.2f}MB, "
                       f"新大小: {stats['converted_size']:.2f}MB, "
                       f"减少: {stats['original_size'] - stats['converted_size']:.2f}MB")
            
            return success, stats
            
        except Exception as e:
            logger.exception(f"处理压缩包时发生异常: {archive_path}")
            stats['error'] = f"处理异常: {str(e)}"
            # 添加失败记录到原压缩包
            # self._save_conversion_record(archive_path, stats, success=False)
            return False, stats
        finally:
            # 清理临时文件
            self._cleanup(temp_dir, new_archive_path, backup_path)
    
    def _validate_archive(self, archive_path):
        """验证压缩包是否需要处理"""
        try:
            # 检查文件扩展名
            if not any((archive_path.lower().endswith(ext) for ext in SUPPORTED_ARCHIVE_FORMATS)):
                logger.info(f"不支持的压缩包格式: {archive_path}")
                return False, 0
                
            # 检查文件是否被占用
            try:
                with open(archive_path, 'rb') as f:
                    f.read(1)
            except (IOError, PermissionError):
                logger.info(f"文件正在被占用: {archive_path}")
                return False, 0
                
            # 检查压缩包内容
            image_count = 0
            needs_processing = True
            target_ext = self.image_converter.config['target_format']
            source_formats = self.image_converter.config['source_formats']
            
            # 使用zipfile读取压缩包内容
            try:
                file_list = []
                with zipfile.ZipFile(archive_path, 'r') as zip_ref:
                    file_list = [f.filename for f in zip_ref.infolist()]
            except zipfile.BadZipFile:
                # 如果zipfile失败，尝试使用7z
                try:
                    cmd = ['7z', 'l', archive_path]
                    result = subprocess.run(cmd, capture_output=True, text=True)
                    if result.returncode != 0:
                        logger.info(f"压缩包可能损坏: {archive_path}")
                        return False, 0
                    
                    file_list = result.stdout.splitlines()
                except Exception:
                    logger.exception(f"检查压缩包内容出错: {archive_path}")
                    return False, 0
            
            if not file_list:
                logger.info(f"压缩包为空: {archive_path}")
                return False, 0
            
            # 检查是否包含视频或音频
            # if any(f.lower().endswith(tuple(VIDEO_FORMATS)) for f in file_list) or \
            #    any(f.lower().endswith(tuple(AUDIO_FORMATS)) for f in file_list):
            #     logger.info(f"压缩包中包含视频或音频文件: {archive_path}")
            #     return False, 0
            
            # 检查是否包含排除的图片格式
            # if any(f.lower().endswith(tuple(EXCLUDED_IMAGE_FORMATS)) for f in file_list):
            #     logger.info(f"压缩包中包含排除格式图片: {archive_path}")
            #     return False, 0
            
            # 检查最小宽度要求
            min_width = self.config.get('min_width', -1)
            if min_width > 0:  # -1表示关闭检查
                analyzer = ArchiveImageAnalyzer()
                avg_width = analyzer.get_archive_average_width(archive_path)
                if avg_width <= min_width:  # 修正符号为<=
                    logger.info(f"[#image]图片平均宽度({avg_width:.0f}px)小于最小要求({min_width}px): {archive_path}")  # 修改日志信息
                    return False, 0
            
            # 计算图片数量并检查是否需要处理
            # for filename in file_list:
            #     for ext in source_formats:
            #         if str(filename).lower().endswith(ext):
            #             image_count += 1
            #             if ext != target_ext:
            #                 needs_processing = True
            #                 break
            
            # 如果包含已处理的图片格式，则跳过处理
            # if any(f.lower().endswith(('.avif', '.jxl', '.webp')) for f in file_list):
            #     logger.info(f"[#archive]压缩包已包含已处理的图片格式: {archive_path}")
            # return False, 0
            
        except Exception as e:
            logger.exception(f"验证压缩包出错: {archive_path}")
            return False, 0
    
    def _prepare_archive(self, archive_path):
        """准备压缩包处理环境"""
        try:
            # 创建临时目录
            # Get the original directory and filename
            original_dir = os.path.dirname(archive_path)
            file_name = os.path.splitext(os.path.basename(archive_path))[0]
            
            # Create timestamp for unique directory name
            timestamp = time.strftime("%Y%m%d_%H%M%S", time.localtime())
            
            # Create temporary directory path
            temp_dir = os.path.join(original_dir, f'temp_{file_name}_{timestamp}')
            os.makedirs(temp_dir, exist_ok=True)
            
            # Keep track of temp directories for later cleanup
            self.temp_directories.append(temp_dir)
            logger.info(f'[#file]创建临时目录: {temp_dir}')
            
            # 创建备份和新压缩包路径
            backup_path = f"{archive_path}.bak"
            new_archive_path = f"{archive_path}.new"
            
            # 创建备份
            shutil.copy2(archive_path, backup_path)
            logger.info(f"[#file]创建备份: {backup_path}")
            
            return temp_dir, backup_path, new_archive_path
            
        except Exception:
            logger.exception(f"准备环境失败: {archive_path}")
            return None, None, None
    
    def _extract_archive(self, archive_path, temp_dir):
        """解压压缩包"""
        try:
            # 尝试使用7z解压
            try:
                cmd = ['7z', 'x', archive_path, f'-o{temp_dir}', '-y']
                result = subprocess.run(cmd, capture_output=True, text=True)
                if result.returncode == 0:
                    logger.info(f"[#file]使用7z成功解压: {archive_path}")
                    return True
                else:
                    logger.warning(f"[#file]7z解压失败，尝试备用方案: {archive_path}")
            except Exception:
                logger.exception(f"7z解压出错，尝试备用方案: {archive_path}")
                
            # 尝试使用zipfile
            # try:
            #     with zipfile.ZipFile(archive_path, 'r') as zip_ref:
            #         zip_ref.extractall(temp_dir)
            #         logger.info(f"使用zipfile成功解压: {archive_path}")
            #         return True
            # except zipfile.BadZipFile:
            #     logger.warning(f"无效的zip文件格式: {archive_path}")
            # except Exception:
            #     logger.exception(f"zipfile解压出错: {archive_path}")
                
            return False
            
        except Exception:
            logger.exception(f"解压文件时出错: {archive_path}")
            return False
    
    def _process_images_with_converter(self, temp_dir,archive_path):
        """使用img_convert模块处理图片
        
        Returns:
            tuple: (处理成功数量, 跳过数量, 原始总大小, 转换后总大小)
        """
        # 添加转换配置日志
        target_format = self.config.get('target_format', 'avif')
        format_config = {
            'quality': self.config.get('quality', 90),
            'lossless': self.config.get('lossless', False),
            'thread_count': self.thread_count
        }
        logger.info(f"[#image]转换配置: 目标格式={target_format}, 参数={format_config}")
        
        # 直接使用img_convert模块处理目录 - 添加replace_original=True
        result = self.image_converter.convert_directory(
            temp_dir, 
            output_dir=None, 
            recursive=True,
            replace_original=True,
            archive_path=archive_path
            # 添加这个参数让转换后替换原始文件
        )
        
        # 提取结果
        processed_count = result.get('success', 0)
        skipped_count = result.get('skipped', 0)
        original_size = result.get('total_original_size', 0)
        converted_size = result.get('total_new_size', 0)
        
        return processed_count, skipped_count, original_size, converted_size    
    def _create_new_archive(self, temp_dir, new_archive_path):
        """创建新的压缩包"""
        try:
            # 检查临时目录是否为空
            if not any(os.scandir(temp_dir)):
                logger.warning(f"临时目录为空: {temp_dir}")
                return False
                
            # 使用7z创建新压缩包
            try:
                cmd = ['7z', 'a', '-tzip', new_archive_path, os.path.join(temp_dir, '*')]
                result = subprocess.run(cmd, capture_output=True, text=True)
                if result.returncode == 0:
                    logger.info(f"使用7z成功创建压缩包: {new_archive_path}")
                    return True
                else:
                    logger.warning(f"7z创建压缩包失败，尝试备用方案: {result.stderr}")
            except Exception:
                logger.exception(f"7z创建压缩包出错，尝试备用方案")
                
            # 使用zipfile创建新压缩包
            try:
                with zipfile.ZipFile(new_archive_path, 'w', zipfile.ZIP_DEFLATED) as zip_ref:
                    for root, _, files in os.walk(temp_dir):
                        for file in files:
                            file_path = os.path.join(root, file)
                            arcname = os.path.relpath(file_path, temp_dir)
                            zip_ref.write(file_path, arcname)
                if os.path.exists(new_archive_path):
                    logger.info(f"使用zipfile成功创建压缩包: {new_archive_path}")
                    return True
            except Exception:
                logger.exception(f"zipfile创建压缩包出错")
                
            return False
            
        except Exception:
            logger.exception(f"创建新压缩包时出错: {new_archive_path}")
            return False
    
    def _replace_archive(self, original_path, new_path, backup_path):
        """替换原始压缩包"""
        try:
            if not os.path.exists(new_path):
                logger.warning(f"新压缩包不存在: {new_path}")
                return False
                
            # 比较文件大小
            original_size = os.path.getsize(original_path)
            new_size = os.path.getsize(new_path)
            
            # 检查转换是否有效 - 只有在大幅减小尺寸时才替换
            size_reduction = original_size - new_size
            reduction_percent = (size_reduction / original_size * 100) if original_size > 0 else 0
            
            logger.info(f"压缩包大小对比: 原始={original_size/1024/1024:.2f}MB, "
                        f"新文件={new_size/1024/1024:.2f}MB, "
                        f"减少={size_reduction/1024/1024:.2f}MB ({reduction_percent:.1f}%)")
            
            # 替换条件：大小减少超过5%或者至少减少1MB
            should_replace = (reduction_percent > 0.5) or (size_reduction > 1024*1024)
            
            if not should_replace:
                logger.info(f"[#archive]新压缩包大小减少不显著，不替换: {original_path}")
                return False
                
            # 替换文件
            # 检查是否为CBR文件
            # 普通情况下直接替换
            os.remove(original_path)
            is_cbr = original_path.lower().endswith('.cbr')
            if is_cbr:
                # 如果新文件大小小于原始文件，将CBR改名为ZIP
                original_path = os.path.splitext(original_path)[0] + '.zip'
            shutil.move(new_path, original_path)
            logger.info(f"[#archive]已替换原始压缩包: {original_path}")
            
            # 删除备份文件
            if os.path.exists(backup_path):
                os.remove(backup_path)
                logger.info(f"[#file]已删除备份文件: {backup_path}")
                
            return True
            
        except Exception:
            logger.exception(f"替换压缩包时出错: {original_path}")
            
            # 如果出错，尝试恢复原始文件
            try:
                if not os.path.exists(original_path) and os.path.exists(backup_path):
                    shutil.move(backup_path, original_path)
                    logger.info(f"已恢复原始文件: {original_path}")
            except Exception:
                logger.exception(f"恢复原始文件失败: {original_path}")
                
            return False    
    def _cleanup(self, temp_dir, new_path, backup_path):
        """清理临时文件"""
        try:
            # 删除临时目录
            if temp_dir and os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)
                self.temp_directories.remove(temp_dir)
                logger.info(f"[#file]已删除临时目录: {temp_dir}")
                
            # 删除新压缩包
            if new_path and os.path.exists(new_path):
                os.remove(new_path)
                logger.info(f"[#file]已删除临时压缩包: {new_path}")
                
            # 只有在出错时才保留备份文件
        except Exception:
            logger.exception("清理临时文件时出错")
    
    def cleanup_all(self):
        """清理所有临时目录"""
        for temp_dir in self.temp_directories[:]:
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)
                self.temp_directories.remove(temp_dir)
                logger.info(f"[#file]已删除临时目录: {temp_dir}")
    
    def _should_skip_conversion(self, archive_path):
        """检查是否应该跳过转换，通过读取压缩包内的.convert记录文件
        
        Args:
            archive_path: 压缩包路径
            
        Returns:
            bool: 是否应该跳过处理
        """
        try:
            import hashlib
            import tempfile
            
            # 计算压缩包文件名的MD5哈希值
            archive_filename = os.path.basename(archive_path)
            md5_hash = hashlib.md5(archive_filename.encode()).hexdigest()
            convert_filename = f"{md5_hash}.convert"
            
            # 检查压缩包中是否存在对应的记录文件
            record_exists = False
            record_content = None
            temp_dir = None
            
            try:
                # 尝试使用7z列出压缩包内容
                cmd = ['7z', 'l', archive_path]
                result = subprocess.run(cmd, capture_output=True, text=True)
                if result.returncode == 0:
                    # 检查输出中是否包含记录文件名
                    if convert_filename in result.stdout:
                        record_exists = True
                        
                        # 创建临时目录提取文件
                        temp_dir = tempfile.mkdtemp()
                        extract_cmd = ['7z', 'e', archive_path, convert_filename, f'-o{temp_dir}', '-y']
                        extract_result = subprocess.run(extract_cmd, capture_output=True, text=True)
                        
                        if extract_result.returncode == 0:
                            # 读取提取的记录文件
                            record_path = os.path.join(temp_dir, convert_filename)
                            if os.path.exists(record_path):
                                with open(record_path, 'r', encoding='utf-8') as f:
                                    record_content = json.load(f)
            except Exception:
                pass
            
            # 如果7z方法失败，尝试使用zipfile
            if not record_exists:
                try:
                    with zipfile.ZipFile(archive_path, 'r') as zip_ref:
                        file_list = [f.filename for f in zip_ref.infolist()]
                        if convert_filename in file_list:
                            record_exists = True
                            
                            # 创建临时目录
                            if not temp_dir:
                                temp_dir = tempfile.mkdtemp()
                                
                            # 提取记录文件
                            zip_ref.extract(convert_filename, temp_dir)
                            record_path = os.path.join(temp_dir, convert_filename)
                            
                            # 读取记录文件
                            if os.path.exists(record_path):
                                with open(record_path, 'r', encoding='utf-8') as f:
                                    record_content = json.load(f)
                except Exception:
                    pass
            
            # 清理临时目录
            if temp_dir and os.path.exists(temp_dir):
                import shutil
                shutil.rmtree(temp_dir, ignore_errors=True)
            
            # 如果没有找到记录文件，不跳过处理
            if not record_content:
                return False
                
            # 检查转换配置是否相同
            current_config = {
                'target_format': self.config.get('target_format', 'avif'),
                'quality': self.config.get('quality', 90),
                'lossless': self.config.get('lossless', False),
                'min_width': self.config.get('min_width', -1)
            }
            
            record_config = record_content.get('config', {})
            
            # 比较关键配置参数
            if (record_config.get('target_format') == current_config['target_format'] and
                record_config.get('quality') == current_config['quality'] and
                record_config.get('lossless') == current_config['lossless'] and
                record_config.get('min_width') == current_config['min_width']):
                
                # 记录跳过日志
                logger.info(f"[#archive]跳过处理: {os.path.basename(archive_path)} - 相同配置已处理 "
                           f"[{record_content.get('timestamp')}], 压缩率: {record_content.get('compression_ratio', 0):.1f}")
                return True
                
            return False
                
        except Exception as e:
            logger.warning(f"检查转换记录出错: {archive_path}, 错误: {str(e)}")
            return False
    
    def _save_conversion_record(self, archive_path, stats, success=True):
        """保存转换记录到压缩包内部的.convert文件
        
        Args:
            archive_path: 压缩包路径
            stats: 处理统计结果
            success: 是否成功转换，默认为True
            
        Returns:
            bool: 是否成功保存记录
        """
        try:
            import hashlib
            
            # 计算压缩包文件名的MD5哈希值
            archive_filename = os.path.basename(archive_path)
            md5_hash = hashlib.md5(archive_filename.encode()).hexdigest()
            convert_filename = f"{md5_hash}.convert"
            
            # 构建转换记录
            record = {
                'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'filename': archive_filename,  # 添加原始文件名信息
                'success': success,  # 添加成功/失败状态
                'config': {
                    'target_format': self.config.get('target_format', 'avif'),
                    'quality': self.config.get('quality', 90),
                    'lossless': self.config.get('lossless', False),
                    'min_width': self.config.get('min_width', -1)
                },
                'stats': {
                    'processed_images': stats.get('processed_images', 0),
                    'skipped_images': stats.get('skipped_images', 0),
                    'original_size_mb': stats.get('original_size', 0),
                    'converted_size_mb': stats.get('converted_size', 0),
                    'processing_time': stats.get('processing_time', 0)
                }
            }
            
            # 如果失败，添加错误信息
            if not success and 'error' in stats:
                record['error'] = stats['error']
                logger.info(f"[#archive]记录失败信息: {stats['error']}")
            
            # 计算压缩率
            original_size = stats.get('original_size', 0)
            converted_size = stats.get('converted_size', 0)
            compression_ratio = ((original_size - converted_size) / original_size * 100) if original_size > 0 else 0
            record['compression_ratio'] = compression_ratio
            
            # 直接在压缩包所在目录创建临时文件
            archive_dir = os.path.dirname(archive_path)
            temp_file_path = os.path.join(archive_dir, f"temp_{convert_filename}")
            
            # 写入JSON数据到临时文件
            with open(temp_file_path, 'w', encoding='utf-8') as f:
                json.dump(record, f, indent=2)
            
            # 将临时文件添加到压缩包中
            try:
                # 尝试使用7z添加文件到压缩包
                cmd = ['7z', 'a', archive_path, temp_file_path, f'-si{convert_filename}']
                result = subprocess.run(cmd, capture_output=True, text=True)
                if result.returncode == 0:
                    status_text = "失败" if not success else "成功"
                    logger.info(f"[#file]已保存转换{status_text}记录到压缩包内部: {convert_filename}")
                # else:
                #     # 如果7z失败，尝试使用zipfile
                #     try:
                #         with zipfile.ZipFile(archive_path, 'a', zipfile.ZIP_DEFLATED) as zip_ref:
                #             zip_ref.write(temp_file_path, convert_filename)
                #         status_text = "失败" if not success else "成功"
                #         logger.info(f"[#file]已保存转换{status_text}记录到压缩包内部: {convert_filename}")
                #     except Exception as e:
                #         logger.warning(f"使用zipfile添加记录文件失败: {str(e)}")
                #         return False
            except Exception as e:
                logger.warning(f"使用7z添加记录文件失败: {str(e)}")
                return False
            finally:
                # 删除临时文件
                if os.path.exists(temp_file_path):
                    os.remove(temp_file_path)
                    
            return True
                
        except Exception as e:
            logger.warning(f"保存转换记录出错: {archive_path}, 错误: {str(e)}")
            return False


def main():
    """主函数，用于命令行调用"""
    import argparse
    
    parser = argparse.ArgumentParser(description='压缩包图片转换工具')
    parser.add_argument('archives', nargs='+', help='要处理的压缩包路径')
    parser.add_argument('--config', '-c', help='JSON配置文件路径')
    parser.add_argument('--format', '-f', choices=['avif', 'webp', 'jxl'], default='avif',
                        help='图片转换目标格式 (默认: avif)')
    parser.add_argument('--quality', '-q', type=int, default=90,
                        help='图片转换质量 (1-100, 默认: 90)')
    parser.add_argument('--lossless', '-l', action='store_true',
                        help='启用无损压缩模式')
    parser.add_argument('--threads', '-t', type=int, default=1,
                        help=f'线程数 (默认: {os.cpu_count()})')
    parser.add_argument('--min-width', type=int, default=-1,
                        help='最小图片宽度(像素)，-1为关闭检查 (默认: -1)')
    
    args = parser.parse_args()
    
    # 准备配置
    config = {}
    
    # 读取配置文件
    if args.config:
        try:
            with open(args.config, 'r', encoding='utf-8') as f:
                file_config = json.load(f)
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
    
    # 初始化转换器
    converter = ArchiveConverter(config)
    
    # 处理压缩包
    total_start_time = time.time()
    processed_count = 0
    failed_count = 0
    stats = []
    
    logger.info(f"开始处理 {len(args.archives)} 个压缩包")
    for archive in args.archives:
        if os.path.exists(archive):
            success, result = converter.convert_archive(archive)
            stats.append(result)
            if success:
                processed_count += 1
            else:
                failed_count += 1
        else:
            logger.error(f"文件不存在: {archive}")
    
    # 显示处理结果
    total_time = time.time() - total_start_time
    logger.info(f"处理完成，耗时: {total_time:.2f}秒")
    logger.info(f"成功处理: {processed_count} 个压缩包")
    logger.info(f"失败: {failed_count} 个压缩包")
    
    # 清理临时文件
    converter.cleanup_all()
    
    # 保存结果统计
    # result_file = f"archive_converter_result_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    # with open(result_file, 'w', encoding='utf-8') as f:
    #     json.dump({
    #         'total_archives': len(args.archives),
    #         'processed': processed_count,
    #         'failed': failed_count,
    #         'total_time': total_time,
    #         'stats': stats
    #     }, f, indent=2)
    # logger.info(f"结果统计已保存到: {result_file}")


if __name__ == "__main__":
    main()