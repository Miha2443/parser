"""Mechanically copy existing pure coordinate helpers, without Streamlit imports."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class CacheSafety(ast.NodeTransformer):
    def visit_Assign(self, node):
        self.generic_visit(node)
        targets = [target.id for target in node.targets if isinstance(target, ast.Name)]
        if targets == ["df"] and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Attribute) and node.value.func.attr == "read_csv":
            node.value.keywords.append(ast.keyword(arg="dtype", value=ast.parse("{'registry': str, 'object_id': str}", mode="eval").body))
        if targets == ["cache_addr"]:
            node.value = ast.parse("cache[cache['address_key'].fillna('').astype(str).str.strip().ne('')][addr_cols].dropna(subset=['lat', 'lon']).drop_duplicates('address_key', keep='last')", mode="eval").body
        return node


def main():
    source = (ROOT / "app/realty_map.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                 and node.name != "load_monitoring_map_objects"]
    for node in functions:
        if node.name == "load_geocode_cache":
            node.args.defaults = []
        if node.name == "apply_geocode_cache":
            node.args.defaults = []
            node.body = [stmt for stmt in node.body if not (
                isinstance(stmt, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "cache"
                                                    for target in stmt.targets))]
    header = ('"""Existing coordinate helpers, copied mechanically; no I/O defaults or UI dependencies."""\n'
              'from __future__ import annotations\nimport json\nimport re\nfrom pathlib import Path\n'
              'from typing import Any\nimport pandas as pd\n\n')
    (ROOT / "pipeline/map_coordinates.py").write_text(
        header + "\n\n".join(ast.unparse(ast.fix_missing_locations(CacheSafety().visit(node))) for node in functions) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
