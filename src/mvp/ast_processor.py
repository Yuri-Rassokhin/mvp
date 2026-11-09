import ast
from .config import DEFAULT_RETURN_VALUE, ENSURE_DEFAULT_RETURN

class MainBlockRemover(ast.NodeTransformer):
    def visit_If(self, node):
        if (isinstance(node.test, ast.Compare) and
            isinstance(node.test.left, ast.Name) and node.test.left.id == "__name__" and
            isinstance(node.test.comparators[0], ast.Constant) and node.test.comparators[0].value == "__main__"):
            return None
        return node

class EnsureReturnTransformer(ast.NodeTransformer):
    def __init__(self, default_return_value=DEFAULT_RETURN_VALUE):
        super().__init__()
        self.default_return_value = default_return_value

    def visit_FunctionDef(self, node):
        return self._inject_return(node)

    def visit_AsyncFunctionDef(self, node):
        return self._inject_return(node)

    def _inject_return(self, node):
        # Если функция не заканчивается на return, добавляем return DEFAULT_RETURN_VALUE
        if not node.body or not isinstance(node.body[-1], ast.Return):
            new_return = ast.Return(value=ast.Constant(value=self.default_return_value))
            ast.fix_missing_locations(new_return)
            node.body.append(new_return)
        self.generic_visit(node)
        return node

def process_tree(tree, filepath, ensure_return: bool = ENSURE_DEFAULT_RETURN, default_return_value=DEFAULT_RETURN_VALUE):
    tree = MainBlockRemover().visit(tree)
    if ensure_return:
        tree = EnsureReturnTransformer(default_return_value=default_return_value).visit(tree)
    ast.fix_missing_locations(tree)
    return tree
