# irm https://raw.githubusercontent.com/yuaotian/go-cursor-help/master/scripts/install.ps1 | iex
import tkinter as tk
from tkinter import ttk
import ttkbootstrap as ttk
from ttkbootstrap.constants import *
import threading
import time
from datetime import datetime, timedelta
import json
import os
import portalocker  # 替换fcntl
import gc  # 导入gc模块
try:
    from pynput import mouse
except ImportError:
    print("警告：未找到 'pynput' 库。自动模式将不可用。")
    print("请运行 'pip install pynput' 来安装。")
    mouse = None
IDLE_THRESHOLD_SECONDS = 100  # 设置闲置阈值为5秒 
ACTIVE_THREAD_COUNT = 2  # 活动状态下的线程数
IDLE_THREAD_COUNT = 16  # 闲置状态下的线程数
# 性能配置
# 可以直接修改这个文件来实时调整性能
# 修改后会立即生效，无需重启程序

# 全局配置路径
CONFIG_FILE = os.path.join(os.path.dirname(__file__), 'performance_config.json')

DEFAULT_CONFIG = {
    "thread_count": 1,
    "batch_size": 1,
    "start_time": datetime.now().isoformat(),  # 添加启动时间戳
    "paused": False  # 添加暂停状态标志
}

def get_config():
    """获取整个配置文件内容"""
    try:
        with open(CONFIG_FILE, 'r+', encoding='utf-8') as f:
            portalocker.lock(f, portalocker.LOCK_SH)  # 共享锁
            try:
                config = json.load(f)
                # 添加自动清理
                cleanup_old_configs(config)
                return config
            except json.JSONDecodeError:
                return {}
            finally:
                portalocker.unlock(f)
    except FileNotFoundError:
        return {}

def get_thread_count():
    """获取当前进程的线程数"""
    pid = os.getpid()
    config = get_config()
    # 如果处于暂停状态，返回0表示没有可用线程
    if is_paused():
        return 0
    return max(1, min(config.get(str(pid), DEFAULT_CONFIG)['thread_count'], 16))

def get_batch_size():
    """获取当前进程的批处理大小"""
    pid = os.getpid()
    config = get_config()
    return max(1, min(config.get(str(pid), DEFAULT_CONFIG)['batch_size'], 100))

def is_paused():
    """检查当前进程是否处于暂停状态"""
    pid = os.getpid()
    config = get_config()
    return config.get(str(pid), DEFAULT_CONFIG).get('paused', False)

def set_paused(paused=True):
    """设置当前进程的暂停状态"""
    pid = os.getpid()
    with open(CONFIG_FILE, 'a+', encoding='utf-8') as f:
        portalocker.lock(f, portalocker.LOCK_EX)  # 排他锁
        try:
            f.seek(0)
            content = f.read()
            config = json.loads(content) if content else {}
            if str(pid) not in config:
                config[str(pid)] = DEFAULT_CONFIG
            config[str(pid)]['paused'] = paused
            f.seek(0)
            f.truncate()
            json.dump(config, f, indent=2)
        except json.JSONDecodeError:
            config = {str(pid): {**DEFAULT_CONFIG, 'paused': paused}}
            json.dump(config, f, indent=2)
        finally:
            portalocker.unlock(f)

def wait_for_resume(check_interval=0.5, timeout=None):
    """
    等待直到恢复处理或超时
    
    参数:
    check_interval: 检查间隔时间（秒）
    timeout: 超时时间（秒），None表示无限等待
    
    返回:
    True: 如果已恢复
    False: 如果超时
    """
    start_time = time.time()
    while is_paused():
        time.sleep(check_interval)
        if timeout and (time.time() - start_time > timeout):
            return False
    return True


