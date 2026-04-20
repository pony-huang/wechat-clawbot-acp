"""Streaming markdown filter — character-level state machine (mirrors TypeScript)."""
import re
from typing import Literal

State = Literal["sol", "body", "fence", "inline"]


class StreamingMarkdownFilter:
    """Streaming markdown filter stripping unsupported syntax character-by-character."""

    def __init__(self) -> None:
        self._buf = ""
        self._fence = False
        self._sol = True
        self._inl: dict | None = None  # {type: str, acc: str}

    def feed(self, delta: str) -> str:
        self._buf += delta
        return self._pump(False)

    def flush(self) -> str:
        return self._pump(True)

    def _pump(self, eof: bool) -> str:
        out = ""
        while self._buf:
            s_len = len(self._buf)
            s_sol = self._sol
            s_fence = self._fence
            s_inl = self._inl

            if self._fence:
                out += self._pump_fence(eof)
            elif self._inl:
                out += self._pump_inline(eof)
            elif self._sol:
                out += self._pump_sol(eof)
            else:
                out += self._pump_body(eof)

            if (
                len(self._buf) == s_len
                and self._sol == s_sol
                and self._fence == s_fence
                and self._inl == s_inl
            ):
                break

        if eof and self._inl:
            markers = {"image": "![", "bold3": "***", "italic": "*", "ubold3": "___", "uitalic": "_"}
            mtype = self._inl["type"]
            out += (markers.get(mtype, "") or "") + self._inl["acc"]
            self._inl = None
        return out

    def _pump_fence(self, eof: bool) -> str:
        if self._sol:
            if len(self._buf) < 3 and not eof:
                return ""
            if self._buf.startswith("```"):
                nl = self._buf.find("\n", 3)
                if nl != -1:
                    self._fence = False
                    line = self._buf[: nl + 1]
                    self._buf = self._buf[nl + 1 :]
                    self._sol = True
                    return line
                if eof:
                    self._fence = False
                    line = self._buf
                    self._buf = ""
                    return line
                return ""
            self._sol = False
        nl = self._buf.find("\n")
        if nl != -1:
            chunk = self._buf[: nl + 1]
            self._buf = self._buf[nl + 1 :]
            self._sol = True
            return chunk
        chunk = self._buf
        self._buf = ""
        return chunk

    def _pump_sol(self, eof: bool) -> str:
        b = self._buf

        if b[0] == "\n":
            self._buf = b[1:]
            return "\n"

        if b[0] == "`":
            if len(b) < 3 and not eof:
                return ""
            if b.startswith("```"):
                nl = b.find("\n", 3)
                if nl != -1:
                    self._fence = True
                    line = b[: nl + 1]
                    self._buf = b[nl + 1 :]
                    self._sol = True
                    return line
                if eof:
                    self._buf = ""
                    return b
                return ""
            self._sol = False
            return ""

        if b[0] == ">":
            self._sol = False
            return ""

        if b[0] == "#":
            n = 0
            while n < len(b) and b[n] == "#":
                n += 1
            if n == len(b) and not eof:
                return ""
            if 5 <= n <= 6 and n < len(b) and b[n] == " ":
                self._buf = b[n + 1 :]
                self._sol = False
                return ""
            self._sol = False
            return ""

        if b[0] in " \t":
            if re.match(r"[^ \t]", b) is None and not eof:
                return ""
            self._sol = False
            return ""

        if b[0] in "-*_":
            ch = b[0]
            j = 0
            while j < len(b) and b[j] in (ch, " "):
                j += 1
            if j == len(b) and not eof:
                return ""
            if j == len(b) or b[j] == "\n":
                count = sum(1 for k in range(j) if b[k] == ch)
                if count >= 3:
                    if j < len(b):
                        self._buf = b[j + 1 :]
                        self._sol = True
                        return b[: j + 1]
                    self._buf = ""
                    return b
            self._sol = False
            return ""

        self._sol = False
        return ""

    def _pump_body(self, eof: bool) -> str:
        out = ""
        i = 0
        while i < len(self._buf):
            c = self._buf[i]
            if c == "\n":
                out += self._buf[: i + 1]
                self._buf = self._buf[i + 1 :]
                self._sol = True
                return out
            if c == "!" and i + 1 < len(self._buf) and self._buf[i + 1] == "[":
                out += self._buf[:i]
                self._buf = self._buf[i + 2 :]
                self._inl = {"type": "image", "acc": ""}
                return out
            if c == "~":
                i += 1
                continue
            if c == "*":
                if i + 2 < len(self._buf) and self._buf[i + 1] == "*" and self._buf[i + 2] == "*":
                    out += self._buf[:i]
                    self._buf = self._buf[i + 3 :]
                    self._inl = {"type": "bold3", "acc": ""}
                    return out
                if i + 1 < len(self._buf) and self._buf[i + 1] == "*":
                    i += 2
                    continue
                if i + 1 < len(self._buf) and self._buf[i + 1] not in " \t\n":
                    out += self._buf[:i]
                    self._buf = self._buf[i + 1 :]
                    self._inl = {"type": "italic", "acc": ""}
                    return out
                i += 1
                continue
            if c == "_":
                if i + 2 < len(self._buf) and self._buf[i + 1] == "_" and self._buf[i + 2] == "_":
                    out += self._buf[:i]
                    self._buf = self._buf[i + 3 :]
                    self._inl = {"type": "ubold3", "acc": ""}
                    return out
                if i + 1 < len(self._buf) and self._buf[i + 1] == "_":
                    i += 2
                    continue
                if i + 1 < len(self._buf) and self._buf[i + 1] not in " \t\n":
                    out += self._buf[:i]
                    self._buf = self._buf[i + 1 :]
                    self._inl = {"type": "uitalic", "acc": ""}
                    return out
                i += 1
                continue
            i += 1

        hold = 0
        if not eof:
            if self._buf.endswith("**"):
                hold = 2
            elif self._buf.endswith("__"):
                hold = 2
            elif self._buf.endswith("*"):
                hold = 1
            elif self._buf.endswith("_"):
                hold = 1
            elif self._buf.endswith("!"):
                hold = 1
        out += self._buf[: len(self._buf) - hold] if hold else self._buf
        self._buf = self._buf[-hold:] if hold else ""
        return out

    def _pump_inline(self, _eof: bool) -> str:
        if not self._inl:
            return ""
        self._inl["acc"] += self._buf
        self._buf = ""

        mtype = self._inl["type"]
        acc = self._inl["acc"]

        if mtype == "bold3":
            idx = acc.find("***")
            if idx != -1:
                content = acc[:idx]
                self._buf = acc[idx + 3 :]
                self._inl = None
                if self.contains_cjk(content):
                    return content
                return f"***{content}***"
            return ""

        if mtype == "ubold3":
            idx = acc.find("___")
            if idx != -1:
                content = acc[:idx]
                self._buf = acc[idx + 3 :]
                self._inl = None
                if self.contains_cjk(content):
                    return content
                return f"___{content}___"
            return ""

        if mtype == "italic":
            for j in range(len(acc)):
                if acc[j] == "\n":
                    r = "*" + acc[: j + 1]
                    self._buf = acc[j + 1 :]
                    self._inl = None
                    self._sol = True
                    return r
                if acc[j] == "*":
                    if j + 1 < len(acc) and acc[j + 1] == "*":
                        j += 1
                        continue
                    content = acc[:j]
                    self._buf = acc[j + 1 :]
                    self._inl = None
                    if self.contains_cjk(content):
                        return content
                    return f"*{content}*"
            return ""

        if mtype == "uitalic":
            for j in range(len(acc)):
                if acc[j] == "\n":
                    r = "_" + acc[: j + 1]
                    self._buf = acc[j + 1 :]
                    self._inl = None
                    self._sol = True
                    return r
                if acc[j] == "_":
                    if j + 1 < len(acc) and acc[j + 1] == "_":
                        j += 1
                        continue
                    content = acc[:j]
                    self._buf = acc[j + 1 :]
                    self._inl = None
                    if self.contains_cjk(content):
                        return content
                    return f"_{content}_"
            return ""

        if mtype == "image":
            cb = acc.find("]")
            if cb == -1:
                return ""
            if cb + 1 >= len(acc):
                return ""
            if acc[cb + 1] != "(":
                r = "![" + acc[: cb + 1]
                self._buf = acc[cb + 1 :]
                self._inl = None
                return r
            cp = acc.find(")", cb + 2)
            if cp != -1:
                self._buf = acc[cp + 1 :]
                self._inl = None
                return ""
            return ""

        return ""

    @staticmethod
    def contains_cjk(text: str) -> bool:
        return bool(re.search(r"[\u2E80-\u9FFF\uAC00-\uD7AF\uF900-\uFAFF]", text))
