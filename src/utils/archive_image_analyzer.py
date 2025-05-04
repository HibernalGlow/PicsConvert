import os
import logging
import zipfile
from io import BytesIO
from PIL import Image, ImageFile
import warnings
from typing import Dict, List, Tuple, Union, Optional
import statistics
from concurrent.futures import ThreadPoolExecutor
import multiprocessing
import pillow_avif
import pillow_jxl
# 基础设置
warnings.filterwarnings('ignore', category=Image.DecompressionBombWarning)
Image.MAX_IMAGE_PIXELS = None
ImageFile.LOAD_TRUNCATED_IMAGES = True



class ArchiveImageAnalyzer:
    """压缩包图片分析器，提供各种图片分析功能"""
    
    def __init__(self, logger=None, max_workers=None):
        """初始化分析器
        
        Args:
            logger: 日志记录器，如果为None则创建默认记录器
            max_workers: 最大工作线程数，默认为CPU核心数
        """
        self.logger = logger or logging.getLogger(__name__)
        self.max_workers = max_workers or multiprocessing.cpu_count()
        
        # 支持的图片格式
        self.supported_formats = {
            '.jpg', '.jpeg', '.png', '.webp', '.bmp', 
            '.avif', '.jxl', '.gif', '.heic', '.heif'
        }
    
    def get_image_width_from_zip(self, zip_file, image_path) -> int:
        """从压缩包中获取图片宽度
        
        Args:
            zip_file: 打开的zipfile对象
            image_path: 压缩包内图片路径
            
        Returns:
            int: 图片宽度，失败返回0
        """
        try:
            with zip_file.open(image_path) as file:
                img_data = BytesIO(file.read())
                with Image.open(img_data) as img:
                    return img.size[0]
        except Exception as e:
            self.logger.error(f"读取图片出错 {image_path}: {str(e)}")
            return 0
    
    def get_image_size_from_zip(self, zip_file, image_path) -> Tuple[int, int]:
        """从压缩包中获取图片尺寸(宽度和高度)
        
        Args:
            zip_file: 打开的zipfile对象
            image_path: 压缩包内图片路径
            
        Returns:
            Tuple[int, int]: (宽度, 高度)，失败返回(0, 0)
        """
        try:
            with zip_file.open(image_path) as file:
                img_data = BytesIO(file.read())
                with Image.open(img_data) as img:
                    return img.size
        except Exception as e:
            self.logger.error(f"读取图片尺寸出错 {image_path}: {str(e)}")
            return (0, 0)
    
    def get_archive_average_width(self, zip_path: str, sample_size: int = 20) -> float:
        """获取压缩包内图片的平均宽度
        
        Args:
            zip_path: 压缩包路径
            sample_size: 采样数量，默认20张
            
        Returns:
            float: 平均宽度，如果没有图片或出错则返回0
        """
        try:
            with zipfile.ZipFile(zip_path, 'r') as zf:
                # 获取所有图片文件
                image_files = [f for f in zf.namelist() if os.path.splitext(f.lower())[1] in self.supported_formats]
                
                if not image_files:
                    self.logger.warning(f"[#file_ops]压缩包 {zip_path} 中没有找到图片")
                    return 0
                
                # 改进的抽样算法
                image_files.sort()  # 确保文件顺序一致
                total_images = len(image_files)
                
                # 计算抽样
                sample_size = min(sample_size, total_images)  # 最多抽样指定数量的图片
                if total_images <= sample_size:
                    sampled_files = image_files  # 如果图片数量较少，使用所有图片
                else:
                    # 确保抽样包含：
                    # 1. 开头的几张图片
                    # 2. 结尾的几张图片
                    # 3. 均匀分布的中间图片
                    head_count = min(3, total_images)  # 开头取3张
                    tail_count = min(3, total_images)  # 结尾取3张
                    middle_count = sample_size - head_count - tail_count  # 中间的图片数量
                    
                    # 获取头部图片
                    head_files = image_files[:head_count]
                    # 获取尾部图片
                    tail_files = image_files[-tail_count:]
                    # 获取中间的图片
                    if middle_count > 0:
                        step = (total_images - head_count - tail_count) // (middle_count + 1)
                        middle_indices = range(head_count, total_images - tail_count, step)
                        middle_files = [image_files[i] for i in middle_indices[:middle_count]]
                    else:
                        middle_files = []
                    
                    sampled_files = head_files + middle_files + tail_files
                    self.logger.debug(f"抽样数量: {len(sampled_files)}/{total_images} (头部:{len(head_files)}, 中间:{len(middle_files)}, 尾部:{len(tail_files)})")

                # 收集宽度
                widths = []
                for img in sampled_files:
                    width = self.get_image_width_from_zip(zf, img)
                    if width > 0:
                        widths.append(width)
                
                # 计算平均宽度
                if widths:
                    avg_width = statistics.mean(widths)
                    self.logger.info(f"压缩包 {zip_path} - 平均宽度: {avg_width:.2f}px, 采样: {len(widths)}/{total_images}")
                    return avg_width
                else:
                    self.logger.warning(f"压缩包 {zip_path} 中没有有效的图片宽度")
                    return 0
                    
        except Exception as e:
            self.logger.error(f"处理压缩包出错 {zip_path}: {str(e)}")
            return 0
    
    def get_archive_image_stats(self, zip_path: str, sample_size: int = 20) -> Dict:
        """获取压缩包内图片的统计信息
        
        Args:
            zip_path: 压缩包路径
            sample_size: 采样数量，默认20张
            
        Returns:
            Dict: 包含各种统计信息的字典，如果出错则返回空字典
        """
        try:
            with zipfile.ZipFile(zip_path, 'r') as zf:
                # 获取所有图片文件
                image_files = [f for f in zf.namelist() if os.path.splitext(f.lower())[1] in self.supported_formats]
                
                if not image_files:
                    self.logger.warning(f"[#file_ops]压缩包 {zip_path} 中没有找到图片")
                    return {}
                
                # 改进的抽样算法
                image_files.sort()  # 确保文件顺序一致
                total_images = len(image_files)
                
                # 计算抽样
                sample_size = min(sample_size, total_images)  # 最多抽样指定数量的图片
                if total_images <= sample_size:
                    sampled_files = image_files  # 如果图片数量较少，使用所有图片
                else:
                    # 确保抽样包含：
                    # 1. 开头的几张图片
                    # 2. 结尾的几张图片
                    # 3. 均匀分布的中间图片
                    head_count = min(3, total_images)  # 开头取3张
                    tail_count = min(3, total_images)  # 结尾取3张
                    middle_count = sample_size - head_count - tail_count  # 中间的图片数量
                    
                    # 获取头部图片
                    head_files = image_files[:head_count]
                    # 获取尾部图片
                    tail_files = image_files[-tail_count:]
                    # 获取中间的图片
                    if middle_count > 0:
                        step = (total_images - head_count - tail_count) // (middle_count + 1)
                        middle_indices = range(head_count, total_images - tail_count, step)
                        middle_files = [image_files[i] for i in middle_indices[:middle_count]]
                    else:
                        middle_files = []
                    
                    sampled_files = head_files + middle_files + tail_files
                
                # 收集尺寸信息
                widths = []
                heights = []
                sizes = []
                
                for img in sampled_files:
                    width, height = self.get_image_size_from_zip(zf, img)
                    if width > 0 and height > 0:
                        widths.append(width)
                        heights.append(height)
                        sizes.append((width, height))
                
                # 计算统计信息
                if not widths:
                    self.logger.warning(f"压缩包 {zip_path} 中没有有效的图片")
                    return {}
                    
                stats = {
                    "total_images": total_images,
                    "sampled_images": len(widths),
                    "avg_width": statistics.mean(widths) if widths else 0,
                    "avg_height": statistics.mean(heights) if heights else 0,
                    "min_width": min(widths) if widths else 0,
                    "max_width": max(widths) if widths else 0,
                    "min_height": min(heights) if heights else 0,
                    "max_height": max(heights) if heights else 0,
                    "median_width": statistics.median(widths) if widths else 0,
                    "median_height": statistics.median(heights) if heights else 0,
                }
                
                # 计算宽高比
                if widths and heights:
                    ratios = [w/h for w, h in sizes]
                    stats["avg_ratio"] = statistics.mean(ratios)
                    stats["median_ratio"] = statistics.median(ratios)
                
                self.logger.info(f"压缩包 {zip_path} - 平均宽度: {stats['avg_width']:.2f}px, 平均高度: {stats['avg_height']:.2f}px")
                return stats
                    
        except Exception as e:
            self.logger.error(f"处理压缩包出错 {zip_path}: {str(e)}")
            return {}
    
    def batch_process_archives(self, zip_paths: List[str], stats_only: bool = False) -> Dict[str, Union[float, Dict]]:
        """批量处理多个压缩包
        
        Args:
            zip_paths: 压缩包路径列表
            stats_only: 是否只返回统计信息，默认False只返回平均宽度
            
        Returns:
            Dict: 压缩包路径到结果的映射
        """
        results = {}
        
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = []
            
            for zip_path in zip_paths:
                if stats_only:
                    future = executor.submit(self.get_archive_image_stats, zip_path)
                else:
                    future = executor.submit(self.get_archive_average_width, zip_path)
                futures.append((zip_path, future))
            
            for zip_path, future in futures:
                try:
                    results[zip_path] = future.result()
                except Exception as e:
                    self.logger.error(f"处理压缩包失败 {zip_path}: {str(e)}")
                    results[zip_path] = 0 if not stats_only else {}
        
        return results