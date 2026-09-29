from pegada.parsers.base import ParseResult, Parser
from pegada.parsers.claude_code import ClaudeCodeParser

PARSERS = {ClaudeCodeParser.agent: ClaudeCodeParser}

__all__ = ["PARSERS", "ClaudeCodeParser", "ParseResult", "Parser"]
