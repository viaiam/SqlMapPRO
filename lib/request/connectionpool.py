#!/usr/bin/env python

# Copyright (c) 2006-2025 sqlmap developers (https://sqlmap.org)
# See the file 'LICENSE' for copying permission

import http.client
import socket
import threading
import time
from urllib.parse import urlparse

from lib.core.common import getSafeExString
from lib.core.data import kb
from lib.core.log import LOGGER as logger
# HTTP连接超时设置
DEFAULT_HTTP_CONNECT_TIMEOUT = 10  # 秒
DEFAULT_HTTP_RECV_TIMEOUT = 30     # 秒
DEFAULT_HTTP_SEND_TIMEOUT = 15     # 秒
DEFAULT_MAX_CONNECTIONS_PER_HOST = 10

class ConnectionPoolManager:
    """
    HTTP连接池管理类，用于优化HTTP请求性能
    """
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(ConnectionPoolManager, cls).__new__(cls)
                cls._instance._initialize()
            return cls._instance
    
    def _initialize(self):
        """
        初始化连接池管理器
        """
        self.pools = {}
        self.pool_locks = {}
        self.global_lock = threading.Lock()
        self.connection_usage_stats = {}
        
    def get_connection(self, host, port, use_ssl=False):
        """
        从连接池获取一个可用的连接
        
        Args:
            host: 主机名
            port: 端口号
            use_ssl: 是否使用SSL
        
        Returns:
            可用的HTTP连接
        """
        key = (host, port, use_ssl)
        
        # 确保该主机的连接池存在
        with self.global_lock:
            if key not in self.pools:
                self.pools[key] = []
                self.pool_locks[key] = threading.Lock()
                self.connection_usage_stats[key] = {"created": 0, "reused": 0, "failed": 0}
        
        pool_lock = self.pool_locks[key]
        
        with pool_lock:
            # 尝试从池中获取一个可用连接
            while self.pools[key]:
                connection = self.pools[key].pop()
                
                # 检查连接是否有效
                if self._is_connection_valid(connection):
                    self.connection_usage_stats[key]["reused"] += 1
                    return connection
                else:
                    self.connection_usage_stats[key]["failed"] += 1
            
            # 如果没有可用连接，创建一个新的
            max_connections = kb.get("max_connections_per_host", DEFAULT_MAX_CONNECTIONS_PER_HOST)
            current_connections = self.connection_usage_stats[key]["created"] + len(self.pools[key])
            
            if current_connections < max_connections:
                connection = self._create_connection(host, port, use_ssl)
                if connection:
                    self.connection_usage_stats[key]["created"] += 1
                    return connection
        
        # 如果达到最大连接数限制，返回None
        return None
    
    def release_connection(self, connection, host, port, use_ssl=False, reuse=True):
        """
        将连接释放回连接池
        
        Args:
            connection: 要释放的连接
            host: 主机名
            port: 端口号
            use_ssl: 是否使用SSL
            reuse: 是否可重用
        """
        if not connection or not reuse:
            try:
                connection.close()
            except Exception:
                pass
            return
        
        key = (host, port, use_ssl)
        
        with self.global_lock:
            if key not in self.pools:
                return
        
        with self.pool_locks[key]:
            # 检查连接是否仍然有效
            if self._is_connection_valid(connection):
                # 如果池未满，将连接放回池中
                max_connections = kb.get("max_connections_per_host", DEFAULT_MAX_CONNECTIONS_PER_HOST)
                if len(self.pools[key]) < max_connections:
                    self.pools[key].append(connection)
                    return
            
            # 如果连接无效或池已满，关闭连接
            try:
                connection.close()
            except Exception:
                pass
    
    def _create_connection(self, host, port, use_ssl=False):
        """
        创建一个新的HTTP连接
        
        Args:
            host: 主机名
            port: 端口号
            use_ssl: 是否使用SSL
        
        Returns:
            创建的HTTP连接或None（如果失败）
        """
        try:
            if use_ssl:
                # 使用HTTPS连接
                connection = http.client.HTTPSConnection(
                    host=host,
                    port=port,
                    timeout=DEFAULT_HTTP_CONNECT_TIMEOUT
                )
            else:
                # 使用HTTP连接
                connection = http.client.HTTPConnection(
                    host=host,
                    port=port,
                    timeout=DEFAULT_HTTP_CONNECT_TIMEOUT
                )
            
            # 设置连接选项以优化性能
            self._set_connection_options(connection)
            
            return connection
        except Exception as ex:
            logger.debug("Error creating connection to %s:%s (SSL: %s): %s" % (
                host, port, use_ssl, getSafeExString(ex)
            ))
            return None
    
    def _set_connection_options(self, connection):
        """
        设置连接选项以优化性能
        
        Args:
            connection: 要设置的连接
        """
        try:
            # 启用TCP_NODELAY以减少延迟
            if hasattr(connection, 'sock') and connection.sock:
                connection.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                
            # 启用Keep-Alive
            connection.set_tunnel(connection.host, connection.port) if hasattr(connection, 'set_tunnel') else None
            
            # 设置超时
            if hasattr(connection, 'sock') and connection.sock:
                connection.sock.settimeout(DEFAULT_HTTP_RECV_TIMEOUT)
        except Exception:
            pass  # 忽略无法设置的选项
    
    def _is_connection_valid(self, connection):
        """
        检查连接是否仍然有效
        
        Args:
            connection: 要检查的连接
        
        Returns:
            如果连接有效，返回True；否则返回False
        """
        try:
            # 检查连接是否已关闭
            if hasattr(connection, 'closed') and connection.closed:
                return False
            
            # 检查socket是否有效
            if hasattr(connection, 'sock') and connection.sock:
                # 使用select来检查socket是否仍然可读/可写
                import select
                readable, writable, exceptional = select.select([connection.sock], [connection.sock], [connection.sock], 0)
                
                # 如果socket在exceptional列表中，则它已损坏
                if exceptional:
                    return False
                
            return True
        except Exception:
            # 发生任何异常，认为连接无效
            return False
    
    def clear_pool(self, host=None, port=None, use_ssl=None):
        """
        清理连接池
        
        Args:
            host: 主机名（可选）
            port: 端口号（可选）
            use_ssl: 是否使用SSL（可选）
        """
        with self.global_lock:
            if host is None and port is None and use_ssl is None:
                # 清理所有连接池
                for key in list(self.pools.keys()):
                    self._close_all_connections(key)
            else:
                # 清理特定的连接池
                for key in list(self.pools.keys()):
                    if ((host is None or key[0] == host) and 
                        (port is None or key[1] == port) and 
                        (use_ssl is None or key[2] == use_ssl)):
                            self._close_all_connections(key)
    
    def _close_all_connections(self, key):
        """
        关闭特定连接池中的所有连接
        
        Args:
            key: 连接池的键
        """
        if key in self.pool_locks:
            with self.pool_locks[key]:
                if key in self.pools:
                    for connection in self.pools[key]:
                        try:
                            connection.close()
                        except Exception:
                            pass
                    
                    self.pools[key] = []
    
    def get_stats(self):
        """
        获取连接池统计信息
        
        Returns:
            统计信息字典
        """
        stats = {}
        
        with self.global_lock:
            for key, usage in self.connection_usage_stats.items():
                host, port, use_ssl = key
                protocol = "https" if use_ssl else "http"
                address = "%s://%s:%d" % (protocol, host, port)
                
                stats[address] = {
                    "created": usage["created"],
                    "reused": usage["reused"],
                    "failed": usage["failed"],
                    "active": len(self.pools.get(key, []))
                }
        
        return stats

# 全局连接池管理器实例
connection_pool_manager = ConnectionPoolManager()