class ConfigGUI:
    def __init__(self):
        self.pid = os.getpid()
        # 初始化当前进程配置
        self._init_config()
        
        # 获取当前时间戳
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        self.root = ttk.Window(
            title=f"性能配置调整器 [{current_time}]",
            themename="cosmo",
            resizable=(True, True)
        )
        self.root.minsize(300, 200)  # 调整最小尺寸
        self.root.geometry("800x500")  # 调整初始尺寸
        
        # 添加窗口关闭事件处理
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        
        # 创建主框架
        self.main_frame = ttk.Frame(self.root)
        self.main_frame.pack(fill=BOTH, expand=YES, padx=20, pady=20)
        
        # 调整grid布局的行数
        self.main_frame.grid_columnconfigure(0, weight=1)
        for i in range(6): # 增加一行用于自动模式和状态
            self.main_frame.grid_rowconfigure(i, weight=1)
        
        # 标题
        title_label = ttk.Label(
            self.main_frame,
            text="性能参数实时调整",
            font=("Helvetica", 16, "bold")
        )
        title_label.grid(row=0, column=0, pady=10, sticky="ew")
        
        # 线程数调整
        thread_frame = ttk.LabelFrame(
            self.main_frame,
            text="线程数 (1-16)",
            padding="10"
        )
        thread_frame.grid(row=1, column=0, sticky="nsew", pady=5)
        thread_frame.grid_columnconfigure(0, weight=1)
        
        self.thread_var = tk.IntVar(value=get_thread_count())
        self.thread_slider = ttk.Scale(
            thread_frame,
            from_=1,
            to=16,
            variable=self.thread_var,
            command=self.update_thread_count
        )
        self.thread_slider.grid(row=0, column=0, sticky="ew", padx=5)
        
        self.thread_label = ttk.Label(
            thread_frame,
            text=f"当前值: {self.thread_var.get()}"
        )
        self.thread_label.grid(row=1, column=0, pady=(5,0))
        
        # 批处理大小调整
        batch_frame = ttk.LabelFrame(
            self.main_frame,
            text="批处理大小 (1-100)",
            padding="10"
        )
        batch_frame.grid(row=2, column=0, sticky="nsew", pady=5)
        batch_frame.grid_columnconfigure(0, weight=1)
        
        self.batch_var = tk.IntVar(value=get_batch_size())
        self.batch_slider = ttk.Scale(
            batch_frame,
            from_=1,
            to=100,
            variable=self.batch_var,
            command=self.update_batch_size
        )
        self.batch_slider.grid(row=0, column=0, sticky="ew", padx=5)
        
        self.batch_label = ttk.Label(
            batch_frame,
            text=f"当前值: {self.batch_var.get()}"
        )
        self.batch_label.grid(row=1, column=0, pady=(5,0))

        # 添加预设模式按钮框架 (移到 row 3)
        preset_frame = ttk.Frame(self.main_frame)
        preset_frame.grid(row=3, column=0, sticky="nsew", pady=10)
        
        # 三个预设按钮
        ttk.Button(
            preset_frame,
            text="低配模式",
            command=lambda: self.set_preset(1, 1),
            bootstyle="secondary"
        ).pack(side=LEFT, expand=YES, padx=5)
        
        ttk.Button(
            preset_frame,
            text="中配模式",
            command=lambda: self.set_preset(8, 8),
            bootstyle="info"
        ).pack(side=LEFT, expand=YES, padx=5)
        
        ttk.Button(
            preset_frame,
            text="高配模式",
            command=lambda: self.set_preset(16, 16),
            bootstyle="primary"
        ).pack(side=LEFT, expand=YES, padx=5)
        
        # 添加控制按钮框架 (移到 row 4)
        control_frame = ttk.Frame(self.main_frame)
        control_frame.grid(row=4, column=0, sticky="nsew", pady=10)
        
        # 初始化暂停状态
        self.paused = is_paused()
        
        # 暂停/恢复按钮 (放入 control_frame)
        self.pause_button = ttk.Button(
            control_frame,
            text="暂停处理" if not self.paused else "恢复处理",
            command=self.toggle_pause,
            bootstyle="warning" if not self.paused else "success"
        )
        self.pause_button.pack(side=LEFT, expand=YES, padx=5)

        # 自动模式按钮 (放入 control_frame)
        self.auto_mode_button = ttk.Button(
            control_frame,
            text="启用自动模式",
            command=self.toggle_auto_mode,
            bootstyle="info" # 初始样式
        )
        if mouse is None: # 如果 pynput 未安装则禁用
            self.auto_mode_button.config(state=DISABLED, text="自动模式(需pynput)")
        self.auto_mode_button.pack(side=LEFT, expand=YES, padx=5)
        
        # 状态标签 (移到 row 5)
        self.status_label = ttk.Label(
            self.main_frame,
            text="✓ 配置已同步",
            bootstyle="success"
        )
        self.status_label.grid(row=5, column=0, pady=10, sticky="ew") # 原 row 5 改为 row 6
        
        # 启动自动保存线程
        self.save_thread = threading.Thread(target=self.auto_save, daemon=True)
        self.save_thread.start()

        # 添加自动模式相关状态
        self.auto_mode_enabled = False
        self.last_mouse_move_time = time.time()
        self.mouse_listener = None
        self.idle_check_timer = None
        self.is_currently_idle = False # 跟踪当前是否处于闲置调整状态
        self.countdown_timer_id = None # 添加倒计时定时器ID
    
    def _init_config(self):
        """初始化当前进程配置"""
        config = get_config()
        if str(self.pid) not in config:
            self._update_config(DEFAULT_CONFIG)

    def _update_config(self, new_values):
        """更新当前进程配置"""
        with open(CONFIG_FILE, 'a+', encoding='utf-8') as f:
            portalocker.lock(f, portalocker.LOCK_EX)  # 排他锁
            try:
                f.seek(0)
                content = f.read()
                config = json.loads(content) if content else {}
                # 添加清理逻辑
                cleanup_old_configs(config)
                config[str(self.pid)] = {
                    **config.get(str(self.pid), DEFAULT_CONFIG),
                    **new_values
                }
                f.seek(0)
                f.truncate()
                json.dump(config, f, indent=2)
            except json.JSONDecodeError:
                config = {str(self.pid): DEFAULT_CONFIG}
                json.dump(config, f, indent=2)
            finally:
                portalocker.unlock(f)

    def update_thread_count(self, *args):
        # 只有在非自动模式下，滑块调整才直接更新标签和触发保存状态
        if not self.auto_mode_enabled:
            self.thread_label.config(text=f"当前值: {self.thread_var.get()}")
            self.show_saving_status()
        # 在自动模式下，标签由 check_idle_status 或 on_move 更新
        
    def update_batch_size(self, *args):
        self.batch_label.config(text=f"当前值: {self.batch_var.get()}")
        self.show_saving_status()
    
    def show_saving_status(self):
        self.status_label.config(text="⟳ 正在保存...", bootstyle="warning")
        
    def save_config(self):
        """保存当前进程配置"""
        self._update_config({
            "thread_count": self.thread_var.get(),
            "batch_size": self.batch_var.get(),
            "paused": self.paused
        })
        self.status_label.config(text="✓ 配置已同步", bootstyle="success")
    
    def auto_save(self):
        """自动保存配置的后台线程"""
        last_saved_threads = -1
        last_saved_batch = -1
        last_saved_paused = None

        while True:
            time.sleep(0.5)  # 延迟保存，避免频繁写入
            current_threads = self.thread_var.get()
            current_batch = self.batch_var.get()
            current_paused = self.paused

            # 仅当值发生变化时才保存
            if (current_threads != last_saved_threads or
                    current_batch != last_saved_batch or
                    current_paused != last_saved_paused):
                self.save_config()
                last_saved_threads = current_threads
                last_saved_batch = current_batch
                last_saved_paused = current_paused
    
    def set_preset(self, threads, batch_size):
        """设置预设配置"""
        self.thread_var.set(threads)
        self.batch_var.set(batch_size)
        self.thread_label.config(text=f"当前值: {threads}")
        self.batch_label.config(text=f"当前值: {batch_size}")
        self.show_saving_status()
    
    def toggle_pause(self):
        """切换暂停/恢复状态"""
        self.paused = not self.paused
        set_paused(self.paused)
        
        if self.paused:
            self.pause_button.config(text="恢复处理", bootstyle="success")
            self.status_label.config(text="⏸ 处理已暂停", bootstyle="warning")
        else:
            self.pause_button.config(text="暂停处理", bootstyle="warning")
            self.status_label.config(text="▶ 处理已恢复", bootstyle="success")
        
        # 确保状态标签在自动模式提示时不被覆盖太快
        if not self.auto_mode_enabled:
            self.root.after(2000, lambda: self.status_label.config(text="✓ 配置已同步", bootstyle="success"))
        else:
             self.root.after(2000, self.update_status_label_for_auto)
    
    def run(self):
        self.root.mainloop()

    def on_close(self):
        """处理窗口关闭事件"""
        # 可以在这里添加任何必要的清理逻辑
        print("关闭性能配置窗口...") # 添加日志或调试信息
        self.stop_mouse_listener() # 停止监听器
        if self.idle_check_timer: # 取消定时器
            self.root.after_cancel(self.idle_check_timer)
            self.idle_check_timer = None
        if self.countdown_timer_id: # 取消倒计时
            self.root.after_cancel(self.countdown_timer_id)
            self.countdown_timer_id = None
        self.root.destroy() # 显式销毁窗口及其所有子部件

    # --- 自动模式相关方法 ---

    def toggle_auto_mode(self):
        """切换自动模式的启用/禁用状态"""
        if mouse is None:
            self.status_label.config(text="❌ 未安装 pynput，无法启用自动模式", bootstyle="danger")
            return

        self.auto_mode_enabled = not self.auto_mode_enabled
        if self.auto_mode_enabled:
            self.auto_mode_button.config(text="禁用自动模式", bootstyle="success")
            self.thread_slider.config(state=DISABLED) # 禁用滑块
            # self.status_label.config(text="⚙️ 自动模式已启用", bootstyle="info") # 状态由倒计时更新
            self.start_mouse_listener()
            self.last_mouse_move_time = time.time() # 重置计时器
            self.is_currently_idle = False # 初始状态为活动
            self.thread_var.set(ACTIVE_THREAD_COUNT) # 设置为活动线程数
            self.update_thread_label_auto()
            self.check_idle_status() # 立即检查一次状态并启动倒计时
        else:
            self.auto_mode_button.config(text="启用自动模式", bootstyle="info")
            self.thread_slider.config(state=NORMAL) # 启用滑块
            self.status_label.config(text="✓ 配置已同步", bootstyle="success")
            self.stop_mouse_listener()
            if self.idle_check_timer:
                self.root.after_cancel(self.idle_check_timer)
                self.idle_check_timer = None
            if self.countdown_timer_id: # 取消倒计时
                self.root.after_cancel(self.countdown_timer_id)
                self.countdown_timer_id = None
            # 禁用自动模式时，可以选择恢复滑块的值或保持当前值
            # 当前保持自动模式最后设置的值
            self.thread_label.config(text=f"当前值: {self.thread_var.get()}")

    def on_mouse_move(self, x, y):
        """鼠标移动事件回调"""
        self.last_mouse_move_time = time.time()
        # print(f"Mouse moved at {self.last_mouse_move_time}") # 调试用

        if self.auto_mode_enabled:
            if self.is_currently_idle:
                # print("Activity detected, switching to ACTIVE threads.") # 调试用
                self.is_currently_idle = False
                self.thread_var.set(ACTIVE_THREAD_COUNT)
                self.update_thread_label_auto() # 更新UI
                self.show_saving_status() # 显示保存状态
                # 从闲置变为活动，立即开始倒计时检查
                if self.idle_check_timer:
                    self.root.after_cancel(self.idle_check_timer)
                if self.countdown_timer_id:
                    self.root.after_cancel(self.countdown_timer_id)
                self.check_idle_status() # 重新开始检查和倒计时

            # 只要有移动，就重置下一次闲置检查和倒计时
            if self.idle_check_timer:
                self.root.after_cancel(self.idle_check_timer)
            if self.countdown_timer_id:
                self.root.after_cancel(self.countdown_timer_id)
            self.idle_check_timer = self.root.after(int(IDLE_THRESHOLD_SECONDS * 1000), self.check_idle_status)
            self.update_countdown_label() # 开始或更新倒计时显示


    def check_idle_status(self):
        """检查是否达到闲置阈值，并管理倒计时"""
        if not self.auto_mode_enabled:
            return

        idle_time = time.time() - self.last_mouse_move_time
        # print(f"Checking idle status. Idle time: {idle_time:.2f}s") # 调试用

        if idle_time >= IDLE_THRESHOLD_SECONDS:
            if not self.is_currently_idle:
                # print(f"Idle threshold reached ({IDLE_THRESHOLD_SECONDS}s), switching to IDLE threads.") # 调试用
                self.is_currently_idle = True
                self.thread_var.set(IDLE_THREAD_COUNT)
                self.update_thread_label_auto() # 更新UI
                self.show_saving_status() # 显示保存状态
                # 进入闲置状态，停止倒计时
                if self.countdown_timer_id:
                    self.root.after_cancel(self.countdown_timer_id)
                    self.countdown_timer_id = None
                self.status_label.config(text=f"⚙️ 自动模式: 闲置 ({IDLE_THREAD_COUNT}线程)", bootstyle="info")
            # 到达闲置状态后，不再主动安排下一次检查或倒计时，等待鼠标移动触发 on_mouse_move
        else:
            # 如果当前不是闲置状态（即活动状态），确保线程数是活动值
            if not self.is_currently_idle:
                 if self.thread_var.get() != ACTIVE_THREAD_COUNT:
                    # print("Ensuring ACTIVE thread count.") # 调试用
                    self.thread_var.set(ACTIVE_THREAD_COUNT)
                    self.update_thread_label_auto()
                    self.show_saving_status()

            # 未达到阈值，安排下一次检查
            remaining_time_for_check = IDLE_THRESHOLD_SECONDS - idle_time
            self.idle_check_timer = self.root.after(int(remaining_time_for_check * 1000) + 100, self.check_idle_status) # 加一点延迟避免过于频繁

            # 同时，启动或继续更新倒计时标签
            self.update_countdown_label()

    def update_countdown_label(self):
        """每秒更新状态标签以显示倒计时"""
        if not self.auto_mode_enabled or self.is_currently_idle:
            if self.countdown_timer_id: # 如果进入闲置或禁用模式，取消现有计时器
                self.root.after_cancel(self.countdown_timer_id)
                self.countdown_timer_id = None
            return # 如果不在自动模式或已闲置，则不更新倒计时

        idle_time = time.time() - self.last_mouse_move_time
        remaining_time = max(0, IDLE_THRESHOLD_SECONDS - idle_time)

        if remaining_time > 0:
            self.status_label.config(
                text=f"⚙️ 自动模式: 活动 ({ACTIVE_THREAD_COUNT}线程) - {int(remaining_time)}s 后闲置",
                bootstyle="info"
            )
            # 安排下一次更新
            self.countdown_timer_id = self.root.after(1000, self.update_countdown_label)
        else:
            # 时间到了，理论上 check_idle_status 会处理状态切换
            # 但为保险起见，这里也更新一下标签
            if not self.is_currently_idle: # 避免在 check_idle_status 切换后又被这里覆盖
                 self.status_label.config(text=f"⚙️ 自动模式: 即将切换到闲置...", bootstyle="info")
            self.countdown_timer_id = None # 倒计时结束

    def update_thread_label_auto(self):
        """在自动模式下更新线程标签"""
        if self.auto_mode_enabled:
            mode = "闲置" if self.is_currently_idle else "活动"
            self.thread_label.config(text=f"当前值: {self.thread_var.get()} ({mode})")

    def update_status_label_for_auto(self):
        """更新状态标签，优先显示自动模式状态或倒计时"""
        if self.auto_mode_enabled:
             if self.is_currently_idle:
                 self.status_label.config(text=f"⚙️ 自动模式: 闲置 ({IDLE_THREAD_COUNT}线程)", bootstyle="info")
             else:
                 # 活动状态下，由 update_countdown_label 更新
                 self.update_countdown_label()
        else:
             self.status_label.config(text="✓ 配置已同步", bootstyle="success")


    def start_mouse_listener(self):
        """启动鼠标监听器"""
        if self.mouse_listener is None and mouse:
            try:
                # 使用非阻塞监听器，并在单独线程中运行以避免阻塞GUI
                self.mouse_listener = mouse.Listener(on_move=self.on_mouse_move)
                # daemon=True 确保主程序退出时线程也退出
                listener_thread = threading.Thread(target=self.mouse_listener.start, daemon=True)
                listener_thread.start()
                print("鼠标监听器已启动。")
            except Exception as e:
                print(f"启动鼠标监听器失败: {e}")
                self.status_label.config(text=f"❌ 启动监听器失败: {e}", bootstyle="danger")
                self.mouse_listener = None # 重置以允许重试

    def stop_mouse_listener(self):
        """停止鼠标监听器"""
        if self.mouse_listener:
            try:
                self.mouse_listener.stop()
                self.mouse_listener = None
                print("鼠标监听器已停止。")
            except Exception as e:
                 print(f"停止鼠标监听器时出错: {e}")
        # 停止监听时也取消倒计时
        if self.countdown_timer_id:
            self.root.after_cancel(self.countdown_timer_id)
            self.countdown_timer_id = None


