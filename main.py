import os
import sys
import threading
import argparse
from pathlib import Path
from typing import Dict, Any, List, Tuple, Set
import time
import json # 新增导入
from functools import partial # 新增导入

current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(current_dir)
from nodes.file_ops.input_handler import InputHandler
from src.convert.format_convert import ArchiveConverter,SUPPORTED_ARCHIVE_FORMATS
from nodes.record.logger_config import setup_logger
from src.convert.performance_control import get_performance_params,start_config_gui_thread
# 导入黑名单文件路径
from src.convert.compression_tracker import BLACKLIST_FILE_PATH
from nodes.monitor.monitor_decorator import infinite_monitor
from nodes.tui.preset.textual_preset import create_config_app
from nodes.tui.textual_logger import TextualLoggerManager
import zipfile
from concurrent.futures import ThreadPoolExecutor

# 获取logger实例
config = {
    'script_name': 'pics_convert',
    "console_enabled": False,
}
logger, config_info = setup_logger(config)
USE_RICH = False  # 是否使用Rich库进行输出

# 定义需要跳过的格式列表
SKIP_FORMATS: Set[str] = {
    '.avif', '.jxl', '.webp'  # 默认跳过这些格式
}

# 全局变量用于存储跳过格式列表，可通过命令行覆盖
ACTIVE_SKIP_FORMATS: Set[str] = SKIP_FORMATS.copy()

# 定义需要跳过的文件名关键词
SKIP_KEYWORDS: Set[str] = {
    '_avif', '_jxl', '_webp',  # 默认跳过包含这些关键词的文件
    'avif_', 'jxl_', 'webp_',
    '.avif', '.jxl', '.webp'
}

# 定义路径黑名单关键词列表
BLACKLIST_PATHS: Set[str] = {
    'temp_'  # 默认跳过包含这些关键词的路
}

# 全局变量用于存储活跃的黑名单路径关键词，可通过命令行覆盖
ACTIVE_BLACKLIST_PATHS: Set[str] = BLACKLIST_PATHS.copy()

LAYOUT_CONFIG = {
    "status": {
        "ratio": 1,
        "title": "🏭 总体进度",
        "style": "lightblue"
    },
    "progress": {
        "ratio": 1,
        "title": "🔄 当前进度",
        "style": "lightgreen"
    },
    "performance": {
        "ratio": 1,
        "title": "📹 性能监控",  # 更新标题
        "style": "lightyellow"
    },
    "image": {
        "ratio": 2,
        "title": "🖼️ 图片转换",
        "style": "lightsalmon"
    },   
    "archive": {
        "ratio": 2,
        "title": "📦 压缩包处理",
        "style": "lightpink"
    },
    "file": {
        "ratio": 2,
        "title": "📂 文件操作",
        "style": "lightcyan"
    },

}

def init_layout():
    TextualLoggerManager.set_layout(LAYOUT_CONFIG, config_info['log_file'])


def process_archive(*args, **kwargs) -> None:
    """处理单个压缩包
    
    Args:
        archive_path: 压缩包路径
        filter_params: 过滤参数字典
        **kwargs: 其他参数
            - min_width: 最小图片宽度
            - thread_count: 线程数 
            - batch_size: 批处理大小
            - infinite_mode: 是否无限模式
            - interval_minutes: 监控间隔(分钟)
    """

    # 提取必要参数
    archive_path = args[0] if args else kwargs.get('archive_path')
    filter_params = kwargs.get('filter_params', {})
    
    # 检查文件格式
    file_ext = Path(archive_path).suffix.lower()
    if file_ext not in SUPPORTED_ARCHIVE_FORMATS:
        logger.info(f"[#archive]不支持的文件格式: {file_ext}")
        return
    
    # 监控性能参数并处理暂停逻辑
    def check_performance_params(current_thread_count=None, current_batch_size=None):
        nonlocal thread_count, batch_size
        new_thread_count, new_batch_size, is_paused = get_performance_params()
        
        # 检查参数是否发生变化
        if new_thread_count != current_thread_count or new_batch_size != current_batch_size:
            logger.info(f"[#performance]🧵 线程数: {new_thread_count} | 批处理: {new_batch_size}")
            thread_count, batch_size = new_thread_count, new_batch_size
            
        # 处理暂停逻辑
        if is_paused:
            logger.info(f"[#performance]⏸ 处理已暂停: {archive_path}")
            while is_paused:
                time.sleep(0.5)  # 防止过于频繁的检查
                _, _, is_paused = get_performance_params()
                # 在暂停状态下继续监控参数变化
                check_performance_params(thread_count, batch_size)
                
            logger.info(f"[#performance]▶ 处理已恢复: {archive_path}")
        
        return thread_count, batch_size, is_paused
    
    # 初始化性能参数
    thread_count, batch_size, is_paused = get_performance_params()
    logger.info(f"[#performance]🧵 线程数: {thread_count} | 批处理: {batch_size}")
    
    # 初次检查是否暂停
    thread_count, batch_size, is_paused = check_performance_params(thread_count, batch_size)
    
    # 设置定期检查计时器
    last_check_time = time.time()
    check_interval = 2.0  # 秒

    
    # 修改转换器配置参数
    converter_params = {
        'thread_count': thread_count,
        'min_width': filter_params.get('min_width', -1),
        'target_format': kwargs.get('format', 'avif').lower(),    # 确保格式小写
        'quality': int(kwargs.get('quality', 90)),                # 确保质量是整数
        'lossless': kwargs.get('lossless', False)                 # 添加无损选项
    }
    
    converter = ArchiveConverter(converter_params)
    try:
        converter.convert_archive(archive_path)
        logger.info(f"[#archive]✅ 成功处理: {archive_path}")
    except Exception as e:
        logger.info(f"[#archive]❌ 处理失败: {archive_path} - {str(e)}")


