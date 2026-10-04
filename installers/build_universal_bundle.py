"""Build EndpointManagementServer-Universal-Windows.zip — a Python-3.8-compatible SOURCE
bundle of the CURRENT code, for Windows Server 2012 clients (which cannot run Python 3.11).

It auto-converts modern typing in annotations (PEP 604 `X | None` -> Optional/Union, builtin
generics `list[...]`/`dict[...]` -> List/Dict) to Python-3.8 form, preserving comments and
layout (only annotation expressions are rewritten). It reuses the 3.8 scaffolding (.bat
scripts, doctor.py, pinned requirements) from an existing Universal zip.

Usage:
    python installers/build_universal_bundle.py [path-to-existing-Universal.zip]

Output: EndpointManagementServer-Universal-Windows.zip in the project root.
"""
from __future__ import annotations

import ast
import io
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
SERVER = ROOT / "server"
AGENT = ROOT / "agent"
OUT_ZIP = ROOT / "EndpointManagementServer-Universal-Windows.zip"

TYPING_ALIASES = {"list": "List", "dict": "Dict", "tuple": "Tuple",
                  "set": "Set", "frozenset": "FrozenSet", "type": "Type"}


class _AnnConv(ast.NodeTransformer):
    """Convert PEP 604 unions and builtin-generic subscripts within an annotation subtree."""

    def __init__(self):
        self.used = set()

    def visit_BinOp(self, node):
        self.generic_visit(node)
        if isinstance(node.op, ast.BitOr):
            # flatten A | B | ... into operand list
            operands = []

            def collect(n):
                if isinstance(n, ast.BinOp) and isinstance(n.op, ast.BitOr):
                    collect(n.left); collect(n.right)
                else:
                    operands.append(n)
            collect(node)
            has_none = any(isinstance(o, ast.Constant) and o.value is None for o in operands)
            non_none = [o for o in operands if not (isinstance(o, ast.Constant) and o.value is None)]
            if has_none and len(non_none) == 1:
                self.used.add("Optional")
                return ast.Subscript(value=ast.Name(id="Optional", ctx=ast.Load()),
                                     slice=non_none[0], ctx=ast.Load())
            inner = (non_none if not has_none else non_none)
            self.used.add("Union")
            tup = ast.Tuple(elts=inner, ctx=ast.Load())
            u = ast.Subscript(value=ast.Name(id="Union", ctx=ast.Load()), slice=tup, ctx=ast.Load())
            if has_none:
                self.used.add("Optional")
                return ast.Subscript(value=ast.Name(id="Optional", ctx=ast.Load()),
                                     slice=u, ctx=ast.Load())
            return u
        return node

    def visit_Subscript(self, node):
        self.generic_visit(node)
        if isinstance(node.value, ast.Name) and node.value.id in TYPING_ALIASES:
            alias = TYPING_ALIASES[node.value.id]
            self.used.add(alias)
            node.value = ast.Name(id=alias, ctx=ast.Load())
        return node


def _convert_source(src: str) -> str:
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return src
    lines = src.splitlines(keepends=True)

    # absolute char offset for (lineno, col) — lineno 1-based
    starts = [0]
    for ln in lines:
        starts.append(starts[-1] + len(ln))

    def off(lineno, col):
        return starts[lineno - 1] + col

    edits = []          # (start, end, newtext)
    used = set()

    def handle(annotation):
        if annotation is None:
            return
        conv = _AnnConv()
        new_node = conv.visit(ast.fix_missing_locations(annotation))
        used.update(conv.used)
        new_text = ast.unparse(new_node)
        old_text = ast.get_source_segment(src, annotation)
        if old_text is not None and new_text != old_text:
            s = off(annotation.lineno, annotation.col_offset)
            e = off(annotation.end_lineno, annotation.end_col_offset)
            edits.append((s, e, new_text))

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            a = node.args
            for arg in list(a.args) + list(a.posonlyargs) + list(a.kwonlyargs):
                handle(arg.annotation)
            if a.vararg:
                handle(a.vararg.annotation)
            if a.kwarg:
                handle(a.kwarg.annotation)
            handle(node.returns)
        elif isinstance(node, ast.AnnAssign):
            handle(node.annotation)

    # Builtin-generic subscripts in RUNTIME positions too (e.g. response_model=list[X] in a
    # route decorator) — these are evaluated on import and break on Python 3.8.
    for node in ast.walk(tree):
        if (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name)
                and node.value.id in TYPING_ALIASES
                and getattr(node, "end_lineno", None) is not None):
            conv = _AnnConv()
            new_node = conv.visit(ast.fix_missing_locations(ast.parse(ast.unparse(node)).body[0].value))
            used.update(conv.used)
            new_text = ast.unparse(new_node)
            s = off(node.lineno, node.col_offset)
            e = off(node.end_lineno, node.end_col_offset)
            old_text = src[s:e]
            if new_text != old_text:
                edits.append((s, e, new_text))

    if not edits and not used:
        return src

    # drop edits fully contained inside another edit (keep the outermost), then dedupe
    edits = sorted(set(edits), key=lambda x: (x[0], -(x[1])))
    filtered = []
    for s, e, t in edits:
        if any(os_ <= s and e <= oe and (os_, oe) != (s, e) for os_, oe, _ in filtered):
            continue
        filtered.append((s, e, t))

    # apply edits from the end so offsets stay valid
    out = src
    for s, e, new_text in sorted(filtered, key=lambda x: x[0], reverse=True):
        out = out[:s] + new_text + out[e:]

    # ensure the typing names are imported
    needed = sorted(used)
    if needed:
        imp = "from typing import " + ", ".join(needed) + "  # py38-compat\n"
        out_lines = out.splitlines(keepends=True)
        insert_at = 0
        for i, ln in enumerate(out_lines):
            if ln.startswith("from __future__"):
                insert_at = i + 1
            elif ln.startswith(("import ", "from ")) and insert_at == 0:
                insert_at = i
                break
        out_lines.insert(insert_at, imp)
        out = "".join(out_lines)
    return out


