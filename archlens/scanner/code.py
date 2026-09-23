"""Source-code analysis: imports, entrypoints, HTTP routes and call graphs.

Python gets a real AST walk. Everything else gets targeted regexes - a full
parser per language is not worth the dependency weight here, and the regexes
only need to be good enough to find imports and route decorators, which are
syntactically boring in every language we care about.
"""

from __future__ import annotations

import ast
import contextlib
import re
from pathlib import Path

from ..models import CodeEntity, Route

# --------------------------------------------------------------------------- #
# Python (AST)
# --------------------------------------------------------------------------- #

# Decorator attribute names that indicate an HTTP route.
_HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "route", "websocket"}


class _PythonVisitor(ast.NodeVisitor):
    """Collects imports, definitions, routes and call edges from one module."""

    def __init__(self, rel_path: str) -> None:
        self.rel_path = rel_path
        self.imports: list[str] = []
        self.entities: list[CodeEntity] = []
        self.routes: list[Route] = []
        self._class_stack: list[str] = []
        # Router variables created via APIRouter()/Blueprint() so we can
        # recognise `@router.get(...)` as a route even when the app object
        # is named something unusual.
        self._router_names: set[str] = {"app", "router", "api", "bp", "blueprint"}

    # -- imports -----------------------------------------------------------
    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.imports.append(alias.name)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module and node.level == 0:
            self.imports.append(node.module)
        elif node.module:
            # Relative import - record it as a local module reference.
            self.imports.append("." * node.level + node.module)
        self.generic_visit(node)

    # -- definitions -------------------------------------------------------
    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        decorators = [self._name_of(d) for d in node.decorator_list]
        self.entities.append(CodeEntity(
            name=node.name, kind="class", file=self.rel_path, line=node.lineno,
            decorators=[d for d in decorators if d],
            docstring=_short_docstring(ast.get_docstring(node)),
            calls=sorted({b for b in (self._name_of(base) for base in node.bases) if b}),
        ))
        self._class_stack.append(node.name)
        self.generic_visit(node)
        self._class_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._handle_function(node, is_async=False)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._handle_function(node, is_async=True)

    def _handle_function(self, node, is_async: bool) -> None:
        qualified = ".".join([*self._class_stack, node.name])
        decorators = [d for d in (self._name_of(d) for d in node.decorator_list) if d]

        self.entities.append(CodeEntity(
            name=qualified,
            kind="method" if self._class_stack else "function",
            file=self.rel_path, line=node.lineno,
            decorators=decorators,
            docstring=_short_docstring(ast.get_docstring(node)),
            calls=sorted(_collect_calls(node))[:24],
            is_async=is_async,
        ))

        for dec in node.decorator_list:
            route = self._route_from_decorator(dec, qualified, node.lineno)
            if route:
                self.routes.append(route)

        self.generic_visit(node)

    # -- assignment: catch `router = APIRouter()` --------------------------
    def visit_Assign(self, node: ast.Assign) -> None:
        if isinstance(node.value, ast.Call):
            callee = self._name_of(node.value.func) or ""
            if callee.split(".")[-1] in {"APIRouter", "Blueprint", "FastAPI", "Flask"}:
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        self._router_names.add(target.id)
        self.generic_visit(node)

    # -- helpers -----------------------------------------------------------
    def _route_from_decorator(self, dec: ast.expr, handler: str, line: int) -> Route | None:
        """Turn `@app.get("/x")` or `@app.route("/x", methods=["POST"])` into a Route."""
        if not isinstance(dec, ast.Call):
            return None
        func = dec.func
        if not isinstance(func, ast.Attribute):
            return None
        attr = func.attr.lower()
        if attr not in _HTTP_METHODS:
            return None
        owner = self._name_of(func.value) or ""
        if owner.split(".")[0] not in self._router_names:
            return None

        path = ""
        if dec.args and isinstance(dec.args[0], ast.Constant) and isinstance(dec.args[0].value, str):
            path = dec.args[0].value

        method = attr.upper()
        if attr == "route":
            method = "GET"
            for kw in dec.keywords:
                if kw.arg == "methods" and isinstance(kw.value, ast.List | ast.Tuple):
                    methods = [
                        el.value for el in kw.value.elts
                        if isinstance(el, ast.Constant) and isinstance(el.value, str)
                    ]
                    if methods:
                        method = "/".join(m.upper() for m in methods)
        elif attr == "websocket":
            method = "WS"

        if not path:
            return None
        framework = "FastAPI" if attr in {"get", "post", "put", "patch", "delete", "websocket"} else "Flask"
        return Route(method=method, path=path, handler=handler,
                     file=self.rel_path, line=line, framework=framework)

    @staticmethod
    def _name_of(node: ast.expr | None) -> str | None:
        """Best-effort dotted name for a Name/Attribute/Call node."""
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            base = _PythonVisitor._name_of(node.value)
            return f"{base}.{node.attr}" if base else node.attr
        if isinstance(node, ast.Call):
            return _PythonVisitor._name_of(node.func)
        return None


