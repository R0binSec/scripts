#!/usr/bin/env python3
"""
独立的网页截图工具
支持命令行参数：
- -u/--url: 指定单个URL进行截图
- -f/--file: 指定包含URL列表的文件进行批量截图
截图保存到当前目录下的img文件夹，并生成HTML图片链接页面
"""

import asyncio
import argparse
import os
import sys
import logging
import datetime
from typing import List, Optional
from pathlib import Path

from playwright.async_api import async_playwright

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class ScreenshotTool:
    """截图工具类"""
    
    def __init__(self, concurrency: int = 3):
        """
        初始化截图工具
        
        Args:
            concurrency: 并发截图数
        """
        self.concurrency = concurrency
        # self.output_dir = Path("img")
        self.output_dir = Path(f"img_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}")
        
        # 创建输出目录
        self.output_dir.mkdir(exist_ok=True)
    
    async def capture_screenshot(self, url: str, page) -> bool:
        """
        捕获单个URL的截图
        
        Args:
            url: 目标URL
            page: Playwright Page对象
        
        Returns:
            是否截图成功
        """
        try:
            # 清理URL，用于文件名
            filename = self._sanitize_filename(url)
            output_path = self.output_dir / f"{filename}.png"
            
            # 尝试加载页面
            try:
                await page.goto(url, timeout=30000, wait_until='networkidle')
            except Exception as e:
                logger.warning(f"页面加载异常但尝试截图: {url}, 错误: {str(e)[:50]}")
            
            # 截图
            await page.screenshot(
                path=str(output_path),
                type='png',
                full_page=False
            )
            
            logger.info(f"截图成功: {url} -> {output_path}")
            return True
            
        except asyncio.TimeoutError:
            logger.error(f"截图超时: {url}")
            return False
        except Exception as e:
            logger.error(f"截图失败: {url}, 错误: {str(e)[:100]}")
            return False
    
    async def _capture_with_semaphore(self, url: str, context, semaphore: asyncio.Semaphore) -> tuple:
        """
        使用信号量控制并发的截图任务
        
        Args:
            url: 目标URL
            context: Playwright BrowserContext
            semaphore: 并发控制信号量
        
        Returns:
            (url, 是否成功) 元组
        """
        async with semaphore:
            page = await context.new_page()
            try:
                success = await self.capture_screenshot(url, page)
                return (url, success)
            finally:
                await page.close()
    
    async def capture_single_url(self, url: str) -> bool:
        """
        截图单个URL
        
        Args:
            url: 目标URL
        
        Returns:
            是否截图成功
        """
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    '--no-sandbox',
                    '--disable-setuid-sandbox',
                    '--disable-dev-shm-usage',
                    '--disable-gpu'
                ]
            )
            
            try:
                context = await browser.new_context(
                    viewport={'width': 1920, 'height': 1080},
                    ignore_https_errors=True,
                    user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
                )
                
                page = await context.new_page()
                success = await self.capture_screenshot(url, page)
                await page.close()
                await context.close()
                
                return success
                
            finally:
                await browser.close()
    
    async def capture_urls_from_file(self, file_path: str) -> dict:
        """
        从文件读取URL列表进行批量截图
        
        Args:
            file_path: 包含URL列表的文件路径
        
        Returns:
            统计信息字典
        """
        urls = self._read_urls_from_file(file_path)
        if not urls:
            logger.error(f"文件 {file_path} 中没有有效的URL")
            return {'total': 0, 'successful': 0, 'failed': 0}
        
        return await self.capture_urls(urls)
    
    async def capture_urls(self, urls: List[str]) -> dict:
        """
        批量截图URL列表
        
        Args:
            urls: URL列表
        
        Returns:
            统计信息字典
        """
        if not urls:
            return {'total': 0, 'successful': 0, 'failed': 0}
        
        total = len(urls)
        successful = 0
        failed = 0
        
        logger.info(f"开始批量截图 - URL数: {total}, 并发数: {self.concurrency}")
        
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    '--no-sandbox',
                    '--disable-setuid-sandbox',
                    '--disable-dev-shm-usage',
                    '--disable-gpu'
                ]
            )
            
            try:
                context = await browser.new_context(
                    viewport={'width': 1920, 'height': 1080},
                    ignore_https_errors=True,
                    user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
                )
                
                # 使用信号量控制并发
                semaphore = asyncio.Semaphore(self.concurrency)
                
                # 创建所有任务
                tasks = [
                    self._capture_with_semaphore(url, context, semaphore)
                    for url in urls
                ]
                
                # 执行所有任务
                for coro in asyncio.as_completed(tasks):
                    url, success = await coro
                    if success:
                        successful += 1
                    else:
                        failed += 1
                    
                    # 每10个URL打印一次进度
                    if (successful + failed) % 10 == 0:
                        logger.info(f"进度: {successful + failed}/{total}")
                
                await context.close()
                
            finally:
                await browser.close()
        
        logger.info(f"截图完成 - 总数: {total}, 成功: {successful}, 失败: {failed}")
        
        # 生成HTML图片链接页面
        if successful > 0:
            self._generate_html_gallery()
        
        return {'total': total, 'successful': successful, 'failed': failed}
    
    def _read_urls_from_file(self, file_path: str) -> List[str]:
        """
        从文件读取URL列表
        
        Args:
            file_path: 文件路径
        
        Returns:
            URL列表
        """
        if not os.path.exists(file_path):
            logger.error(f"文件不存在: {file_path}")
            return []
        
        urls = []
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line and self._is_valid_url(line):
                        urls.append(line)
        except Exception as e:
            logger.error(f"读取文件失败: {file_path}, 错误: {e}")
            return []
        
        return urls
    
    def _is_valid_url(self, url: str) -> bool:
        """
        检查URL是否有效
        
        Args:
            url: URL字符串
        
        Returns:
            是否有效
        """
        return url.startswith(('http://', 'https://'))
    
    def _sanitize_filename(self, url: str) -> str:
        """
        清理URL，生成安全的文件名
        
        Args:
            url: URL字符串
        
        Returns:
            安全的文件名
        """
        # 替换协议
        filename = url.replace('http://', 'http__').replace('https://', 'https__')
        
        # 替换特殊字符
        filename = filename.replace('/', '_').replace('?', '_').replace('&', '_')
        filename = filename.replace('=', '_').replace(':', '_').replace('*', '_')
        filename = filename.replace('"', '_').replace('<', '_').replace('>', '_')
        filename = filename.replace('|', '_').replace('\\', '_')
        
        # 限制文件名长度
        if len(filename) > 100:
            filename = filename[:100]
        
        return filename
    
    def _generate_html_gallery(self) -> None:
        """
        生成HTML图片链接页面，每行显示6个图片
        """
        # 获取当前时间，精确到秒
        current_time = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        html_filename = f"screenshots_{current_time}.html"
        
        # 获取所有PNG文件
        png_files = list(self.output_dir.glob("*.png"))
        if not png_files:
            logger.warning("没有找到PNG文件，跳过HTML生成")
            return
        
        # 按文件名排序
        png_files.sort()
        
        # 生成HTML内容
        html_content = self._build_html_content(png_files)
        
        # 保存HTML文件
        with open(html_filename, 'w', encoding='utf-8') as f:
            f.write(html_content)
        
        logger.info(f"HTML图片链接页面已生成: {html_filename}")
    
    def _build_html_content(self, png_files: list) -> str:
        """
        构建HTML内容
        
        Args:
            png_files: PNG文件列表
        
        Returns:
            HTML内容字符串
        """
        html = '''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>网页截图预览</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            margin: 20px;
            background-color: #f5f5f5;
        }
        .container {
            margin: 0 auto;
            background: white;
            padding: 20px;
            border-radius: 8px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
        }
        h1 {
            text-align: center;
            color: #333;
            margin-bottom: 30px;
        }
        .gallery {
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 15px;
        }
        .screenshot-item {
            text-align: center;
            border: 1px solid #ddd;
            border-radius: 4px;
            padding: 10px;
            background: #fafafa;
            transition: transform 0.2s;
        }
        .screenshot-item:hover {
            transform: translateY(-2px);
            box-shadow: 0 4px 8px rgba(0,0,0,0.1);
        }
        .screenshot-item img {
            max-width: 100%;
            height: auto;
            border-radius: 4px;
            cursor: pointer;
        }
        .screenshot-item .url {
            margin-top: 8px;
            font-size: 12px;
            color: #666;
            word-break: break-all;
            max-height: 60px;
            overflow: hidden;
        }
        .modal {
            display: none;
            position: fixed;
            z-index: 1000;
            left: 0;
            top: 0;
            width: 100%;
            height: 100%;
            background-color: rgba(0,0,0,0.9);
        }
        .modal-content {
            margin: auto;
            display: block;
            max-width: 90%;
            max-height: 90%;
            margin-top: 5%;
        }
        .close {
            position: absolute;
            top: 15px;
            right: 35px;
            color: #fff;
            font-size: 40px;
            font-weight: bold;
            cursor: pointer;
        }
        .stats {
            text-align: center;
            margin-bottom: 20px;
            color: #666;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>网页截图预览</h1>
        <div class="stats">
            共 <strong>''' + str(len(png_files)) + '''</strong> 张截图，生成时间: ''' + datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S") + '''
        </div>
        <div class="gallery">
