"""Small, non-executing parser for Lightroom's Lua table literals."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any


class LuaTableError(ValueError):
    """Raised when a catalog settings literal is not a supported Lua table."""


@dataclass(frozen=True)
class _Token:
    kind: str
    value: Any
    offset: int


_NUMBER = re.compile(r"[+-]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?")
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_PUNCTUATION = frozenset("{}[]=,;")


def _decode_string(source: str, offset: int) -> tuple[str, int]:
    quote = source[offset]
    index = offset + 1
    result: list[str] = []
    escapes = {"a": "\a", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v"}
    while index < len(source):
        char = source[index]
        index += 1
        if char == quote:
            return "".join(result), index
        if char != "\\":
            result.append(char)
            continue
        if index >= len(source):
            raise LuaTableError(f"unterminated escape at character {index - 1}")
        escaped = source[index]
        index += 1
        if escaped in escapes:
            result.append(escapes[escaped])
        elif escaped in "\\\"'":
            result.append(escaped)
        elif escaped == "\n":
            result.append("\n")
        elif escaped == "\r":
            if index < len(source) and source[index] == "\n":
                index += 1
            result.append("\n")
        elif escaped.isdigit():
            digits = escaped
            while len(digits) < 3 and index < len(source) and source[index].isdigit():
                digits += source[index]
                index += 1
            result.append(chr(int(digits, 10)))
        elif escaped == "z":
            while index < len(source) and source[index].isspace():
                index += 1
        else:
            result.append(escaped)
    raise LuaTableError(f"unterminated string at character {offset}")


def _tokenize(source: str) -> list[_Token]:
    tokens: list[_Token] = []
    index = 0
    while index < len(source):
        char = source[index]
        if char.isspace():
            index += 1
            continue
        if source.startswith("--", index):
            newline = source.find("\n", index + 2)
            index = len(source) if newline < 0 else newline + 1
            continue
        if char in "\"'":
            value, index = _decode_string(source, index)
            tokens.append(_Token("string", value, index))
            continue
        if char in _PUNCTUATION:
            tokens.append(_Token(char, char, index))
            index += 1
            continue
        number = _NUMBER.match(source, index)
        if number:
            raw = number.group(0)
            value = float(raw) if any(marker in raw for marker in ".eE") else int(raw)
            tokens.append(_Token("number", value, index))
            index = number.end()
            continue
        identifier = _IDENTIFIER.match(source, index)
        if identifier:
            tokens.append(_Token("identifier", identifier.group(0), index))
            index = identifier.end()
            continue
        raise LuaTableError(f"unexpected character {char!r} at character {index}")
    tokens.append(_Token("eof", None, len(source)))
    return tokens


class _Parser:
    def __init__(self, source: str):
        self.tokens = _tokenize(source)
        self.index = 0

    @property
    def current(self) -> _Token:
        return self.tokens[self.index]

    def _take(self, kind: str) -> _Token:
        token = self.current
        if token.kind != kind:
            raise LuaTableError(f"expected {kind!r} at character {token.offset}, got {token.kind!r}")
        self.index += 1
        return token

    def _value(self) -> Any:
        token = self.current
        if token.kind == "{":
            return self._table()
        if token.kind in {"string", "number"}:
            self.index += 1
            return token.value
        if token.kind == "identifier":
            self.index += 1
            if token.value == "true":
                return True
            if token.value == "false":
                return False
            if token.value == "nil":
                return None
            # Lightroom quotes its enum-like values, but accepting a bare
            # identifier keeps this a useful data parser without executing Lua.
            return token.value
        raise LuaTableError(f"expected value at character {token.offset}")

    def _table(self) -> dict[Any, Any] | list[Any]:
        self._take("{")
        positional: list[Any] = []
        keyed: dict[Any, Any] = {}
        next_index = 1
        while self.current.kind != "}":
            if self.current.kind == "eof":
                raise LuaTableError("unterminated table")
            key: Any | None = None
            if self.current.kind == "[":
                self._take("[")
                key = self._value()
                self._take("]")
                self._take("=")
            elif self.current.kind == "identifier" and self.tokens[self.index + 1].kind == "=":
                key = self._take("identifier").value
                self._take("=")
            value = self._value()
            if key is None:
                positional.append(value)
                while next_index in keyed:
                    next_index += 1
                keyed[next_index] = value
                next_index += 1
            else:
                keyed[key] = value
                if isinstance(key, int) and key >= next_index:
                    next_index = key + 1
            if self.current.kind in {",", ";"}:
                self.index += 1
            elif self.current.kind != "}":
                raise LuaTableError(f"expected table separator at character {self.current.offset}")
        self._take("}")
        return positional if not keyed or len(positional) == len(keyed) else keyed

    def parse(self) -> dict[str, Any]:
        # Catalogs conventionally prefix the literal with ``s =``.  Accepting
        # a direct table is useful for focused tests and exported fragments.
        if self.current.kind == "identifier" and self.tokens[self.index + 1].kind == "=":
            self._take("identifier")
            self._take("=")
        result = self._value()
        if self.current.kind != "eof":
            raise LuaTableError(f"unexpected token at character {self.current.offset}")
        if not isinstance(result, dict):
            raise LuaTableError("top-level Lightroom settings value must be a table with keys")
        return result


def parse_lua_table(text: str | bytes) -> dict[str, Any]:
    """Parse a Lightroom ``s = { ... }`` value without evaluating Lua."""

    source = text.decode("utf-8", "replace") if isinstance(text, bytes) else text
    return _Parser(source).parse()
