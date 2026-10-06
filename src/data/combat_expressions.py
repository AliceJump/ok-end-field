"""Small typed numeric expressions for authored combat transitions; no eval/prose."""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass


class MissingCombatInput(ValueError):
    pass


@dataclass(frozen=True)
class CombatExpression:
    operation: str
    operands: tuple[CombatExpression | float | str, ...]

    def evaluate(self, inputs):
        if self.operation == "input":
            key, = self.operands
            if key not in inputs:
                raise MissingCombatInput(f"Missing combat input: {key}")
            value = inputs[key]
        else:
            if self.operation in {"all", "any"}:
                wanted = self.operation == "any"
                for operand in self.operands:
                    value = operand.evaluate(inputs) if isinstance(operand, CombatExpression) else operand
                    if bool(value) == wanted:
                        return float(wanted)
                return float(not wanted)
            args = [x.evaluate(inputs) if isinstance(x, CombatExpression) else x for x in self.operands]
            if self.operation == "literal":
                value, = args
            elif self.operation == "add":
                value = sum(args)
            elif self.operation == "multiply":
                value = math.prod(args)
            elif self.operation == "divide":
                if args[1] == 0:
                    raise MissingCombatInput("Zero combat formula divisor")
                value = args[0] / args[1]
            elif self.operation == "min":
                value = min(args)
            elif self.operation == "max":
                value = max(args)
            elif self.operation == "floor":
                value = math.floor(args[0])
            elif self.operation == "float32":
                try:
                    value = struct.unpack("<f", struct.pack("<f", args[0]))[0]
                except OverflowError as error:
                    raise MissingCombatInput("Native single-precision value overflow") from error
            elif self.operation == "ceil":
                value = math.ceil(args[0])
            elif self.operation == "round":
                value = round(args[0])
            elif self.operation == "table":
                index = args[0]
                if index != int(index) or not 1 <= index < len(args):
                    raise MissingCombatInput(f"Combat table index outside captured rows: {index}")
                value = args[int(index)]
            elif self.operation == "lt":
                value = float(args[0] < args[1])
            elif self.operation == "le":
                value = float(args[0] <= args[1])
            elif self.operation == "gt":
                value = float(args[0] > args[1])
            elif self.operation == "ge":
                value = float(args[0] >= args[1])
            elif self.operation == "eq":
                value = float(args[0] == args[1])
            elif self.operation == "mask_any":
                if any(a != int(a) for a in args):
                    raise MissingCombatInput("Non-integer combat mask")
                value = float(bool(int(args[0]) & int(args[1])))
            elif self.operation == "all":
                value = float(all(args))
            elif self.operation == "any":
                value = float(any(args))
            elif self.operation == "not":
                value = float(not args[0])
            else:
                raise ValueError(f"Unknown combat expression operation: {self.operation}")
        if not isinstance(value, (int, float)) or not math.isfinite(value):
            raise MissingCombatInput("Non-finite or non-numeric combat expression")
        return float(value)


def combat_input(key):
    return CombatExpression("input", (key,))
