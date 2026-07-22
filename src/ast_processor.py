import ast

class MainBlockRemover(ast.NodeTransformer):
    def visit_If(self, node):
        if (isinstance(node.test, ast.Compare) and
            isinstance(node.test.left, ast.Name) and node.test.left.id == "__name__" and
            isinstance(node.test.comparators[0], ast.Constant) and node.test.comparators[0].value == "__main__"):
            return None
        return node

class EnsureReturnTransformer(ast.NodeTransformer):
    def visit_FunctionDef(self, node):
        return self._inject_return(node)

    def visit_AsyncFunctionDef(self, node):
        return self._inject_return(node)

    def _inject_return(self, node):
        # Если функция не заканчивается на return, добавляем return 200
        if not node.body or not isinstance(node.body[-1], ast.Return):
            new_return = ast.Return(value=ast.Constant(value=200))
            ast.fix_missing_locations(new_return)
            node.body.append(new_return)
        self.generic_visit(node)
        return node

def process_tree(tree, filepath):
    tree = MainBlockRemover().visit(tree)
    tree = EnsureReturnTransformer().visit(tree)
    ast.fix_missing_locations(tree)
    return tree