def _add_tree(zf: zipfile.ZipFile, base: Path, arc_prefix: str, convert: bool,
              exclude_rel: set | None = None) -> None:
    exclude_rel = exclude_rel or set()
    for fp in base.rglob("*"):
        if fp.is_dir():
            continue
        rel_posix = fp.relative_to(base).as_posix()
        if rel_posix in exclude_rel:
            continue
        parts = set(fp.relative_to(base).parts)
        if parts & {".venv", "__pycache__", "data", "logs", "server_dist", "agent_dist",
                    "build", "dist", "build_srv"}:
            continue
        if fp.name in (".env", ".secret", ".evidence_key", "FIRST_RUN.txt") or fp.suffix in (
                ".pyc", ".db", ".db-wal", ".db-shm", ".exe", ".zip"):
            continue
        rel = fp.relative_to(base).as_posix()
        arc = f"{arc_prefix}/{rel}" if arc_prefix else rel
        if convert and fp.suffix == ".py":
            zf.writestr(arc, _convert_source(fp.read_text(encoding="utf-8")))
        else:
            zf.writestr(arc, fp.read_bytes())


# Scaffolding lives in the repo (installers/win2012_scaffold/) so the bundle is fully
# reproducible anywhere, including the Linux cloud. Maps repo file -> path inside the zip.
SCAFFOLD_DIR = HERE / "win2012_scaffold"
SCAFFOLD_MAP = {
    "START.bat": "START.bat",                              # one-click, auto-installs per OS
    "SETUP_AND_RUN.bat": "SETUP_AND_RUN.bat",
    "SERVER_CONTROL.bat": "SERVER_CONTROL.bat",
    "RUN_SERVER.bat": "RUN_SERVER.bat",
    "INSTALL.txt": "INSTALL.txt",
    "COMPATIBILITY_NOTES.txt": "COMPATIBILITY_NOTES.txt",
    "doctor.py": "server/doctor.py",
    "requirements-win38.txt": "server/requirements.txt",   # 3.8-pinned, overrides converted one
}


def main() -> int:
    missing = [f for f in SCAFFOLD_MAP if not (SCAFFOLD_DIR / f).exists()]
    if missing:
        print(f"WARNING: missing scaffolding in {SCAFFOLD_DIR}: {missing}")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # requirements.txt comes from the 3.8-pinned scaffold, so skip the repo one
        _add_tree(zf, SERVER, "server", convert=True, exclude_rel={"requirements.txt"})
        _add_tree(zf, AGENT, "agent", convert=True)        # 3.8-convert agent source
        for extra in ("generate_agent.py", "README.md"):
            fp = ROOT / extra
            if fp.exists():
                if extra.endswith(".py"):
                    zf.writestr(extra, _convert_source(fp.read_text(encoding="utf-8")))
                else:
                    zf.writestr(extra, fp.read_bytes())
        # scaffolding + updater from the repo (override converted requirements/doctor)
        for repo_file, arc in SCAFFOLD_MAP.items():
            fp = SCAFFOLD_DIR / repo_file
            if fp.exists():
                zf.writestr(arc, fp.read_bytes())
        upd = HERE / "UPDATE.bat"
        if upd.exists():
            zf.writestr("UPDATE.bat", upd.read_bytes())

    OUT_ZIP.write_bytes(buf.getvalue())
    print(f"Wrote {OUT_ZIP}  ({OUT_ZIP.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
