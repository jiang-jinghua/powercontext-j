# -*- coding: utf-8 -*-
"""极简 YAML 发射器 + 回环校验器

只覆盖本项目用到的 YAML 子集：块映射 / 块序列 / 单引号标量 / 字面块标量(| 与 |-)。
emit() 生成 DSL 文本，parse() 将其解析回 Python 对象并用于 round-trip 校验，
保证 build_dsl.py 产出的 .yml 在语法上自洽。
"""
import re

_BOOL_LIKE = {"null", "true", "false", "yes", "no", "on", "off", "~"}
_NUM_RE = re.compile(r"^[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?$")
_SPECIAL_FIRST = "-?:,[]{}#&*!|>'\"%@`"


def _needs_quote(s: str) -> bool:
    if s == "":
        return True
    if s != s.strip():
        return True
    if s[0] in _SPECIAL_FIRST:
        return True
    if ": " in s or " #" in s or s.endswith(":") or "#" in s:
        return True
    if s.lower() in _BOOL_LIKE:
        return True
    if _NUM_RE.match(s):
        return True
    if "\t" in s:
        return True
    return False


def _render_scalar(v, indent: int) -> str:
    if v is None:
        return "null"
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, (list, dict)):
        return "[]" if isinstance(v, list) else "{}"
    if isinstance(v, (int, float)):
        return repr(v)
    s = str(v)
    if "\n" in s:
        ends_nl = s.endswith("\n")
        body = s[:-1] if ends_nl else s
        head = "|" if ends_nl else "|-"
        lines = [head]
        for ln in body.split("\n"):
            lines.append((" " * indent + ln) if ln else "")
        return "\n".join(lines)
    if _needs_quote(s):
        return "'" + s.replace("'", "''") + "'"
    return s


def emit_dict(d: dict, indent: int) -> list:
    pad = " " * indent
    lines = []
    for k, v in d.items():
        if isinstance(v, dict) and v:
            lines.append("%s%s:" % (pad, k))
            lines.extend(emit_dict(v, indent + 2))
        elif isinstance(v, list) and v:
            lines.append("%s%s:" % (pad, k))
            lines.extend(emit_list(v, indent + 2))
        else:
            lines.append("%s%s: %s" % (pad, k, _render_scalar(v, indent + 2)))
    return lines


def emit_list(lst: list, indent: int) -> list:
    pad = " " * indent
    lines = []
    for item in lst:
        if isinstance(item, dict):
            if not item:
                lines.append(pad + "- {}")
                continue
            body = emit_dict(item, indent + 2)
            lines.append(pad + "- " + body[0][indent + 2:])
            lines.extend(body[1:])
        elif isinstance(item, list):
            if not item:
                lines.append(pad + "- []")
                continue
            body = emit_list(item, indent + 2)
            lines.append(pad + "- " + body[0][indent + 2:])
            lines.extend(body[1:])
        else:
            lines.append(pad + "- " + _render_scalar(item, indent + 2))
    return lines


def dumps(data) -> str:
    if isinstance(data, dict):
        return "\n".join(emit_dict(data, 0)) + "\n"
    if isinstance(data, list):
        return "\n".join(emit_list(data, 0)) + "\n"
    return _render_scalar(data, 0) + "\n"


# ---------------------------------------------------------------- 解析（校验用）

def _parse_scalar(s: str):
    s = s.strip()
    if s.startswith("'") and s.endswith("'") and len(s) >= 2:
        return s[1:-1].replace("''", "'")
    if s == "[]" :
        return []
    if s == "{}":
        return {}
    if s == "null" or s == "~" or s == "":
        return None
    low = s.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    if _NUM_RE.match(s):
        try:
            return int(s)
        except ValueError:
            return float(s)
    return s


def _split_key(line: str):
    idx = line.find(": ")
    if idx == -1:
        if line.endswith(":"):
            return line[:-1], ""
        raise ValueError("无法解析行: %r" % line)
    return line[:idx], line[idx + 2:]


def _collect_block(lines, start, min_indent):
    """收集字面块标量内容，返回 (text, next_index)。"""
    block_indent = None
    buf = []
    i = start
    while i < len(lines):
        ln = lines[i]
        if ln.strip() == "":
            buf.append("")
            i += 1
            continue
        ind = len(ln) - len(ln.lstrip(" "))
        if ind <= min_indent:
            break
        if block_indent is None:
            block_indent = ind
        buf.append(ln[block_indent:] if len(ln) >= block_indent else ln.lstrip(" "))
        i += 1
    while buf and buf[-1] == "":
        buf.pop()
    return "\n".join(buf), i


def _parse_block(lines, start, indent):
    """解析一个块（映射或序列），返回 (value, next_index)。"""
    if start >= len(lines):
        return None, start
    first = lines[start]
    ind = len(first) - len(first.lstrip(" "))
    if first.lstrip().startswith("- ") or first.rstrip() == "-":
        return _parse_seq(lines, start, ind)
    return _parse_map(lines, start, ind)


def _parse_seq(lines, start, indent):
    result = []
    i = start
    while i < len(lines):
        ln = lines[i]
        if ln.strip() == "":
            i += 1
            continue
        ind = len(ln) - len(ln.lstrip(" "))
        if ind < indent or not ln.lstrip().startswith("- "):
            break
        rest = ln.lstrip()[2:]
        if rest == "":
            val, i = _parse_block(lines, i + 1, indent + 2)
            result.append(val)
            continue
        child_indent = indent + (len(ln) - len(ln.lstrip(" ")) - indent) + 2
        # "- key: ..." / "- key:" -> 内联开始的映射
        if ": " in rest or rest.endswith(":"):
            sub_lines = [" " * (indent + 2) + rest]
            i += 1
            while i < len(lines):
                nxt = lines[i]
                if nxt.strip() == "":
                    sub_lines.append("")
                    i += 1
                    continue
                nind = len(nxt) - len(nxt.lstrip(" "))
                if nind <= indent:
                    break
                sub_lines.append(nxt)
                i += 1
            val, _ = _parse_map(sub_lines, 0, indent + 2)
            result.append(val)
        else:
            result.append(_parse_scalar(rest))
            i += 1
    return result, i


def _parse_map(lines, start, indent):
    result = {}
    i = start
    while i < len(lines):
        ln = lines[i]
        if ln.strip() == "":
            i += 1
            continue
        ind = len(ln) - len(ln.lstrip(" "))
        if ind != indent or ln.lstrip().startswith("- "):
            break
        key, inline = _split_key(ln.strip())
        if inline == "":
            val, i2 = _parse_block(lines, i + 1, indent + 2)
            result[key] = val
            i = i2
        elif inline.startswith("|") and (inline == "|" or inline == "|-"):
            text, i2 = _collect_block(lines, i + 1, indent)
            result[key] = text + ("\n" if inline == "|" else "")
            i = i2
        else:
            result[key] = _parse_scalar(inline)
            i += 1
    return result, i


def loads(text: str):
    lines = text.rstrip("\n").split("\n")
    val, _ = _parse_block(lines, 0, 0)
    return val
