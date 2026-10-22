"""Safe arithmetic evaluation.

Deliberately does NOT use ``eval()``. The previous README example used
``eval(expr, {"__builtins__": {}})``, which is trivially escaped via
``().__class__.__base__.__subclasses__()`` and is a remote code execution
vector on any machine that copies it.

This implementation only accepts numeric literals and arithmetic operators,
so there is nothing to escape.
"""

import ast
import math
import operator
from typing import Any, Dict, Optional

from backend.skills.base import BaseSkill, SkillContext

_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

_UNARY_OPS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}

# Names the expression is permitted to reference.
SAFE_NAMES: Dict[str, Any] = {
    "pi": math.pi,
    "e": math.e,
    "tau": math.tau,
    "inf": math.inf,
    "sqrt": math.sqrt,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "asin": math.asin,
    "acos": math.acos,
    "atan": math.atan,
    "atan2": math.atan2,
    "log": math.log,
    "log10": math.log10,
    "log2": math.log2,
    "exp": math.exp,
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "pow": pow,
    "floor": math.floor,
    "ceil": math.ceil,
    "factorial": math.factorial,
    "hypot": math.hypot,
    "degrees": math.degrees,
    "radians": math.radians,
}

# Guard against absurd computations like 9**9**9.
MAX_EXPONENT = 1000


class CalculatorSkill(BaseSkill):
    name: str = "calculator"
    display_name: str = "Safe Calculator"
    enabled_by_default: bool = False  # Opt-in: switch it on in the Tools modal.
    description: str = (
        "Evaluate a mathematical expression exactly. Call this for any arithmetic, "
        "unit conversion, percentages, trigonometry or roots so results are exact "
        "rather than estimated."
    )
    parameters: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "expression": {
                "type": "string",
                "description": "Expression to evaluate, e.g. '42 * (3 + 4)' or 'sqrt(2)**2' or 'sin(pi/6)'.",
            }
        },
        "required": ["expression"],
    }

    async def execute(
        self, ctx: Optional[SkillContext] = None, expression: str = "", **kwargs
    ) -> Dict[str, Any]:
        expression = (expression or "").strip()
        if not expression:
            return {"error": "No expression provided."}

        try:
            tree = ast.parse(expression, mode="eval")
        except SyntaxError as e:
            return {"error": f"Could not parse expression: {e.msg}"}

        try:
            value = self._eval_node(tree.body)
        except ZeroDivisionError:
            return {"error": "Division by zero.", "expression": expression}
        except (ValueError, OverflowError) as e:
            return {"error": f"Invalid operation: {e}", "expression": expression}
        except _Unsafe as e:
            return {"error": f"Unsupported expression: {e}", "expression": expression}

        if isinstance(value, float):
            if math.isinf(value):
                return {"error": "Result is infinite.", "expression": expression}
            if math.isnan(value):
                return {"error": "Result is undefined (NaN).", "expression": expression}
            if value.is_integer() and abs(value) < 1e15:
                value = int(value)

        return {"expression": expression, "result": value}

    def _eval_node(self, node: ast.AST) -> Any:
        # Only literal numbers.
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
                raise _Unsafe("only numeric literals are allowed")
            return node.value

        if isinstance(node, ast.UnaryOp):
            op = _UNARY_OPS.get(type(node.op))
            if not op:
                raise _Unsafe(f"unary operator {type(node.op).__name__}")
            return op(self._eval_node(node.operand))

        if isinstance(node, ast.BinOp):
            op = _BIN_OPS.get(type(node.op))
            if not op:
                raise _Unsafe(f"operator {type(node.op).__name__}")
            left = self._eval_node(node.left)
            right = self._eval_node(node.right)

            if isinstance(node.op, ast.Pow):
                if abs(right) > MAX_EXPONENT:
                    raise _Unsafe(f"exponent larger than {MAX_EXPONENT}")
                if isinstance(left, float) or isinstance(right, float):
                    if abs(left) > 1e6 or abs(right) > 1e6:
                        raise _Unsafe("magnitude too large to exponentiate")
            return op(left, right)

        if isinstance(node, ast.Name):
            if node.id in SAFE_NAMES:
                return SAFE_NAMES[node.id]
            raise _Unsafe(f"name '{node.id}'")

        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name):
                raise _Unsafe("only named functions may be called")
            fn = SAFE_NAMES.get(node.func.id)
            if fn is None or not callable(fn):
                raise _Unsafe(f"function '{node.func.id}'")
            if node.keywords:
                raise _Unsafe("keyword arguments are not supported")
            return fn(*[self._eval_node(a) for a in node.args])

        raise _Unsafe(f"syntax element {type(node).__name__}")


class _Unsafe(Exception):
    """Raised when an expression uses a construct we refuse to evaluate."""