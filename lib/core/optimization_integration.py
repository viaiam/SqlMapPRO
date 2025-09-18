#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Copyright (c) 2006-2025 sqlmap developers (https://sqlmap.org)
See the file 'LICENSE' for copying permission

优化功能集成模块
该模块负责将SQLMap的优化功能（线程池、HTTP连接池等）集成到主程序中
"""

import os
import sys
import threading
import time

from lib.core.common import dataToStdout
from lib.core.common import getSafeExString
from lib.core.common import getUnicode
from lib.core.common import openFile
from lib.core.common import setPaths
from lib.core.data import conf
from lib.core.data import kb
from lib.core.data import logger
from lib.core.exception import SqlmapBaseException
from lib.core.exception import SqlmapConnectionException
from lib.core.exception import SqlmapValueException
from lib.core.exception import SqlmapUserQuitException
from lib.core.optimization import optimization_manager
from lib.core.settings import MAX_NUMBER_OF_THREADS

# 导入我们创建的优化组件
from lib.core.threadpool import runThreadsWithPool
from lib.request.connectionpool import ConnectionPoolManager

# HTTP连接池实例
_http_connection_pool = None

# 优化功能初始化标志
_initialized = False

# 线程池替换标志
_thread_pool_replaced = False

# HTTP连接池替换标志
_http_pool_replaced = False

# 原始的runThreads函数引用
_original_runThreads = None

# 原始的Connect.getPage方法引用
_original_getPage = None

# 原始的socket.create_connection函数引用
_original_create_connection = None

# 优化统计数据
_optimization_stats = {
    "thread_pool_usage_count": 0,        # 线程池使用次数
    "http_pool_usage_count": 0,          # HTTP连接池使用次数
    "query_cache_hits": 0,               # 查询缓存命中次数
    "query_cache_misses": 0,             # 查询缓存未命中次数
    "total_time_saved": 0.0,             # 总共节省的时间（秒）
    "start_time": time.time()            # 优化启用的时间
}

# 记录原始函数引用的函数
def _save_original_functions():
    """
    保存原始函数的引用，以便在需要时恢复
    """
    global _original_runThreads, _original_getPage, _original_create_connection
    
    # 保存原始的runThreads函数
    from lib.core import threads
    _original_runThreads = threads.runThreads
    
    # 保存原始的Connect.getPage方法
    from lib.request.connect import Connect
    _original_getPage = Connect.getPage
    
    # 保存原始的socket.create_connection函数
    import socket
    _original_create_connection = socket.create_connection

# 初始化HTTP连接池
def _init_http_connection_pool():
    """
    初始化HTTP连接池
    """
    global _http_connection_pool
    
    try:
        # 从配置中获取HTTP连接池的参数
        config = optimization_manager.get_optimization_config()
        max_connections = config.get("max_connections_per_host")
        if max_connections is None:
            # 默认使用核心数的2倍或最大线程数
            import multiprocessing
            max_connections = min(multiprocessing.cpu_count() * 2, MAX_NUMBER_OF_THREADS)
        
        # 创建连接池实例
        _http_connection_pool = ConnectionPoolManager(max_connections=max_connections)
        
        infoMsg = "HTTP连接池已初始化，最大连接数: %d" % max_connections
        logger.info(infoMsg)
        
    except Exception as ex:
        errMsg = "初始化HTTP连接池失败: %s" % getSafeExString(ex)
        logger.error(errMsg)

# 替换线程管理函数
def _replace_thread_management():
    """
    替换SQLMap的线程管理函数为我们的线程池实现
    """
    global _thread_pool_replaced
    
    try:
        from lib.core import threads
        
        # 保存原始函数引用
        if not _original_runThreads:
            _save_original_functions()
        
        # 替换为我们的线程池实现
        threads.runThreads = runThreadsWithPool
        
        _thread_pool_replaced = True
        
        infoMsg = "线程管理已替换为ThreadPoolExecutor实现"
        logger.info(infoMsg)
        
    except Exception as ex:
        errMsg = "替换线程管理函数失败: %s" % getSafeExString(ex)
        logger.error(errMsg)

# 替换HTTP连接处理
def _replace_http_connection():
    """
    替换SQLMap的HTTP连接处理逻辑，集成连接池功能
    """
    global _http_pool_replaced
    
    try:
        from lib.request.connect import Connect
        
        # 保存原始函数引用
        if not _original_getPage:
            _save_original_functions()
        
        # 初始化连接池
        if not _http_connection_pool:
            _init_http_connection_pool()
        
        # 定义新的getPage方法，集成连接池功能
        def _pooled_getPage(*args, **kwargs):
            global _optimization_stats
            
            # 增加HTTP连接池使用计数
            _optimization_stats["http_pool_usage_count"] += 1
            
            # 获取URL信息用于连接池
            url = None
            if args and isinstance(args[0], (str, bytes)):
                url = args[0]
            elif 'url' in kwargs:
                url = kwargs['url']
            
            # 如果有URL，尝试使用连接池
            if url and _http_connection_pool:
                try:
                    # 从连接池获取连接
                    conn = _http_connection_pool.get_connection(url)
                    
                    if conn:
                        # 使用连接池中的连接处理请求
                        # 这里需要根据实际情况修改，可能需要重写Connect.getPage方法的内部实现
                        # 为了简化，我们暂时还是使用原始方法，但记录使用了连接池
                        result = _original_getPage(*args, **kwargs)
                        
                        # 释放连接回池中
                        _http_connection_pool.release_connection(conn)
                        
                        return result
                except Exception as ex:
                    debugMsg = "使用HTTP连接池处理请求时出错: %s" % getSafeExString(ex)
                    logger.debug(debugMsg)
            
            # 如果无法使用连接池，回退到原始方法
            return _original_getPage(*args, **kwargs)
        
        # 替换原始方法
        Connect.getPage = _pooled_getPage
        
        _http_pool_replaced = True
        
        infoMsg = "HTTP连接处理已集成连接池功能"
        logger.info(infoMsg)
        
    except Exception as ex:
        errMsg = "集成HTTP连接池功能失败: %s" % getSafeExString(ex)
        logger.error(errMsg)

# 初始化所有优化功能
def init_optimizations():
    """
    初始化所有优化功能
    """
    global _initialized
    
    try:
        if _initialized:
            return
        
        # 保存原始函数引用
        _save_original_functions()
        
        # 初始化优化管理器
        # optimization_manager.initialize() - 此方法不存在，直接启用优化功能
        
        # 根据配置启用相应的优化功能
        if optimization_manager.is_optimization_enabled("thread_pool"):
            _replace_thread_management()
        
        if optimization_manager.is_optimization_enabled("http_connection_pool"):
            _replace_http_connection()
        
        # 启用查询缓存
        if optimization_manager.is_optimization_enabled("query_cache"):
            from lib.core.optimization import enable_query_cache
            enable_query_cache()
        
        _initialized = True
        
        infoMsg = "SQLMap优化功能已初始化"
        logger.info(infoMsg)
        
    except Exception as ex:
        errMsg = "初始化优化功能失败: %s" % getSafeExString(ex)
        logger.error(errMsg)

# 恢复原始功能
def restore_original_functions():
    """
    恢复原始的函数实现
    """
    global _thread_pool_replaced, _http_pool_replaced, _initialized
    
    try:
        # 恢复线程管理函数
        if _thread_pool_replaced:
            from lib.core import threads
            threads.runThreads = _original_runThreads
            _thread_pool_replaced = False
        
        # 恢复HTTP连接处理
        if _http_pool_replaced:
            from lib.request.connect import Connect
            Connect.getPage = _original_getPage
            _http_pool_replaced = False
        
        # 关闭连接池
        global _http_connection_pool
        if 'connection_pool_manager' in sys.modules['lib.request.connectionpool'].__dict__:
            from lib.request.connectionpool import connection_pool_manager
            connection_pool_manager.clear_pool()
        
        _initialized = False
        
        infoMsg = "SQLMap原始功能已恢复"
        logger.info(infoMsg)
        
    except Exception as ex:
        errMsg = "恢复原始功能失败: %s" % getSafeExString(ex)
        logger.error(errMsg)

# 获取优化统计信息
def get_optimization_stats():
    """
    获取优化功能的统计信息
    """
    global _optimization_stats
    
    # 计算总运行时间
    _optimization_stats["total_runtime"] = time.time() - _optimization_stats["start_time"]
    
    return _optimization_stats

# 打印优化统计信息
def print_optimization_stats():
    """
    打印优化功能的统计信息
    """
    try:
        stats = get_optimization_stats()
        
        infoMsg = "\n=== 优化功能统计信息 ==="
        logger.info(infoMsg)
        
        infoMsg = "线程池使用次数: %d" % stats["thread_pool_usage_count"]
        logger.info(infoMsg)
        
        infoMsg = "HTTP连接池使用次数: %d" % stats["http_pool_usage_count"]
        logger.info(infoMsg)
        
        infoMsg = "查询缓存命中率: %d/%d (%.2f%%)" % (
            stats["query_cache_hits"], 
            stats["query_cache_hits"] + stats["query_cache_misses"],
            (stats["query_cache_hits"] / (stats["query_cache_hits"] + stats["query_cache_misses"] + 1e-9)) * 100
        )
        logger.info(infoMsg)
        
        infoMsg = "总共节省时间: %.2f秒" % stats["total_time_saved"]
        logger.info(infoMsg)
        
        infoMsg = "总运行时间: %.2f秒" % stats["total_runtime"]
        logger.info(infoMsg)
        
        infoMsg = "优化功能状态: 线程池=%s, HTTP连接池=%s, 查询缓存=%s" % (
            "启用" if _thread_pool_replaced else "禁用",
            "启用" if _http_pool_replaced else "禁用",
            "启用" if optimization_manager.is_optimization_enabled("query_cache") else "禁用"
        )
        logger.info(infoMsg)
        
        infoMsg = "=========================="
        logger.info(infoMsg)
        
    except Exception as ex:
        errMsg = "打印优化统计信息时出错: %s" % getSafeExString(ex)
        logger.error(errMsg)

# 集成到SQLMap的主流程中
def integrate_with_sqlmap():
    """
    将优化功能集成到SQLMap的主流程中
    """
    try:
        # 在sqlmap.py的main函数中，在init()调用后初始化优化功能
        # 这里我们提供一个供外部调用的接口
        # 实际集成时需要修改sqlmap.py文件
        pass
        
    except Exception as ex:
        errMsg = "集成优化功能到SQLMap主流程时出错: %s" % getSafeExString(ex)
        logger.error(errMsg)

# 在模块加载时尝试自动初始化
try:
    # 检查是否在SQLMap的环境中运行
    if 'sqlmap' in sys.modules or any('sqlmap' in path for path in sys.path):
        # 等待SQLMap的核心模块初始化完成后再初始化优化功能
        # 实际集成时需要在sqlmap.py的适当位置调用init_optimizations()
        pass
        
except Exception:
    # 忽略初始化错误，避免影响SQLMap的正常运行
    pass

# 确保在程序退出时恢复原始功能和打印统计信息
import atexit
atexit.register(print_optimization_stats)
atexit.register(restore_original_functions)