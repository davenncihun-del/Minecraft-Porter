from __future__ import annotations
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import javalang

RULES_DIR = Path(__file__).parent / "rules"


class SignatureRewriteError(Exception):
    """Raised when a rule matches a call site but the call site's source
    text can't be safely rewritten (unbalanced parens, unexpected shape).
    The planner treats this as a skip-and-report, never a silent edit."""


def _simple_name(fqn_or_name: str) -> str:
    return fqn_or_name.rsplit(".", 1)[-1]


@dataclass
class SignatureRule:
    id: str
    kind: str
    method_name: str
    receiver_types: List[str]
    reason: str
    match_arg_count: Optional[int] = None
    wrapper_constructor: Optional[str] = None
    unwrap_accessors: Optional[List[str]] = None
    direction: str = "forward"
    inverse: Optional[str] = None
    # CONSTRUCTOR_ARG_INJECT: position and value to inject
    inject_position: Optional[int] = None
    inject_value: Optional[str] = None
    # CONSTRUCTOR_ARG_STRIP: list of argument positions to remove
    strip_positions: Optional[List[int]] = None
    # IMPORT_REWRITE: mapping of old import → new import
    import_rewrites: Optional[Dict[str, str]] = None
    # Generic replacement template (for template-based rewrites)
    replacement_template: Optional[Dict[str, str]] = None
    # Auditing Metadata
    confidence: str = "HIGH"
    severity: str = "INFO"
    requires_review: bool = False
    automation_level: str = "FULL_AUTO"
    breaking_risk: str = "NONE"
    version_guard: Optional[str] = None
    loader_guard: Optional[List[str]] = None
    tags: Optional[List[str]] = None
    references: Optional[List[str]] = None
    before_example: Optional[str] = None
    after_example: Optional[str] = None

    @classmethod
    def from_dict(cls, data: dict) -> "SignatureRule":
        kind = data["kind"]
        match_signature = data.get("matchSignature")
        return cls(
            id=data["id"],
            kind=kind,
            method_name=data.get("methodName", ""),
            receiver_types=[_simple_name(t) for t in data.get("receiverTypes", [])],
            reason=data.get("reason", f"{kind} rewrite for {data.get('methodName', kind)}"),
            match_arg_count=len(match_signature) if match_signature else None,
            wrapper_constructor=data.get("wrapperConstructor"),
            unwrap_accessors=data.get("unwrapAccessors"),
            direction=data.get("direction", "forward"),
            inverse=data.get("inverse"),
            inject_position=data.get("injectPosition"),
            inject_value=data.get("injectValue"),
            strip_positions=data.get("stripPositions"),
            import_rewrites=data.get("importRewrites"),
            replacement_template=data.get("replacement"),
            confidence=data.get("confidence", "HIGH"),
            severity=data.get("severity", "INFO"),
            requires_review=data.get("requiresReview", False),
            automation_level=data.get("automationLevel", "FULL_AUTO"),
            breaking_risk=data.get("breakingRisk", "NONE"),
            version_guard=data.get("versionGuard"),
            loader_guard=data.get("loaderGuard"),
            tags=data.get("tags"),
            references=data.get("references"),
            before_example=data.get("beforeExample"),
            after_example=data.get("afterExample"),
        )


def _index_from_position(lines: List[str], line: int, column: int) -> int:
    return sum(len(l) for l in lines[: line - 1]) + (column - 1)


def _position_from_index(lines: List[str], idx: int) -> Tuple[int, int]:
    running = 0
    for line_no, line_text in enumerate(lines, start=1):
        if idx < running + len(line_text):
            return line_no, idx - running + 1
        running += len(line_text)
    raise SignatureRewriteError("index past end of file")


def _find_matching_paren(text: str, open_idx: int) -> int:
    depth = 0
    i = open_idx
    while i < len(text):
        c = text[i]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise SignatureRewriteError("unbalanced parentheses")


def _split_top_level_args(inner_text: str) -> List[str]:
    args: List[str] = []
    current: List[str] = []
    depth = 0
    in_string = False
    in_char = False
    i = 0
    while i < len(inner_text):
        c = inner_text[i]
        if in_string:
            current.append(c)
            if c == "\\" and i + 1 < len(inner_text):
                i += 1
                current.append(inner_text[i])
            elif c == '"':
                in_string = False
        elif in_char:
            current.append(c)
            if c == "'":
                in_char = False
        elif c == '"':
            in_string = True
            current.append(c)
        elif c == "'":
            in_char = True
            current.append(c)
        elif c in "([{":
            depth += 1
            current.append(c)
        elif c in ")]}":
            depth -= 1
            current.append(c)
        elif c == "," and depth == 0:
            args.append("".join(current).strip())
            current = []
        else:
            current.append(c)
        i += 1
    tail = "".join(current).strip()
    if tail:
        args.append(tail)
    return args