def process_archives(archive_paths: List[str], **kwargs) -> None:
    """批量处理压缩包，支持无限模式监控
    
    Args:
        archive_paths: 压缩包路径列表
        **kwargs: 其他参数
            - filter_params: 过滤参数字典
            - interval_minutes: 监控间隔(分钟)
            - directories: 监控的目录列表
            - archive_path: (可选) 关联的压缩包路径，用于黑名单功能
    """
    # 添加总进度记录
    total_files = len(archive_paths)
    current_file = 0
    logger.info(f"[#status]开始处理,共{total_files}个文件")
    
    # 根据模式处理文件
    for archive_path in archive_paths:
        current_file += 1
        progress = (current_file / total_files) * 100
        logger.info(f"[@status]总进度:({current_file}/{total_files}) {progress:.1f}% ")
        logger.info(f"[#archive]处理: {archive_path}")
        
        # 调用单个压缩包处理函数，传递 archive_path
        process_kwargs = kwargs.copy()
        process_kwargs['archive_path'] = archive_path # 确保 archive_path 传递下去
        process_archive(**process_kwargs)
    
    # 处理完成后输出最终进度
    logger.info(f"[#status]处理完成 - 共处理{total_files}个文件")

def check_archive_skip(archive_path: str, json_blacklist: Set[str]) -> Tuple[str, bool, str]: # 新增 json_blacklist 参数
    """检查压缩包是否应该被跳过
    
    Args:
        archive_path: 压缩包路径
        json_blacklist: 从JSON文件加载的黑名单路径集合
        
    Returns:
        Tuple[str, bool, str]: (压缩包路径, 是否跳过, 跳过原因)
    """
    try:
        resolved_path_str = str(Path(archive_path).resolve())

        # 1. 检查JSON黑名单
        if resolved_path_str in json_blacklist:
            logger.info(f"[#archive]跳过JSON黑名单中的压缩包: {resolved_path_str}")
            return (archive_path, True, "json_blacklist")

        # 2. 检查路径是否包含命令行指定的黑名单关键词
        if ACTIVE_BLACKLIST_PATHS:
            full_path_lower = resolved_path_str.lower()
            if any(keyword.lower() in full_path_lower for keyword in ACTIVE_BLACKLIST_PATHS):
                logger.info(f"[#archive]跳过命令行黑名单路径的压缩包: {resolved_path_str}")
                return (archive_path, True, "keyword_blacklist")
        
        # 3. 检查文件名是否包含需要跳过的关键词 (如果需要可以取消注释)
        # file_name = Path(archive_path).stem.lower()  # 取消注释以启用文件名检查
        # if any(keyword in file_name for keyword in SKIP_KEYWORDS):
        #     logger.info(f"[#archive]跳过文件名包含关键词的压缩包: {archive_path}")
        #     return (archive_path, True, "name")
        
        # 4. 如果跳过格式列表为空，则不跳过任何文件
        if not ACTIVE_SKIP_FORMATS:
            logger.info(f"[#archive]跳过格式列表为空，不跳过任何文件: {archive_path}")
            return (archive_path, False, "")
            
        # 5. 快速检查压缩包内容是否包含跳过格式
        try:
            with zipfile.ZipFile(archive_path, 'r') as zip_ref:
                # 只获取前10个文件样本，避免处理大型压缩包
                sample_files = [f.filename for f in zip_ref.infolist()[:10]]
                
                # 检查样本文件中是否包含需要跳过的格式
                if any(f.lower().endswith(tuple(ACTIVE_SKIP_FORMATS)) for f in sample_files):
                    return (archive_path, True, "content")
        except (zipfile.BadZipFile, PermissionError, IOError) as e:
            logger.warning(f"[#archive]读取压缩包时出错 (跳过内容检查): {archive_path}, Error: {e}")
            # 如果压缩包损坏或无法读取，不跳过，让后续验证阶段决定
            return (archive_path, False, "")
            
        # 通过所有检查，不需要跳过
        return (archive_path, False, "")
        
    except Exception as e:
        logger.error(f"[#archive]检查压缩包跳过状态时发生意外错误: {archive_path}, Error: {e}")
        # 如果发生错误，不跳过，让后续验证阶段决定
        return (archive_path, False, "")

