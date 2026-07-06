"""Core framework for k8s-multi-cluster MCP server.

This package provides the dynamic tool loading system that automatically
discovers and registers tools from the src/tools/ directory.
"""

from .server import DynamicMCPServer

__all__ = ["DynamicMCPServer"]
