from __future__ import annotations

import ast
import logging
import operator
import re

from telegram import Update
from telegram.constants import ChatType
from telegram.ext import ContextTypes

log = logging.getLogger(__name__)

_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def _pow(left: float, right: float) -> float:
    if abs(right) > 12 or abs(left) > 1_000_000:
        raise ValueError("too large")
    value = left ** right
    if abs(value) > 1e15:
        raise ValueError("too large")
    return value


_OPS[ast.Pow] = _pow

# Skip real dates like 12/05/2024, not sums like 4-1.
DATE = re.compile(r"^\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}$")
HAS_OP = re.compile(r"[+\-*/^×÷]|x", re.I)


def _eval(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(
        node.value, bool
    ):
        return float(node.value)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.operand))
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.left), _eval(node.right))
    raise ValueError("unsupported")


def _normalize(raw: str) -> str | None:
    text = raw.strip().replace("×", "*").replace("÷", "/").replace("−", "-")
    text = text.replace("^", "**")
    text = re.sub(r"(\d)\s*[xX]\s*(\d)", r"\1*\2", text)
    if text.startswith("="):
        text = text[1:].strip()
    if text.endswith("="):
        text = text[:-1].strip()
    if not text or len(text) > 80:
        return None
    if DATE.match(text.replace(" ", "")):
        return None
    if not HAS_OP.search(text):
        return None
    if not re.fullmatch(r"[\d+\-*/().%\s*eE]+", text):
        return None
    return text


def _format(value: float) -> str:
    if abs(value) > 1e15:
        raise ValueError("too large")
    if abs(value - round(value)) < 1e-12:
        return str(int(round(value)))
    text = f"{value:.12g}"
    return text


def calculate(raw: str) -> str | None:
    expr = _normalize(raw)
    if not expr:
        return None
    try:
        tree = ast.parse(expr, mode="eval")
        value = _eval(tree)
        if not isinstance(value, (int, float)) or value != value:  # NaN
            return None
        return _format(float(value))
    except (ValueError, ZeroDivisionError, OverflowError, SyntaxError, TypeError, RecursionError):
        return None


async def on_calc(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if not message or not chat or not user or user.is_bot:
        return
    if chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP, ChatType.PRIVATE):
        return
    text = (message.text or "").strip()
    if not text or text.startswith("/"):
        return
    result = calculate(text)
    if result is None:
        return
    try:
        await message.reply_text(result)
    except Exception:
        log.exception("calc reply failed")
