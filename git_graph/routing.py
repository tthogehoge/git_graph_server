"""HTTP API route registration."""

ROUTES = {}
ENGINE_ROUTES = set()  # engine の操作 (git_graph.engine.operation) を呼ぶ POST API のパス。操作が自分で履歴に記録する


def route(path, *, method="GET", engine=False):
    """engine=True: この API は git の処理を engine に任せる薄い route。履歴への記録は engine の操作が行うので、
    server 側で包んで記録しない (二重に記録しない)。"""
    method = method.upper()

    def register(handler):
        key = (method, path)
        if key in ROUTES:
            raise ValueError(f"duplicate route: {method} {path}")
        ROUTES[key] = handler
        if engine:
            ENGINE_ROUTES.add(path)
        return handler

    return register


__all__ = ["ENGINE_ROUTES", "ROUTES", "route"]