def _collect_calls(node: ast.AST) -> set[str]:
    """Every function name invoked inside a definition, for the call graph."""
    calls: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            name = _PythonVisitor._name_of(child.func)
            if name and not name.startswith("_"):
                calls.add(name)
    return calls


def _short_docstring(doc: str | None) -> str | None:
    if not doc:
        return None
    first = doc.strip().split("\n\n", 1)[0].replace("\n", " ").strip()
    return first[:200] if first else None


def analyse_python(rel_path: str, text: str) -> tuple[list[str], list[CodeEntity], list[Route]]:
    """Parse a Python module. Returns ([imports], [entities], [routes])."""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, RecursionError):
        # A file we cannot parse still contributes via the regex probes.
        return [], [], []
    visitor = _PythonVisitor(rel_path)
    # A deeply nested module can blow the stack mid-walk; keep whatever the
    # visitor collected before that rather than losing the whole file.
    with contextlib.suppress(RecursionError):
        visitor.visit(tree)
    return visitor.imports, visitor.entities, visitor.routes


# --------------------------------------------------------------------------- #
# Django URL patterns - a separate shape from decorators
# --------------------------------------------------------------------------- #

_DJANGO_URL = re.compile(
    r"""\b(?:path|re_path|url)\s*\(\s*r?['"](?P<path>[^'"]*)['"]\s*,\s*(?P<view>[\w.]+)""",
)


def analyse_django_urls(rel_path: str, text: str) -> list[Route]:
    if "urlpatterns" not in text:
        return []
    return [
        Route(method="ANY", path="/" + m.group("path").lstrip("/"),
              handler=m.group("view"), file=rel_path, framework="Django")
        for m in _DJANGO_URL.finditer(text)
    ]


# --------------------------------------------------------------------------- #
# JavaScript / TypeScript
# --------------------------------------------------------------------------- #

_JS_IMPORT = re.compile(
    r"""(?:^|\n)\s*(?:import\s+(?:[\w*{},\s]+\s+from\s+)?|export\s+[\w*{},\s]+\s+from\s+)"""
    r"""['"](?P<mod>[^'"]+)['"]""",
)
_JS_REQUIRE = re.compile(r"""require\s*\(\s*['"](?P<mod>[^'"]+)['"]\s*\)""")
_JS_DYNAMIC_IMPORT = re.compile(r"""\bimport\s*\(\s*['"](?P<mod>[^'"]+)['"]\s*\)""")

_EXPRESS_ROUTE = re.compile(
    r"""\b(?P<owner>app|router|api|server)\s*\.\s*"""
    r"""(?P<method>get|post|put|patch|delete|all|use)\s*\(\s*['"`](?P<path>[^'"`]+)['"`]""",
)
_NEST_ROUTE = re.compile(
    r"""@(?P<method>Get|Post|Put|Patch|Delete)\s*\(\s*(?:['"`](?P<path>[^'"`]*)['"`])?\s*\)""",
)
_JS_FUNC = re.compile(
    r"""(?:^|\n)\s*(?:export\s+)?(?:default\s+)?"""
    r"""(?:async\s+)?(?:function\s+(?P<fn>\w+)|class\s+(?P<cls>\w+)"""
    r"""|(?:const|let|var)\s+(?P<arrow>\w+)\s*(?::\s*[^=]+)?=\s*(?:async\s*)?\()""",
)