@dataclass
class RenderedRewrite:
    line: int
    column: int
    old: str
    new: str


class RuleSet:
    def __init__(self, from_version: str, to_version: str, loader: str, rules: List[SignatureRule]):
        self.from_version = from_version
        self.to_version = to_version
        self.loader = loader
        self.rules = rules

    @classmethod
    def from_json(cls, path: Path) -> "RuleSet":
        data = json.loads(path.read_text(encoding="utf-8"))
        rules = [SignatureRule.from_dict(r) for r in data.get("rules", [])]
        return cls(
            from_version=data.get("fromVersion", "unknown"),
            to_version=data.get("toVersion", "unknown"),
            loader=data.get("loader", "any"),
            rules=rules,
        )

    @classmethod
    def for_versions(cls, from_version: Optional[str], to_version: Optional[str]) -> Optional["RuleSet"]:
        if not from_version or not to_version or not RULES_DIR.exists():
            return None
        candidate = RULES_DIR / f"{from_version}_to_{to_version}.json"
        if candidate.exists():
            return cls.from_json(candidate)
        return None

    def match(self, invocation: "javalang.tree.MethodInvocation", receiver_type: Optional[str]) -> Optional[SignatureRule]:
        if receiver_type is None:
            return None
        receiver_simple = _simple_name(receiver_type)
        arg_count = len(invocation.arguments) if invocation.arguments else 0
        for rule in self.rules:
            if rule.method_name != invocation.member:
                continue
            if receiver_simple not in rule.receiver_types:
                continue
            if rule.match_arg_count is not None:
                if rule.kind == "METHOD_CALL_ARGS_TO_OBJECT":
                    # matchSignature describes the leading args to wrap (e.g. x,y,z);
                    # trailing args (e.g. setBlockState's state/flags) pass through untouched,
                    # so this only requires *at least* that many arguments, not exactly.
                    if arg_count < rule.match_arg_count:
                        continue
                elif rule.match_arg_count != arg_count:
                    continue
            return rule
        return None

    def render(self, invocation, rule: SignatureRule, text: str) -> RenderedRewrite:
        lines = text.splitlines(keepends=True)
        if invocation.position is None:
            raise SignatureRewriteError("no source position available for call site")

        start = _index_from_position(lines, invocation.position.line, invocation.position.column)
        open_paren = text.find("(", start)
        if open_paren == -1:
            raise SignatureRewriteError("could not locate call arguments")
        close_paren = _find_matching_paren(text, open_paren)

        old_span = text[open_paren : close_paren + 1]
        inner = text[open_paren + 1 : close_paren]
        args = _split_top_level_args(inner)

        if rule.kind == "METHOD_CALL_ARGS_TO_OBJECT":
            wrap_count = rule.match_arg_count if rule.match_arg_count is not None else len(args)
            if len(args) < wrap_count:
                raise SignatureRewriteError("argument count does not match rule at render time")
            leading, trailing = args[:wrap_count], args[wrap_count:]
            wrapped = f"new {_simple_name(rule.wrapper_constructor)}({', '.join(leading)})"
            new_span = "(" + ", ".join([wrapped, *trailing]) + ")"
        elif rule.kind == "METHOD_CALL_OBJECT_TO_ARGS":
            if len(args) != 1:
                raise SignatureRewriteError("expected exactly one object argument to unwrap")
            single = args[0]
            accessors = rule.unwrap_accessors or []
            new_span = "(" + ", ".join(f"{single}.{accessor}()" for accessor in accessors) + ")"
        elif rule.kind == "CONSTRUCTOR_ARG_INJECT":
            # Insert a new argument at a specific position
            pos = rule.inject_position if rule.inject_position is not None else len(args)
            value = rule.inject_value or "0"
            new_args = list(args)
            new_args.insert(pos, value)
            new_span = "(" + ", ".join(new_args) + ")"
        elif rule.kind == "CONSTRUCTOR_ARG_STRIP":
            # Remove arguments at specified positions
            positions = set(rule.strip_positions or [])
            new_args = [a for i, a in enumerate(args) if i not in positions]
            new_span = "(" + ", ".join(new_args) + ")"
        elif rule.kind == "IMPORT_REWRITE":
            # Import rewrites are not rendered at the call-site level;
            # they are consumed by the import migrator.
            return RenderedRewrite(line=0, column=0, old="", new="")
        else:
            raise SignatureRewriteError(f"unsupported rule kind: {rule.kind}")

        line, column = _position_from_index(lines, open_paren)
        return RenderedRewrite(line=line, column=column, old=old_span, new=new_span)