def load_blacklist(file_path: Path) -> Set[str]:
    """加载JSON格式的黑名单文件"""
    if not file_path.exists():
        logger.info(f"[#file]黑名单文件不存在: {file_path}")
        return set()
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            blacklist_data = json.load(f)
            if isinstance(blacklist_data, list):
                # 确保路径是绝对路径且规范化
                return {str(Path(p).resolve()) for p in blacklist_data}
            else:
                logger.warning(f"[#file]黑名单文件格式错误，应为列表: {file_path}")
                return set()
    except json.JSONDecodeError:
        logger.error(f"[#file]解析黑名单JSON文件失败: {file_path}")
        return set()
    except Exception as e:
        logger.error(f"[#file]加载黑名单文件时出错: {file_path}, 错误: {e}")
        return set()


@infinite_monitor()
def monitor_and_process(paths: List[str], **kwargs) -> None:
    """监控指定路径并处理压缩包
    
    Args:
        paths: 需要监控的路径列表
        **kwargs: 其他参数
    """
    # 获取所有支持的压缩包文件路径
    archive_paths = InputHandler.get_all_file_paths(set(paths), SUPPORTED_ARCHIVE_FORMATS)
    if not archive_paths:
        logger.info("[#file]未找到支持的压缩包文件")
        return

    # 加载JSON黑名单
    json_blacklist = load_blacklist(BLACKLIST_FILE_PATH)
    if json_blacklist:
        logger.info(f"[#file]已加载JSON黑名单，共 {len(json_blacklist)} 条记录")

    # 添加快速预扫描，检查并过滤需要跳过的压缩包
    logger.info(f"[#file]开始预扫描，共找到 {len(archive_paths)} 个压缩包")
    logger.info(f"[#file]当前跳过格式: {', '.join(ACTIVE_SKIP_FORMATS)}")
    logger.info(f"[#file]当前命令行黑名单路径关键词: {', '.join(ACTIVE_BLACKLIST_PATHS)}")
    # logger.info(f"[#file]跳过关键词: {', '.join(SKIP_KEYWORDS)}")
    
    # 获取线程数
    thread_count, _, _ = get_performance_params()
    # thread_count = 16 # 可以取消注释以固定线程数
    logger.info(f"[#performance]预扫描使用 {thread_count} 个线程")
    
    # 多线程预扫描
    filtered_archive_paths = []
    skipped_by_name = 0
    skipped_by_content = 0
    skipped_by_keyword_blacklist = 0
    skipped_by_json_blacklist = 0 # 新增计数器
    
    # 使用 partial 将 json_blacklist 固定为 check_archive_skip 的参数
    check_func = partial(check_archive_skip, json_blacklist=json_blacklist)
    
    with ThreadPoolExecutor(max_workers=thread_count) as executor:
        # 并行执行检查
        results = list(executor.map(check_func, archive_paths))
        
        # 处理结果
        for path, should_skip, skip_reason in results:
            if should_skip:
                if skip_reason == "name":
                    logger.info(f"[#archive]跳过文件名指示已处理的压缩包: {path}")
                    skipped_by_name += 1
                elif skip_reason == "content":
                    logger.info(f"[#archive]跳过内容含跳过格式的压缩包: {path}")
                    skipped_by_content += 1
                elif skip_reason == "keyword_blacklist": # 修改原因标识
                    logger.info(f"[#archive]跳过命令行黑名单路径的压缩包: {path}")
                    skipped_by_keyword_blacklist += 1
                elif skip_reason == "json_blacklist": # 新增原因处理
                    logger.info(f"[#archive]跳过JSON黑名单中的压缩包: {path}")
                    skipped_by_json_blacklist += 1
            else:
                filtered_archive_paths.append(path)
    
    # 在处理结果后对过滤后的列表进行排序
    filtered_archive_paths.sort()
    logger.info(f"[#file]已对过滤后的压缩包路径进行升序排序")

    skipped_total = skipped_by_name + skipped_by_content + skipped_by_keyword_blacklist + skipped_by_json_blacklist # 更新总数
    logger.info(f"[#status]预扫描完成，共跳过 {skipped_total} 个压缩包：")
    logger.info(f"[#status]- 通过文件名跳过：{skipped_by_name} 个")
    logger.info(f"[#status]- 通过内容检查跳过：{skipped_by_content} 个")
    logger.info(f"[#status]- 通过命令行黑名单路径跳过：{skipped_by_keyword_blacklist} 个")
    logger.info(f"[#status]- 通过JSON黑名单文件跳过：{skipped_by_json_blacklist} 个") # 新增日志
    logger.info(f"[#status]剩余待处理（已排序）：{len(filtered_archive_paths)} 个压缩包") # 更新日志说明已排序
    
    # 根据模式处理过滤后的文件
    if filtered_archive_paths:
        # 确保将 archive_path 传递给 process_archives
        process_archives(filtered_archive_paths, **kwargs)
    else:
        logger.info("[#status]没有需要处理的压缩包文件")