def analyse_javascript(rel_path: str, text: str) -> tuple[list[str], list[CodeEntity], list[Route]]:
    imports = [m.group("mod") for m in _JS_IMPORT.finditer(text)]
    imports += [m.group("mod") for m in _JS_REQUIRE.finditer(text)]
    imports += [m.group("mod") for m in _JS_DYNAMIC_IMPORT.finditer(text)]

    entities: list[CodeEntity] = []
    for match in _JS_FUNC.finditer(text):
        name = match.group("fn") or match.group("cls") or match.group("arrow")
        if not name:
            continue
        entities.append(CodeEntity(
            name=name,
            kind="class" if match.group("cls") else "function",
            file=rel_path,
            line=text.count("\n", 0, match.start()) + 1,
        ))

    routes: list[Route] = []
    for match in _EXPRESS_ROUTE.finditer(text):
        method = match.group("method").upper()
        if method == "USE":  # middleware mount, not an endpoint
            continue
        routes.append(Route(
            method="ANY" if method == "ALL" else method,
            path=match.group("path"), handler=match.group("owner"),
            file=rel_path, line=text.count("\n", 0, match.start()) + 1,
            framework="Express",
        ))
    for match in _NEST_ROUTE.finditer(text):
        routes.append(Route(
            method=match.group("method").upper(),
            path="/" + (match.group("path") or "").lstrip("/"),
            handler=rel_path.rsplit("/", 1)[-1],
            file=rel_path, line=text.count("\n", 0, match.start()) + 1,
            framework="NestJS",
        ))

    return imports, entities, routes


# --------------------------------------------------------------------------- #
# Java / Kotlin (Spring)
# --------------------------------------------------------------------------- #

_JAVA_IMPORT = re.compile(r"^\s*import\s+(?:static\s+)?(?P<mod>[\w.]+)", re.M)
_SPRING_CLASS_MAPPING = re.compile(r"""@RequestMapping\s*\(\s*(?:value\s*=\s*)?['"](?P<path>[^'"]*)['"]""")
_SPRING_METHOD = re.compile(
    r"""@(?P<kind>Get|Post|Put|Patch|Delete|Request)Mapping\s*\("""
    r"""\s*(?:(?:value|path)\s*=\s*)?['"](?P<path>[^'"]*)['"]""",
)
_JAVA_TYPE = re.compile(r"^\s*(?:public|private|protected)?\s*(?:final\s+|abstract\s+)?"
                        r"(?:class|interface|record|enum)\s+(?P<name>\w+)", re.M)


def analyse_java(rel_path: str, text: str) -> tuple[list[str], list[CodeEntity], list[Route]]:
    imports = [m.group("mod") for m in _JAVA_IMPORT.finditer(text)]

    entities = [
        CodeEntity(name=m.group("name"), kind="class", file=rel_path,
                   line=text.count("\n", 0, m.start()) + 1)
        for m in _JAVA_TYPE.finditer(text)
    ]

    base = ""
    class_mapping = _SPRING_CLASS_MAPPING.search(text)
    if class_mapping and "@RestController" in text or "@Controller" in text:
        base = class_mapping.group("path") if class_mapping else ""

    routes: list[Route] = []
    for match in _SPRING_METHOD.finditer(text):
        kind = match.group("kind")
        path = (base.rstrip("/") + "/" + match.group("path").lstrip("/")) or "/"
        routes.append(Route(
            method="ANY" if kind == "Request" else kind.upper(),
            path="/" + path.lstrip("/"),
            handler=entities[0].name if entities else rel_path,
            file=rel_path, line=text.count("\n", 0, match.start()) + 1,
            framework="Spring",
        ))
    return imports, entities, routes


# --------------------------------------------------------------------------- #
# Go
# --------------------------------------------------------------------------- #

_GO_IMPORT_BLOCK = re.compile(r"import\s*\((?P<body>.*?)\)", re.S)
_GO_IMPORT_LINE = re.compile(r"""['"](?P<mod>[^'"]+)['"]""")
_GO_SINGLE_IMPORT = re.compile(r"""^\s*import\s+(?:\w+\s+)?['"](?P<mod>[^'"]+)['"]""", re.M)
_GO_FUNC = re.compile(r"^\s*func\s+(?:\([^)]*\)\s*)?(?P<name>\w+)\s*\(", re.M)
_GO_ROUTE = re.compile(
    r"""\.(?:HandleFunc|Handle|GET|POST|PUT|PATCH|DELETE)\s*\(\s*['"](?P<path>[^'"]+)['"]""",
)