def cleanup_old_configs(config):
    """清理超过24小时的非活跃配置"""
    now = datetime.now()
    expired_pids = []
    
    for pid_str in list(config.keys()):
        try:
            # 仅通过时间戳判断，避免进程检查的兼容性问题
            start_time = datetime.fromisoformat(config[pid_str].get('start_time', now.isoformat()))
            if (now - start_time) > timedelta(hours=6):
                expired_pids.append(pid_str)
        except Exception:
            continue
    
    # 删除过期配置
    for pid in expired_pids:
        del config[pid]

def start_config_gui_thread():
    """启动配置 GUI 线程"""
    config_gui_thread = threading.Thread(target=lambda: ConfigGUI().run(), daemon=True)
    config_gui_thread.start()

def performance_controlled(func):
    """
    装饰器：为函数添加性能控制功能
    
    使用示例:
    @performance_controlled
    def process_images(images_list, **kwargs):
        threads = kwargs.get('thread_count', 1)
        batch = kwargs.get('batch_size', 1)
        # 处理逻辑...
    """
    import functools
    
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        # 检查是否处于暂停状态
        while is_paused():
            wait_for_resume(check_interval=0.5, timeout=1)
            
        # 注入性能参数
        if 'thread_count' not in kwargs:
            kwargs['thread_count'] = get_thread_count()
        if 'batch_size' not in kwargs:
            kwargs['batch_size'] = get_batch_size()
            
        # 执行原函数
        return func(*args, **kwargs)
    
    return wrapper