def process_with_args(args):
    """处理命令行参数的逻辑"""
    # 处理跳过格式的参数
    global ACTIVE_SKIP_FORMATS, ACTIVE_BLACKLIST_PATHS
    
    if args.skip is not None:
        # 如果提供了跳过格式参数
        if args.skip.strip() == "":
            # 如果是空字符串，则禁用所有跳过
            ACTIVE_SKIP_FORMATS = set()
            logger.info("[#file]已禁用所有跳过格式")
        else:
            # 否则，解析逗号分隔的格式列表
            formats = args.skip.strip().split(',')
            ACTIVE_SKIP_FORMATS = {fmt.strip() for fmt in formats if fmt.strip()}
            logger.info(f"[#file]已设置自定义跳过格式: {', '.join(ACTIVE_SKIP_FORMATS)}")
    else:
        # 使用默认设置
        ACTIVE_SKIP_FORMATS = SKIP_FORMATS.copy()
    
    # 处理黑名单路径关键词的参数
    if args.blacklist is not None:
        # 如果提供了黑名单路径关键词参数
        if args.blacklist.strip() == "":
            # 如果是空字符串，则禁用所有黑名单
            ACTIVE_BLACKLIST_PATHS = set()
            logger.info("[#file]已禁用所有黑名单路径关键词")
        else:
            # 否则，解析逗号分隔的关键词列表
            keywords = args.blacklist.strip().split(',')
            ACTIVE_BLACKLIST_PATHS = {kw.strip() for kw in keywords if kw.strip()}
            logger.info(f"[#file]已设置自定义黑名单路径关键词: {', '.join(ACTIVE_BLACKLIST_PATHS)}")
    else:
        # 使用默认设置
        ACTIVE_BLACKLIST_PATHS = BLACKLIST_PATHS.copy()
    
    # 构建过滤参数
    filter_params = {
        'min_width': args.min_width,
        'format': args.format,
        'quality': args.quality
    }

    # 使用InputHandler获取输入路径
    start_config_gui_thread()

    paths = InputHandler.get_input_paths(
        cli_paths=args.paths if args.paths else None,
        use_clipboard=args.clipboard,  # 使用命令行参数控制是否使用剪贴板
        allow_manual=True
    )
    if not paths:
        logger.info("[#file]未提供有效的压缩包路径")
        return

    init_layout()
    
    # 构建参数
    kwargs = {
        'filter_params': filter_params,
        'interval_minutes': args.interval if args.infinite else -1,
        'directories': paths,  # 添加目录列表
        'format': args.format,
        'quality': args.quality,
        'lossless': args.lossless,  # 添加无损选项
        **filter_params
    }
    
    # 调用监控和处理函数，由装饰器控制是否为无限模式
    monitor_and_process(paths, **kwargs)

