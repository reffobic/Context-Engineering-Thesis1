"""
File-level context selection: given a project file map (path + purpose) and a
user change request, return the minimal set of files an LLM needs as context.

This implements the idea from Dr. Ibrahim's demo: ask the AI "what are the
need context (min list of files) to answer the following user question/request?"
"""

from .file_map import FileEntry, FileMap
from .selector import FileContextSelector, SelectionResult

__all__ = ["FileEntry", "FileMap", "FileContextSelector", "SelectionResult"]