class PerformanceContext:
    """
    性能控制上下文管理器
    
    使用示例:
    with PerformanceContext() as perf:
        if perf.thread_count > 0:
            # 使用perf.thread_count和perf.batch_size处理任务
            ...
            # 在循环中检查暂停
            if perf.is_paused():
                perf.wait_for_resume()
    """
    
    def __init__(self):
        self._update_params()
        
    def __enter__(self):
        self._update_params()
        return self
        
    def __exit__(self, exc_type, exc_val, exc_tb):
        # 显式触发垃圾回收以释放内存
        gc.collect()
    
    def _update_params(self):
        """更新性能参数"""
        self.thread_count = get_thread_count()
        self.batch_size = get_batch_size()
        self._paused = is_paused()
    
    def is_paused(self):
        """检查是否已暂停"""
        self._paused = is_paused()
        return self._paused
    
    def wait_for_resume(self, check_interval=0.5, timeout=None):
        """等待直到恢复或超时"""
        if self.is_paused():
            return wait_for_resume(check_interval=check_interval, timeout=timeout)
        return True
    
    def get_params(self):
        """获取当前性能参数"""
        self._update_params()
        return {
            'thread_count': self.thread_count,
            'batch_size': self.batch_size,
            'paused': self._paused
        }


def get_performance_params():
    """
    获取当前性能参数 - 简单的一行式使用
    
    使用示例:
    thread_count, batch_size, is_pause_state = get_performance_params()
    
    """
    return get_thread_count(), get_batch_size(), is_paused()

if __name__ == "__main__":
    app = ConfigGUI()
    app.run()