def main():
    
# 将 parser 定义修改为如下
    parser = argparse.ArgumentParser(description='压缩包图片处理工具')
    parser.add_argument('--min-width', type=int, default=0,
                    help='最小图片宽度(像素)，低于此宽度的压缩包将被跳过')
    parser.add_argument('--infinite', '-inf', action='store_true',
                    help='启用无限循环监控模式')
    parser.add_argument('--interval', type=int, default=10,
                    help='监控模式的检查间隔(分钟)')
    parser.add_argument('--format', '-f', type=str, default='avif', 
                    choices=['avif', 'webp', 'jxl', 'jpg', 'png'],
                    help='目标格式 (默认: avif)')
    parser.add_argument('--quality', '-q', type=int, default=90,
                    help='压缩质量 1-100 (默认: 90)')
    parser.add_argument('--clipboard', '-c', action='store_true',
                    help='从剪贴板读取路径')
    parser.add_argument('--lossless', '-l', action='store_true',
                    help='启用无损模式')
    parser.add_argument('paths', nargs='*', help='压缩包路径')
    parser.add_argument('--no-run', '-nr', action='store_true',
                    help='只显示配置界面，不执行转换')
    # 添加命令行参数用于覆盖跳过格式
    parser.add_argument('--skip', type=str, 
                    help='覆盖跳过格式，格式为逗号分隔的后缀名列表，例如：.avif,.jxl,.webp；设置为空字符串可禁用跳过')
    # 添加命令行参数用于覆盖黑名单路径关键词
    parser.add_argument('--blacklist', '-b', type=str, 
                    help='覆盖黑名单路径关键词，格式为逗号分隔的关键词列表，例如：backup,temp,downloads；设置为空字符串可禁用黑名单')
    
    # 使用命令行参数或TUI配置界面
    if len(sys.argv) > 1:
        args = parser.parse_args()
        process_with_args(args)
    else:
        # 定义复选框选项

        # 预设配置
        preset_configs = {
            "AVIF-80-inf": {
                "description": "AVIF格式 90质量 无限模式",
                "checkbox_options": ["infinite","clipboard",],
                "input_values": {
                    "format": "avif",
                    "quality": "80",
                    "interval": "10",
                }
            },
            "AVIF-skip-jxl": {
                "description": "AVIF格式 80质量 仅跳过JXL",
                "checkbox_options": ["clipboard"],
                "input_values": {
                    "format": "avif",
                    "quality": "80",
                    "skip": ".jxl,.webp",
                    "blacklist": "02COS",
                }
            },
            "JXL-lossless": {  # 添加新的预设
                "description": "JXL格式 CJXL无损转换",
                "checkbox_options": ["clipboard","lossless"],  # 启用JPEG无损
                "input_values": {
                    "format": "jxl",
                    "quality": "100",
                    
                }
            },
            "JXL-80": {
                "description": "JXL格式 80质量",
                "checkbox_options": ["clipboard"],
                "input_values": {
                    "format": "jxl",
                    "quality": "80",
                }
            },
            "AVIF-90-1800": {
                "description": "AVIF格式 90质量 1800宽度过滤",
                "checkbox_options": ["clipboard"],
                "input_values": {
                    "format": "avif",
                    "quality": "80",
                    "min_width": "1800"
                }
            },
            # ...preset definitions...  # 其他预设配置相同，为简洁起见省略
        }

        def on_run(params: dict):
            """TUI配置界面的回调函数"""
            # 将TUI参数转换为命令行参数格式
            sys.argv = [sys.argv[0]]
            
            # 添加选中的复选框选项
            for arg, enabled in params['options'].items():
                if enabled:
                    sys.argv.append(arg)
                    
            # 添加输入框的值
            for arg, value in params['inputs'].items():
                if value.strip():
                    sys.argv.append(arg)
                    sys.argv.append(value)
            
            # 使用全局的 parser 解析参数
            args = parser.parse_args()
            process_with_args(args)

        # 创建并运行配置界面
        # Check if --no-run flag is in the arguments
        no_run = "--no-run" in sys.argv or "-nr" in sys.argv
        app = create_config_app(
            program=__file__,
            parser=parser,
            title="图片压缩配置",
            preset_configs=preset_configs,
            on_run=False,
            rich_mode=USE_RICH,
            # if no_run else on_run,
        )
        if not USE_RICH:
            app.run()
        else:
            args = app.args  
            process_with_args(args)
if __name__ == '__main__':
    main()
