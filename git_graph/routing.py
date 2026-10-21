"""HTTP API route registration."""

ROUTES = {}


def route(path, *, method="GET"):
    method = method.upper()

    def register(handler):
        key = (method, path)
        if key in ROUTES:
            raise ValueError(f"duplicate route: {method} {path}")
        ROUTES[key] = handler
        return handler

    return register


__all__ = ["ROUTES", "route"]
