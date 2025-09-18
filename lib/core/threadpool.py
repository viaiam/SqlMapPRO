#!/usr/bin/env python

# Copyright (c) 2006-2025 sqlmap developers (https://sqlmap.org)
# See the file 'LICENSE' for copying permission

import concurrent.futures
import threading
import traceback

from lib.core.common import getSafeExString
from lib.core.data import kb
from lib.core.enums import CUSTOM_LOGGING
from lib.core.exception import SqlmapBaseException, SqlmapConnectionException, SqlmapSkipTargetException, SqlmapUserQuitException, SqlmapValueException
from lib.core.log import LOGGER as logger
from lib.core.settings import THREAD_FINALIZATION_TIMEOUT

class ThreadPoolExecutorWithExceptionHandling(concurrent.futures.ThreadPoolExecutor):
    """
    增强版ThreadPoolExecutor，添加了异常处理和线程追踪功能
    """
    
    def __init__(self, max_workers=None):
        """
        初始化线程池执行器
        
        Args:
            max_workers: 最大线程数
        """
        super(ThreadPoolExecutorWithExceptionHandling, self).__init__(max_workers=max_workers)
        self.active_threads = set()
        self.lock = threading.Lock()
    
    def submit(self, fn, *args, **kwargs):
        """
        提交任务到线程池，并跟踪活动线程
        
        Args:
            fn: 要执行的函数
            *args: 函数参数
            **kwargs: 关键字参数
            
        Returns:
            未来对象(Future)
        """
        future = super(ThreadPoolExecutorWithExceptionHandling, self).submit(self._wrap_function, fn, *args, **kwargs)
        
        with self.lock:
            self.active_threads.add(future)
        
        future.add_done_callback(lambda f: self.active_threads.discard(f))
        
        return future
    
    def _wrap_function(self, fn, *args, **kwargs):
        """
        包装函数以捕获和处理异常，并记录线程ID
        
        Args:
            fn: 要执行的函数
            *args: 函数参数
            **kwargs: 关键字参数
            
        Returns:
            函数执行结果
        """
        try:
            kb.multiThreadMode = True
            return fn(*args, **kwargs)
        except KeyboardInterrupt:
            raise
        except (SqlmapUserQuitException, SqlmapSkipTargetException):
            raise
        except Exception as ex:
            if not isinstance(ex, (SqlmapUserQuitException, SqlmapSkipTargetException)):
                errMsg = getSafeExString(ex) if isinstance(ex, SqlmapBaseException) else "%s: %s" % (type(ex).__name__, getSafeExString(ex))
                logger.error("thread %s: '%s'" % (threading.current_thread().getName(), errMsg))

                if conf.get("verbose") > 1 and not isinstance(ex, SqlmapConnectionException):
                    traceback.print_exc()
            raise
        finally:
            if not threading.active_count() > 1:
                kb.multiThreadMode = False

from lib.core.option import conf

def runThreadsWithPool(numThreads, threadFunction, cleanupFunction=None, forwardException=True, threadChoice=False, startThreadMsg=True):
    """
    使用ThreadPoolExecutor实现多线程执行
    
    Args:
        numThreads: 线程数量
        threadFunction: 要在每个线程中执行的函数
        cleanupFunction: 清理函数，在所有线程完成后执行
        forwardException: 是否向前传递异常
        threadChoice: 是否允许用户选择线程数量
        startThreadMsg: 是否显示启动线程的消息
    """
    kb.multiThreadMode = True
    threads = []
    
    try:
        # 创建线程池，使用合适的线程数量
        if numThreads <= 0:
            numThreads = 1
        
        # 根据系统资源动态调整线程数量上限
        import os
        import multiprocessing
        max_possible_threads = min(multiprocessing.cpu_count() * 4, 32)  # 最多使用CPU核心数的4倍或32个线程
        numThreads = min(numThreads, max_possible_threads)
        
        if threadChoice and numThreads > 1:
            message = "please enter number of threads? [%d] " % numThreads
            reply = raw_input(message)
            if reply.isdigit() and int(reply) > 0:
                numThreads = int(reply)
        
        if startThreadMsg and numThreads > 1:
            logger.info("starting %d threads" % numThreads)
        
        # 创建线程池执行器
        with ThreadPoolExecutorWithExceptionHandling(max_workers=numThreads) as executor:
            # 提交任务到线程池
            futures = []
            for i in range(numThreads):
                future = executor.submit(threadFunction, i, numThreads)
                futures.append(future)
            
            # 等待所有任务完成
            try:
                # 收集所有结果，处理异常
                for future in concurrent.futures.as_completed(futures):
                    try:
                        future.result()
                    except Exception as ex:
                        if isinstance(ex, KeyboardInterrupt):
                            raise
                        
                        # 记录异常但继续处理其他任务
                        if not isinstance(ex, (SqlmapUserQuitException, SqlmapSkipTargetException)):
                            errMsg = getSafeExString(ex) if isinstance(ex, SqlmapBaseException) else "%s: %s" % (type(ex).__name__, getSafeExString(ex))
                            logger.error("thread error: '%s'" % errMsg)
            except KeyboardInterrupt:
                if numThreads > 1:
                    logger.info("waiting for threads to finish%s" % (" (Ctrl+C was pressed)" if isinstance(ex, KeyboardInterrupt) else ""))
            
            if forwardException:
                # 重新抛出第一个异常
                for future in futures:
                    if future.exception():
                        raise future.exception()
    except (SqlmapConnectionException, SqlmapValueException) as ex:
        print()
        kb.threadException = True
        logger.error("thread %s: '%s'" % (threading.current_thread().getName(), ex))

        if conf.get("verbose") > 1 and isinstance(ex, SqlmapValueException):
            traceback.print_exc()
    except KeyboardInterrupt:
        kb.multipleCtrlC = True
        raise
    except Exception as ex:
        if not kb.multipleCtrlC:
            from lib.core.common import unhandledExceptionMessage

            kb.threadException = True
            errMsg = unhandledExceptionMessage()
            logger.error("thread %s: %s" % (threading.current_thread().getName(), errMsg))
            traceback.print_exc()
    finally:
        kb.multiThreadMode = False
        
        # 执行清理函数
        if cleanupFunction:
            try:
                cleanupFunction()
            except KeyboardInterrupt:
                raise
            except Exception as ex:
                errMsg = getSafeExString(ex) if isinstance(ex, SqlmapBaseException) else "%s: %s" % (type(ex).__name__, getSafeExString(ex))
                logger.error("error occurred during thread cleanup: '%s'" % errMsg)
                
                if conf.get("verbose") > 1:
                    traceback.print_exc()