'''
        
        # 添加图片项
        for png_file in png_files:
            # 从文件名还原URL
            url = self._filename_to_url(png_file.stem)
            
            html += f'''            <div class="screenshot-item">
                <img src="{self.output_dir}/{png_file.name}" alt="{url}" onclick="openModal(this)">
                <div class="url" title="{url}"><a href="{url}" target="_blank">{url}</a></div>
            </div>
'''
        
        html += '''        </div>
    </div>
    
    <!-- 模态框 -->
    <div id="modal" class="modal">
        <span class="close" onclick="closeModal()">&times;</span>
        <img class="modal-content" id="modal-img">
    </div>
    
    <script>
        function openModal(img) {
            document.getElementById('modal').style.display = 'block';
            document.getElementById('modal-img').src = img.src;
        }
        
        function closeModal() {
            document.getElementById('modal').style.display = 'none';
        }
        
        // 点击模态框背景关闭
        document.getElementById('modal').onclick = function(event) {
            if (event.target === this) {
                closeModal();
            }
        }
        
        // ESC键关闭
        document.addEventListener('keydown', function(event) {
            if (event.key === 'Escape') {
                closeModal();
            }
        });
    </script>
</body>
</html>'''
        
        return html
    
    def _filename_to_url(self, filename: str) -> str:
        """
        从文件名还原URL
        
        Args:
            filename: 文件名（不含扩展名）
        
        Returns:
            原始URL
        """
        # 还原协议
        url = filename.replace('http__', 'http://').replace('https__', 'https://')
        
        # 还原其他字符
        url = url.replace('_', '/')
        
        return url


def main():
    """主函数"""
    parser = argparse.ArgumentParser(description='网页截图工具')
    parser.add_argument('-u', '--url', type=str, help='单个URL进行截图')
    parser.add_argument('-f', '--file', type=str, help='包含URL列表的文件路径')
    parser.add_argument('-c', '--concurrency', type=int, default=3, help='并发数（默认3）')
    
    args = parser.parse_args()
    
    # 检查参数
    if not args.url and not args.file:
        parser.print_help()
        print("\n错误: 必须指定 -u/--url 或 -f/--file 参数")
        sys.exit(1)
    
    if args.url and args.file:
        print("错误: 不能同时使用 -u 和 -f 参数")
        sys.exit(1)
    
    # 创建截图工具
    tool = ScreenshotTool(concurrency=args.concurrency)
    
    # 执行截图
    try:
        if args.url:
            # 截图单个URL
            success = asyncio.run(tool.capture_single_url(args.url))
            if success:
                print(f"截图成功: {args.url}")
                # 单个URL也生成HTML
                tool._generate_html_gallery()
            else:
                print(f"截图失败: {args.url}")
                sys.exit(1)
        else:
            # 批量截图
            result = asyncio.run(tool.capture_urls_from_file(args.file))
            print(f"批量截图完成: 总数={result['total']}, 成功={result['successful']}, 失败={result['failed']}")
            
    except KeyboardInterrupt:
        print("\n用户中断操作")
        sys.exit(1)
    except Exception as e:
        logger.error(f"程序执行异常: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()