#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
SQLMap优化功能测试脚本
"""

import os
import sys

# 添加SQLMap根目录到Python路径
sqlmap_path = os.path.dirname(os.path.abspath(__file__))
sys.path.append(sqlmap_path)

try:
    print("\n=== 测试SQLMap优化功能 ===")
    print("直接测试优化功能，跳过SQLMap复杂的初始化流程")
    
    # 导入优化模块和数据对象
    from lib.core.optimization import optimization_manager
    from lib.core.data import kb
    
    # 检查优化管理器是否可用
    print("\n=== 优化管理器状态检查 ===")
    print(f"优化管理器对象: {optimization_manager}")
    
    # 启用所有优化功能
    print("\n=== 启用SQLMap优化功能 ===")
    try:
        success = optimization_manager.enable_all_optimizations()
        print(f"是否成功启用所有优化功能: {success}")
    except Exception as e:
        print(f"启用优化功能时出错: {e}")
    
    # 获取优化状态
    print("\n=== 当前优化状态 ===")
    try:
        status = optimization_manager.get_optimization_status()
        for name, enabled in status.items():
            print(f"{name}: {'启用' if enabled else '禁用'}")
    except Exception as e:
        print(f"获取优化状态时出错: {e}")
    
    # 获取优化配置
    print("\n=== 优化配置 ===")
    try:
        config = optimization_manager.get_optimization_config()
        for key, value in config.items():
            print(f"{key}: {value}")
    except Exception as e:
        print(f"获取优化配置时出错: {e}")
    
    # 测试基本优化功能
    print("\n=== 测试查询缓存功能 ===")
    try:
        # 尝试启用查询缓存
        optimization_manager.enable_optimization("query_cache")
        print("查询缓存已启用")
        
        # 简单测试查询缓存是否工作
        if hasattr(kb, 'query_cache'):
            print("查询缓存对象已创建")
        else:
            print("查询缓存对象尚未创建")
    except Exception as e:
        print(f"测试查询缓存功能时出错: {e}")
    
    print("\n=== 测试HTTP连接池功能 ===")
    try:
        # 尝试启用HTTP连接池
        optimization_manager.enable_optimization("http_connection_pool")
        print("HTTP连接池已启用")
        
        # 检查连接池配置
        if hasattr(kb, 'max_connections_per_host'):
            print(f"最大每主机连接数: {kb.max_connections_per_host}")
        else:
            print("连接池配置尚未设置")
    except Exception as e:
        print(f"测试HTTP连接池功能时出错: {e}")
    
    print("\n=== 测试完成 ===")
    
except Exception as e:
    print(f"测试过程中发生错误: {e}")
    import traceback
    traceback.print_exc()