def analyse_go(rel_path: str, text: str) -> tuple[list[str], list[CodeEntity], list[Route]]:
    imports: list[str] = []
    for block in _GO_IMPORT_BLOCK.finditer(text):
        imports += [m.group("mod") for m in _GO_IMPORT_LINE.finditer(block.group("body"))]
    imports += [m.group("mod") for m in _GO_SINGLE_IMPORT.finditer(text)]

    entities = [
        CodeEntity(name=m.group("name"), kind="function", file=rel_path,
                   line=text.count("\n", 0, m.start()) + 1)
        for m in _GO_FUNC.finditer(text)
    ]
    routes = [
        Route(method="ANY", path=m.group("path"), handler=rel_path,
              file=rel_path, line=text.count("\n", 0, m.start()) + 1, framework="Go")
        for m in _GO_ROUTE.finditer(text)
    ]
    return imports, entities, routes


# --------------------------------------------------------------------------- #
# C# (ASP.NET)
# --------------------------------------------------------------------------- #

_CS_USING = re.compile(r"^\s*using\s+(?:static\s+)?(?P<mod>[\w.]+)\s*;", re.M)
_CS_TYPE = re.compile(r"^\s*(?:public|internal)?\s*(?:sealed\s+|abstract\s+|partial\s+)*"
                      r"(?:class|record|interface)\s+(?P<name>\w+)", re.M)
_CS_ROUTE = re.compile(
    r"""\[(?:Http(?P<method>Get|Post|Put|Patch|Delete)|Route)"""
    r"""\s*\(\s*['"](?P<path>[^'"]*)['"]\s*\)\]""",
)


def analyse_csharp(rel_path: str, text: str) -> tuple[list[str], list[CodeEntity], list[Route]]:
    imports = [m.group("mod") for m in _CS_USING.finditer(text)]
    entities = [
        CodeEntity(name=m.group("name"), kind="class", file=rel_path,
                   line=text.count("\n", 0, m.start()) + 1)
        for m in _CS_TYPE.finditer(text)
    ]
    routes = [
        Route(method=(m.group("method") or "ANY").upper(),
              path="/" + m.group("path").lstrip("/"),
              handler=entities[0].name if entities else rel_path,
              file=rel_path, line=text.count("\n", 0, m.start()) + 1, framework="ASP.NET")
        for m in _CS_ROUTE.finditer(text)
    ]
    return imports, entities, routes


# --------------------------------------------------------------------------- #
# Dispatch
# --------------------------------------------------------------------------- #

def analyse(rel_path: str, language: str, text: str) -> tuple[list[str], list[CodeEntity], list[Route]]:
    """Language-dispatched analysis. Returns ([imports], [entities], [routes])."""
    if language == "Python":
        imports, entities, routes = analyse_python(rel_path, text)
        routes += analyse_django_urls(rel_path, text)
        return imports, entities, routes
    if language in {"JavaScript", "TypeScript", "Vue", "Svelte"}:
        return analyse_javascript(rel_path, text)
    if language in {"Java", "Kotlin", "Scala"}:
        return analyse_java(rel_path, text)
    if language == "Go":
        return analyse_go(rel_path, text)
    if language in {"C#", "F#"}:
        return analyse_csharp(rel_path, text)
    return [], [], []


# --------------------------------------------------------------------------- #
# Entrypoint detection
# --------------------------------------------------------------------------- #

_ENTRYPOINT_NAMES = {
    "main.py", "app.py", "wsgi.py", "asgi.py", "manage.py", "__main__.py",
    "server.py", "run.py", "cli.py", "worker.py", "handler.py", "lambda_function.py",
    "index.js", "index.ts", "main.js", "main.ts", "server.js", "server.ts",
    "app.js", "app.ts", "main.go", "program.cs", "startup.cs", "application.java",
}


def is_entrypoint(rel_path: str, text: str, language: str) -> bool:
    name = Path(rel_path).name.lower()
    if name in _ENTRYPOINT_NAMES:
        return True
    if language == "Python" and 'if __name__ == "__main__"' in text:
        return True
    if language == "Python" and "if __name__ == '__main__'" in text:
        return True
    if language == "Go" and re.search(r"^\s*func\s+main\s*\(\s*\)", text, re.M):
        return True
    return language in {"Java", "Kotlin"} and "public static void main" in text
