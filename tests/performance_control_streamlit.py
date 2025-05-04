import streamlit as st
import json
import os
import portalocker
from datetime import datetime, timedelta
import time
import threading
import sys

# 全局配置路径
CONFIG_FILE = os.path.join(os.path.dirname(__file__), 'performance_config.json')

DEFAULT_CONFIG = {
    "thread_count": 1,
    "batch_size": 1,
    "start_time": datetime.now().isoformat(),
    "paused": False
}

def get_config():
    """获取整个配置文件内容"""
    try:
        with open(CONFIG_FILE, 'r+', encoding='utf-8') as f:
            portalocker.lock(f, portalocker.LOCK_SH)
            try:
                config = json.load(f)
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
        portalocker.lock(f, portalocker.LOCK_EX)
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

def update_config(thread_count, batch_size, paused):
    """更新当前进程配置"""
    pid = os.getpid()
    with open(CONFIG_FILE, 'a+', encoding='utf-8') as f:
        portalocker.lock(f, portalocker.LOCK_EX)
        try:
            f.seek(0)
            content = f.read()
            config = json.loads(content) if content else {}
            config[str(pid)] = {
                **config.get(str(pid), DEFAULT_CONFIG),
                "thread_count": thread_count,
                "batch_size": batch_size,
                "paused": paused
            }
            f.seek(0)
            f.truncate()
            json.dump(config, f, indent=2)
        except json.JSONDecodeError:
            config = {str(pid): DEFAULT_CONFIG}
            json.dump(config, f, indent=2)
        finally:
            portalocker.unlock(f)

def cleanup_old_configs(config):
    """清理超过6小时的非活跃配置"""
    now = datetime.now()
    expired_pids = []
    
    for pid_str in list(config.keys()):
        try:
            start_time = datetime.fromisoformat(config[pid_str].get('start_time', now.isoformat()))
            if (now - start_time) > timedelta(hours=6):
                expired_pids.append(pid_str)
        except Exception:
            continue
    
    for pid in expired_pids:
        del config[pid]

def create_performance_tab():
    """创建性能控制标签页"""
    st.title("性能参数实时调整")
    
    # 获取当前进程ID和时间戳
    pid = os.getpid()
    timestamp = datetime.now().strftime("%H%M%S")
    tab_name = f"进程 {pid} - {timestamp}"
    
    # 创建标签页
    tab1, tab2, tab3 = st.tabs(["性能控制", "预设模式", "状态信息"])
    
    with tab1:
        # 线程数调整
        thread_count = st.slider(
            "线程数 (1-16)",
            min_value=1,
            max_value=16,
            value=get_thread_count(),
            key=f"thread_{pid}_{timestamp}"
        )
        
        # 批处理大小调整
        batch_size = st.slider(
            "批处理大小 (1-100)",
            min_value=1,
            max_value=100,
            value=get_batch_size(),
            key=f"batch_{pid}_{timestamp}"
        )
        
        # 暂停/恢复按钮
        paused = is_paused()
        if st.button("暂停处理" if not paused else "恢复处理", 
                    key=f"pause_{pid}_{timestamp}"):
            set_paused(not paused)
            st.experimental_rerun()
    
    with tab2:
        col1, col2, col3 = st.columns(3)
        
        with col1:
            if st.button("低配模式", key=f"low_{pid}_{timestamp}"):
                update_config(1, 1, False)
                st.experimental_rerun()
        
        with col2:
            if st.button("中配模式", key=f"medium_{pid}_{timestamp}"):
                update_config(8, 8, False)
                st.experimental_rerun()
        
        with col3:
            if st.button("高配模式", key=f"high_{pid}_{timestamp}"):
                update_config(16, 16, False)
                st.experimental_rerun()
    
    with tab3:
        st.info(f"当前进程ID: {pid}")
        st.info(f"启动时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        st.info(f"当前状态: {'已暂停' if is_paused() else '运行中'}")
        
        # 显示当前配置
        st.json({
            "线程数": get_thread_count(),
            "批处理大小": get_batch_size(),
            "暂停状态": is_paused()
        })
    
    # 自动保存配置
    update_config(thread_count, batch_size, is_paused())

def main():
    st.set_page_config(
        page_title="性能控制面板",
        page_icon="⚡",
        layout="wide"
    )
    
    # 创建性能控制标签页
    create_performance_tab()

if __name__ == "__main__":
    main() 