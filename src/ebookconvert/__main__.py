#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import shutil
import tempfile
import zipfile
import subprocess
import argparse
import glob
import time
from pathlib import Path
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeElapsedColumn
from rich.table import Table
from rich.prompt import Prompt
from rich.theme import Theme

# 创建一个自定义主题
custom_theme = Theme({
    "info": "cyan",
    "warning": "yellow",
    "error": "bold red",
    "success": "bold green",
    "epub": "bold blue",
    "mobi": "bold magenta",
    "pdf": "bold yellow"
})

# 初始化Rich控制台
console = Console(theme=custom_theme)

def extract_epub_images(epub_path, output_dir):
    """从EPUB文件中提取图片"""
    console.print(Panel(f"[epub]处理EPUB文件[/epub]: {epub_path}", title="EPUB提取", border_style="blue"))
    
    # 提取图片的计数器
    extracted_count = 0
    moved_count = 0
    
    # EPUB本质上是一个ZIP文件
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeElapsedColumn(),
        console=console
    ) as progress:
        # 创建任务
        extract_task = progress.add_task("[cyan]提取文件中...", total=100)
        
        # 获取文件信息列表以计算总数
        with zipfile.ZipFile(epub_path, 'r') as zip_ref:
            file_list = zip_ref.infolist()
            image_files = [f for f in file_list if any(f.filename.lower().endswith(ext) 
                           for ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp'])]
            
            # 更新任务总数
            progress.update(extract_task, total=len(image_files))
            
            # 提取文件
            for i, file_info in enumerate(image_files):
                zip_ref.extract(file_info, output_dir)
                extracted_count += 1
                progress.update(extract_task, advance=1, description=f"[cyan]正在提取: {os.path.basename(file_info.filename)}")
                time.sleep(0.01)  # 稍微减速以显示进度
    
    # 将提取的图片移动到输出目录的根目录
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        console=console
    ) as progress:
        move_task = progress.add_task("[green]整理文件...", total=0)
        
        # 先统计需要移动的文件数量
        files_to_move = []
        for root, _, files in os.walk(output_dir):
            for file in files:
                if any(file.lower().endswith(ext) for ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp']):
                    src_path = os.path.join(root, file)
                    dst_path = os.path.join(output_dir, file)
                    if src_path != dst_path:
                        files_to_move.append((src_path, dst_path))
        
        # 更新任务总数
        progress.update(move_task, total=len(files_to_move))
        
        # 执行移动
        for src_path, dst_path in files_to_move:
            # 如果目标文件已存在，添加序号
            if os.path.exists(dst_path):
                base, ext = os.path.splitext(dst_path)
                i = 1
                while os.path.exists(f"{base}_{i}{ext}"):
                    i += 1
                dst_path = f"{base}_{i}{ext}"
            
            shutil.move(src_path, dst_path)
            moved_count += 1
            progress.update(move_task, advance=1, description=f"[green]正在移动: {os.path.basename(dst_path)}")
    
    console.print(f"[success]✓ 已提取 {extracted_count} 个文件，整理 {moved_count} 个文件[/success]")

def extract_mobi_images(mobi_path, output_dir):
    """从MOBI文件中提取图片"""
    console.print(Panel(f"[mobi]处理MOBI文件[/mobi]: {mobi_path}", title="MOBI提取", border_style="magenta"))
    
    try:
        # 使用kindleunpack提取MOBI文件
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console
        ) as progress:
            task = progress.add_task("[magenta]运行KindleUnpack...", total=1)
            cmd = ['kindle_unpack', '-i', mobi_path, output_dir]
            subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            progress.update(task, advance=1)
        
        # 提取图片的计数器
        moved_count = 0
        
        # 寻找提取出的图片并移动到输出目录
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            console=console
        ) as progress:
            # 先统计需要移动的文件数量
            files_to_move = []
            for root, _, files in os.walk(output_dir):
                for file in files:
                    if any(file.lower().endswith(ext) for ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp']):
                        src_path = os.path.join(root, file)
                        dst_path = os.path.join(output_dir, file)
                        if src_path != dst_path:
                            files_to_move.append((src_path, dst_path))
            
            # 创建任务
            move_task = progress.add_task("[magenta]整理文件...", total=len(files_to_move))
            
            for src_path, dst_path in files_to_move:
                # 如果目标文件已存在，添加序号
                if os.path.exists(dst_path):
                    base, ext = os.path.splitext(dst_path)
                    i = 1
                    while os.path.exists(f"{base}_{i}{ext}"):
                        i += 1
                    dst_path = f"{base}_{i}{ext}"
                
                if src_path != dst_path:
                    shutil.move(src_path, dst_path)
                    moved_count += 1
                    progress.update(move_task, advance=1, description=f"[magenta]正在移动: {os.path.basename(dst_path)}")
        
        console.print(f"[success]✓ 已整理 {moved_count} 个文件[/success]")
                    
    except Exception as e:
        console.print(f"[error]处理MOBI文件时出错: {e}[/error]")
        console.print("[error]请确保已安装KindleUnpack工具[/error]")

def extract_pdf_images(pdf_path, output_dir):
    """从PDF文件中提取图片"""
    console.print(Panel(f"[pdf]处理PDF文件[/pdf]: {pdf_path}", title="PDF提取", border_style="yellow"))
    
    try:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console
        ) as progress:
            task = progress.add_task("[yellow]提取PDF图片...", total=1)
            # 使用pdfimages工具提取图片
            # pdfimages -all 会提取所有类型的图片
            cmd = ['pdfimages', '-all', pdf_path, os.path.join(output_dir, 'img')]
            subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            progress.update(task, advance=1)
            
        # 统计提取出的图片数量
        image_count = 0
        for _, _, files in os.walk(output_dir):
            for file in files:
                if any(file.lower().endswith(ext) for ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.ppm', '.pgm', '.pbm']):
                    image_count += 1
        
        console.print(f"[success]✓ 已从PDF提取 {image_count} 个图片[/success]")
            
    except Exception as e:
        console.print(f"[error]提取PDF图片时出错: {e}[/error]")
        console.print("[error]请确保已安装pdfimages工具 (poppler-utils)[/error]")

def create_zip_archive(source_dir, output_zip_path):
    """将提取的图片打包成ZIP文件"""
    console.print(Panel(f"创建ZIP压缩包: {output_zip_path}", title="压缩打包", border_style="cyan"))
    
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        console=console
    ) as progress:
        # 先统计文件数量
        files_to_zip = []
        for root, _, files in os.walk(source_dir):
            for file in files:
                file_path = os.path.join(root, file)
                arcname = os.path.relpath(file_path, source_dir)
                files_to_zip.append((file_path, arcname))
                
        # 创建压缩任务
        zip_task = progress.add_task("[cyan]正在压缩文件...", total=len(files_to_zip))
        
        # 执行压缩
        with zipfile.ZipFile(output_zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for file_path, arcname in files_to_zip:
                zipf.write(file_path, arcname)
                progress.update(zip_task, advance=1, description=f"[cyan]正在压缩: {os.path.basename(file_path)}")
                # time.sleep(0.01)  # 稍微减速以显示进度

def clean_path(path):
    """清理用户输入的路径，去除不必要的引号并处理特殊字符"""
    # 去除首尾的引号（如果有）
    path = path.strip()
    if (path.startswith('"') and path.endswith('"')) or (path.startswith("'") and path.endswith("'")):
        path = path[1:-1]
    
    # 处理Windows路径中的反斜杠转义
    path = path.replace('\\\\', '\\')
    
    return path

def process_ebook(file_path):
    """处理电子书文件并提取图片到ZIP压缩包"""
    file_path = os.path.abspath(file_path)
    if not os.path.exists(file_path):
        console.print(f"[error]错误: 文件 '{file_path}' 不存在[/error]")
        return
    
    # 创建临时目录用于存放提取的图片
    with tempfile.TemporaryDirectory() as temp_dir:
        file_name = os.path.basename(file_path)
        base_name, ext = os.path.splitext(file_name)
        ext = ext.lower()
        
        # 根据文件类型使用不同的提取方法
        if ext == '.epub':
            extract_epub_images(file_path, temp_dir)
        elif ext == '.mobi':
            extract_mobi_images(file_path, temp_dir)
        elif ext == '.pdf':
            extract_pdf_images(file_path, temp_dir)
        else:
            console.print(f"[error]不支持的文件类型: {ext}[/error]")
            return
        
        # 检查是否有提取到图片
        image_count = 0
        for root, _, files in os.walk(temp_dir):
            for file in files:
                if any(file.lower().endswith(ext) for ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.ppm', '.pgm', '.pbm']):
                    image_count += 1
        
        if image_count == 0:
            console.print(f"[warning]没有从 {file_name} 中提取到任何图片[/warning]")
            return
        
        console.print(f"[info]已提取 {image_count} 张图片[/info]")
        
        # 创建ZIP压缩包
        zip_path = os.path.join(os.path.dirname(file_path), f"{base_name}_images.zip")
        create_zip_archive(temp_dir, zip_path)
        console.print(Panel(f"[success]完成! 图片已保存到: {zip_path}[/success]", border_style="green"))

def main():
    # 绘制欢迎标题
    console.print(Panel.fit(
        "[bold cyan]电子书图片提取工具[/bold cyan]\n"
        "[dim]从EPUB/MOBI/PDF电子书中提取所有图片并打包为ZIP文件[/dim]",
        border_style="cyan"
    ))
    
    # 支持的电子书扩展名
    supported_extensions = ['.epub', '.mobi', '.pdf']
    files_to_process = []
      # 通过交互方式获取用户输入
    console.print("[info]请输入电子书文件路径或文件夹路径（支持通配符 *）：[/info]")
    console.print("[dim]可以输入多个路径，每行一个，输入空行结束[/dim]")
    
    inputs = []
    while True:
        user_input = Prompt.ask("> ").strip()
        if not user_input:
            break
        # 清理用户输入的路径
        cleaned_input = clean_path(user_input)
        inputs.append(cleaned_input)
    
    if not inputs:
        console.print("[warning]未提供任何输入，退出程序[/warning]")
        return
    
    # 创建一个表格显示输入信息
    table = Table(title="输入路径")
    table.add_column("类型", style="cyan")
    table.add_column("路径", style="green")
    
    for input_path in inputs:
        if os.path.isdir(input_path):
            table.add_row("目录", input_path)
        elif '*' in input_path or '?' in input_path:
            table.add_row("通配符", input_path)
        elif os.path.isfile(input_path):
            table.add_row("文件", input_path)
        else:
            table.add_row("未知", input_path)
            
    console.print(table)
    
    # 处理查找文件
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console
    ) as progress:
        scan_task = progress.add_task("[yellow]扫描文件中...", total=len(inputs))
        
        for input_path in inputs:
            # 如果是目录，处理目录下所有支持的电子书文件
            if os.path.isdir(input_path):
                progress.update(scan_task, description=f"[yellow]扫描目录: {input_path}")
                for ext in supported_extensions:
                    dir_files = glob.glob(os.path.join(input_path, f"*{ext}"))
                    files_to_process.extend(dir_files)
              # 如果包含通配符，使用glob进行匹配
            elif ('*' in input_path or '?' in input_path) and '[' not in input_path and ']' not in input_path:
                progress.update(scan_task, description=f"[yellow]扫描匹配: {input_path}")
                matched_files = glob.glob(input_path)
                for file in matched_files:
                    if os.path.isfile(file) and any(file.lower().endswith(ext) for ext in supported_extensions):
                        files_to_process.append(file)
            
            # 单个文件
            elif os.path.isfile(input_path) and any(input_path.lower().endswith(ext) for ext in supported_extensions):
                progress.update(scan_task, description=f"[yellow]添加文件: {input_path}")
                files_to_process.append(input_path)
            
            else:
                progress.update(scan_task, description=f"[yellow]跳过: {input_path}")
                console.print(f"[warning]警告: '{input_path}' 不是支持的电子书文件或目录，已跳过[/warning]")
            
            progress.update(scan_task, advance=1)
            time.sleep(0.2)  # 让用户能看清进度
    
    if not files_to_process:
        console.print("[error]未找到任何支持的电子书文件[/error]")
        return
    
    # 显示找到的文件
    console.print(f"[success]找到 {len(files_to_process)} 个电子书文件待处理[/success]")
    
    # 创建文件列表表格
    file_table = Table(title=f"待处理文件 ({len(files_to_process)})")
    file_table.add_column("序号", style="cyan", justify="right")
    file_table.add_column("类型", style="magenta")
    file_table.add_column("文件名", style="green")
    file_table.add_column("完整路径", style="dim")
    
    for i, file_path in enumerate(files_to_process):
        file_name = os.path.basename(file_path)
        ext = os.path.splitext(file_name)[1].lower()
        file_type = {
            '.epub': '[blue]EPUB[/blue]',
            '.mobi': '[magenta]MOBI[/magenta]',
            '.pdf': '[yellow]PDF[/yellow]'
        }.get(ext, "未知")
        
        file_table.add_row(str(i+1), file_type, file_name, file_path)
    
    console.print(file_table)
    
    # 询问是否继续
    if Prompt.ask("[yellow]是否继续处理这些文件?[/yellow]", choices=["y", "n"], default="y") == "n":
        console.print("[warning]已取消处理[/warning]")
        return
    
    # 处理文件
    for i, file_path in enumerate(files_to_process):
        console.rule(f"[{i+1}/{len(files_to_process)}] {os.path.basename(file_path)}")
        process_ebook(file_path)

if __name__ == "__main__":
    main()
