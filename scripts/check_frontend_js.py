#!/usr/bin/env python3
"""Static verification for the frontend ES modules (uses esprima).

Checks, for every .js module under web/static:
  1. syntax validity (esprima parse);
  2. import/export cross-consistency (every named import must exist in
     the referenced module's export list);
  3. no free identifier that is neither declared in the module nor
     imported nor a known browser global — this catches the classic
     "used a helper but forgot to import it" mistake introduced by the
     single-file -> modules split.

Usage: python3 scripts/check_frontend_js.py [root]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import esprima

STATIC = Path(__file__).resolve().parent.parent / "src" / "payload" / "web" / "static"

# Browser/ECMAScript globals the modules may legitimately reference
# without importing. Kept deliberately tight (the code style is plain
# JS, no framework magic).
GLOBALS = {
    "window", "document", "location", "localStorage", "navigator", "history",
    "fetch", "EventSource", "FormData", "URLSearchParams", "atob", "btoa",
    "Blob", "File", "FileReader", "URL", "AbortController",
    "JSON", "Promise", "Math", "Number", "String", "Boolean", "Array", "Object",
    "Set", "Map", "WeakMap", "Symbol", "Date", "RegExp", "Error", "TypeError",
    "RangeError", "ReferenceError", "SyntaxError", "parseInt", "parseFloat",
    "isNaN", "isFinite", "encodeURIComponent", "decodeURIComponent", "encodeURI",
    "decodeURI", "setTimeout", "clearTimeout", "setInterval", "clearInterval",
    "requestAnimationFrame", "cancelAnimationFrame", "queueMicrotask",
    "console", "Intl", "structuredClone", "performance", "globalThis", "undefined",
    "Infinity", "NaN", "TextEncoder", "TextDecoder", "DOMParser", "DOMException",
    "CSS", "matchMedia", "CustomEvent",
}


def scan_pattern(p, declared):
    """Adds every name bound by a declaration pattern to 'declared'."""
    if not isinstance(p, dict):
        return
    t = p.get("type")
    if t == "Identifier":
        declared.add(p["name"])
    elif t == "ObjectPattern":
        for prop in p.get("properties", []):
            if isinstance(prop, dict) and prop.get("type") == "RestElement":
                scan_pattern(prop.get("argument"), declared)
            else:
                scan_pattern(prop.get("value"), declared)
    elif t == "ArrayPattern":
        for el in p.get("elements", []):
            if el is not None:
                scan_pattern(el, declared)
    elif t == "RestElement":
        scan_pattern(p.get("argument"), declared)
    elif t == "AssignmentPattern":
        scan_pattern(p.get("left"), declared)


def scan(node, declared, referenced):
    """Recursive name-resolution walk: every Identifier the module uses
    as a value ends up in 'referenced', every name it binds (variables,
    params, functions, classes, imports, labels) in 'declared'."""
    if isinstance(node, list):
        for item in node:
            scan(item, declared, referenced)
        return
    if not isinstance(node, dict):
        return
    t = node.get("type")

    if t == "Identifier":
        referenced.add(node["name"])
        return
    if t == "MemberExpression":
        scan(node.get("object"), declared, referenced)
        if node.get("computed"):
            scan(node.get("property"), declared, referenced)
        return
    if t == "Property":
        if node.get("computed"):
            scan(node.get("key"), declared, referenced)
        scan(node.get("value"), declared, referenced)
        return
    if t == "FunctionDeclaration":
        if node.get("id"):
            declared.add(node["id"]["name"])
        for p in node.get("params", []):
            scan_pattern(p, declared)
        scan(node.get("body"), declared, referenced)
        return
    if t in ("FunctionExpression", "ArrowFunctionExpression"):
        if t == "FunctionExpression" and node.get("id"):
            declared.add(node["id"]["name"])
        for p in node.get("params", []):
            scan_pattern(p, declared)
        scan(node.get("body"), declared, referenced)
        return
    if t in ("ClassDeclaration", "ClassExpression"):
        if node.get("id"):
            declared.add(node["id"]["name"])
        scan(node.get("superClass"), declared, referenced)
        for el in (node.get("body", {}).get("body", []) or []):
            scan(el, declared, referenced)
        return
    if t in ("MethodDefinition", "PropertyDefinition", "ClassMethod", "ClassProperty"):
        if node.get("computed"):
            scan(node.get("key"), declared, referenced)
        if isinstance(node.get("value"), dict):
            scan(node.get("value"), declared, referenced)
        for p in node.get("params", []):
            scan_pattern(p, declared)
        if isinstance(node.get("body"), dict):
            scan(node.get("body"), declared, referenced)
        return
    if t == "VariableDeclarator":
        scan_pattern(node.get("id"), declared)
        scan(node.get("init"), declared, referenced)
        return
    if t == "CatchClause":
        scan_pattern(node.get("param"), declared)
        scan(node.get("body"), declared, referenced)
        return
    if t == "LabeledStatement":
        declared.add(node["label"]["name"])
        scan(node.get("body"), declared, referenced)
        return
    if t in ("BreakStatement", "ContinueStatement"):
        if node.get("label"):
            referenced.add(node["label"]["name"])
        return
    if t in ("AssignmentExpression", "AssignmentPattern"):
        # assignment targets are references to existing bindings
        scan(node.get("left"), declared, referenced)
        scan(node.get("right"), declared, referenced)
        return
    if t == "ImportDeclaration":
        for spec in node.get("specifiers", []):
            if spec.get("local"):
                declared.add(spec["local"]["name"])
        return
    if t == "ExportNamedDeclaration":
        scan(node.get("declaration"), declared, referenced)
        for spec in node.get("specifiers", []):
            if spec.get("local"):
                referenced.add(spec["local"]["name"])
        return
    if t == "ExportDefaultDeclaration":
        scan(node.get("declaration"), declared, referenced)
        return
    if t in ("ExportAllDeclaration", "MetaProperty"):
        return

    # generic fallback: walk every child (loc/range are position info)
    for key, value in node.items():
        if key in ("loc", "range", "start", "end"):
            continue
        if isinstance(value, list):
            for item in value:
                scan(item, declared, referenced)
        elif isinstance(value, dict):
            scan(value, declared, referenced)


def collect(node, declared, referenced):
    """Collect declared and referenced identifiers in a module tree
    (already converted to plain dicts via .toDict())."""
    scan(node, declared, referenced)


def parse_module(path: Path):
    src = path.read_text(encoding="utf-8")
    try:
        tree = esprima.parseModule(src, {"loc": True, "tolerant": False}).toDict()
    except Exception as exc:  # esprima raises Error subclasses
        return {"ok": False, "error": str(exc), "path": path}
    return {"ok": True, "tree": tree, "path": path}


def imports_and_exports(tree):
    exports = set()
    imports = {}  # source -> set of names
    def handle(node):
        t = node.get("type")
        if t == "ExportNamedDeclaration":
            if node.get("declaration"):
                d = node["declaration"]
                if d["type"] == "VariableDeclaration":
                    for decl in d.get("declarations", []):
                        ids = []
                        def collect_ids(p):
                            if p.get("type") == "Identifier":
                                ids.append(p["name"])
                            else:
                                for v in p.get("properties", []) if p.get("type") == "ObjectPattern" else []:
                                    collect_ids(v.get("value") if isinstance(v, dict) else None)
                        collect_ids(decl.get("id"))
                        exports.update(ids)
                elif d["type"] == "FunctionDeclaration":
                    exports.add(d["id"]["name"])
                elif d["type"] == "ClassDeclaration":
                    exports.add(d["id"]["name"])
            elif node.get("specifiers"):
                for spec in node["specifiers"]:
                    if spec["type"] == "ExportSpecifier" and spec.get("exported"):
                        name = spec["exported"].get("name")
                        if name:
                            exports.add(name)
        elif t == "ExportDefaultDeclaration":
            exports.add("default")
        elif t == "ExportAllDeclaration":
            exports.add("*")
        elif t == "ImportDeclaration":
            source = node["source"]["value"]
            names = set()
            for spec in node.get("specifiers", []):
                if spec["type"] == "ImportNamespaceSpecifier":
                    names.add("*")
                else:
                    names.add(spec["local"]["name"])
            imports.setdefault(source, set()).update(names)

    def walk(node):
        if isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, dict):
            handle(node)
            for key, value in node.items():
                if key in ("loc", "range", "start", "end"):
                    continue
                if isinstance(value, (list, dict)):
                    walk(value)

    walk(tree)
    return exports, imports


def check_template_context(tree) -> list[str]:
    """iconSpan() returns a plain HTML string: interpolated inside a
    render`...` template it gets ESCAPED (shows as literal
    &lt;span class="icon">...). icon()/raw() return raw() markers:
    interpolated inside a PLAIN template they stringify as
    "[object Object]". Flag both directions so the classic
    "wrong helper in the wrong template" bug can't come back.

    Context is decided by the template's OWN tag (a render`...` template
    escapes its direct interpolations; a plain template — including a
    sub-template nested inside a render one — interpolates as-is)."""

    problems = []

    def callee_name(node):
        if isinstance(node, dict) and node.get("type") == "Identifier":
            return node.get("name")
        return None

    def is_call(node, *names):
        return (isinstance(node, dict) and node.get("type") == "CallExpression"
                and callee_name(node.get("callee")) in names)

    def line(node):
        return (node.get("loc") or {}).get("start", {}).get("line", "?")

    render_quasis = set()  # TemplateLiteral nodes already checked as render-tagged

    def walk(node):
        if isinstance(node, list):
            for item in node:
                walk(item)
            return
        if not isinstance(node, dict):
            return
        ntype = node.get("type")
        if ntype == "TaggedTemplateExpression" and callee_name(node.get("tag")) == "render":
            quasi = node.get("quasi") or {}
            render_quasis.add(id(quasi))
            for expr in quasi.get("expressions", []):
                if is_call(expr, "iconSpan"):
                    problems.append(f"iconSpan() inside render`...` (escaped) at line {line(expr)}")
        elif ntype == "TemplateLiteral" and id(node) not in render_quasis:
            for expr in node.get("expressions", []):
                if is_call(expr, "icon", "raw"):
                    problems.append(f"icon()/raw() inside a plain template ([object Object]) at line {line(expr)}")
        for key, value in node.items():
            if key in ("loc", "range", "start", "end"):
                continue
            if isinstance(value, (list, dict)):
                walk(value)

    walk(tree)
    return problems


def main() -> int:
    js_files = sorted(STATIC.rglob("*.js"))
    js_files = [p for p in js_files if "vendor" not in p.parts]
    errors = []

    # 1. syntax
    modules = {}
    for path in js_files:
        rel = path.relative_to(STATIC)
        res = parse_module(path)
        if not res["ok"]:
            errors.append(f"SYNTAX {rel}: {res['error']}")
        else:
            modules[rel] = res["tree"]

    # 2. import/export consistency
    export_map = {}
    import_map = {}
    for rel, tree in modules.items():
        exports, imports = imports_and_exports(tree)
        export_map[rel] = exports
        import_map[rel] = imports

    for rel, imports in import_map.items():
        mod_dir = rel.parent
        for source, names in imports.items():
            # resolve "./x.js" relative to the importing module
            target = (STATIC / mod_dir / source).resolve().relative_to(STATIC)
            if target not in export_map:
                errors.append(f"IMPORT {rel}: unknown module '{source}' (resolved to {target})")
                continue
            exports = export_map[target]
            for name in names:
                if name == "*":
                    continue
                if name not in exports:
                    errors.append(f"IMPORT {rel}: '{name}' is not exported by {source}")

    # 3. free identifiers
    for rel, tree in modules.items():
        declared = set()
        referenced = set()
        collect(tree, declared, referenced)
        missing = sorted(
            name for name in referenced
            if name not in declared and name not in GLOBALS
        )
        if missing:
            errors.append(f"UNDEFINED {rel}: {', '.join(missing)}")

    # 4. template context: iconSpan in render (escaped) / icon+raw in
    # plain templates ([object Object])
    for rel, tree in modules.items():
        for problem in check_template_context(tree):
            errors.append(f"TEMPLATE {rel}: {problem}")

    if errors:
        print(f"{len(errors)} problem(s) found:")
        for e in errors:
            print("  " + e)
        return 1
    print(f"OK: {len(js_files)} modules parsed, imports/exports consistent, no undefined identifiers.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
