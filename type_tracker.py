from __future__ import annotations
from typing import Dict, Optional

import javalang


class LocalTypeTracker:
    """
    Approximate static-type resolver for javalang ASTs.

    javalang builds a syntax tree only — it does not resolve symbols against
    a classpath the way JavaSymbolSolver does for JavaParser. This tracker
    fills that gap just enough for signature-rewrite rules to work safely:
    it indexes field declarations, method parameters, local variable
    declarations, enhanced-for variables, and catch-clause parameters, then
    maps every MethodInvocation node in a method body to the symbol table
    that was visible at that point.

    This is deliberately conservative. If a variable's declared type can't
    be determined, `resolve()` returns None, and callers should treat that
    as "do not rewrite" rather than guessing — the same posture the
    JavaParser/JavaSymbolSolver engine takes on UnsolvedSymbolException.
    """

    def __init__(self, tree: javalang.tree.CompilationUnit):
        self.tree = tree
        self.field_types: Dict[str, str] = {}
        self.invocation_scope: Dict[int, Dict[str, str]] = {}
        self._build()

    def _type_name(self, type_node) -> Optional[str]:
        if type_node is None:
            return None
        name = getattr(type_node, "name", None)
        if isinstance(name, list):
            return ".".join(name)
        return name

    def _build(self) -> None:
        if self.tree is None:
            return

        for _, class_decl in self.tree.filter(javalang.tree.ClassDeclaration):
            for field_decl in getattr(class_decl, "fields", []) or []:
                type_name = self._type_name(field_decl.type)
                if type_name is None:
                    continue
                for declarator in field_decl.declarators:
                    self.field_types[declarator.name] = type_name

        for node_type in (javalang.tree.MethodDeclaration, javalang.tree.ConstructorDeclaration):
            for _, method_node in self.tree.filter(node_type):
                self._index_method(method_node)

    def _index_method(self, method_node) -> None:
        symtable: Dict[str, str] = dict(self.field_types)

        for param in getattr(method_node, "parameters", None) or []:
            type_name = self._type_name(param.type)
            if type_name:
                symtable[param.name] = type_name

        if getattr(method_node, "body", None):
            for _, decl in method_node.filter(javalang.tree.LocalVariableDeclaration):
                type_name = self._type_name(decl.type)
                if type_name is None:
                    continue
                for declarator in decl.declarators:
                    symtable[declarator.name] = type_name

            for _, decl in method_node.filter(javalang.tree.TryResource):
                type_name = self._type_name(decl.type)
                if type_name:
                    symtable[decl.name] = type_name

            for _, decl in method_node.filter(javalang.tree.CatchClauseParameter):
                types = getattr(decl, "types", None)
                if types:
                    symtable[decl.name] = types[0]

            for _, decl in method_node.filter(javalang.tree.VariableDeclaration):
                # Enhanced-for and some declaration forms surface here too.
                type_name = self._type_name(getattr(decl, "type", None))
                if type_name is None:
                    continue
                for declarator in getattr(decl, "declarators", []) or []:
                    symtable[declarator.name] = type_name

            for _, inv in method_node.filter(javalang.tree.MethodInvocation):
                self.invocation_scope[id(inv)] = symtable

    def resolve(self, invocation_node, qualifier: Optional[str]) -> Optional[str]:
        if not qualifier or qualifier == "this":
            return None
        scope = self.invocation_scope.get(id(invocation_node))
        if scope and qualifier in scope:
            return scope[qualifier]
        return self.field_types.get(qualifier)
