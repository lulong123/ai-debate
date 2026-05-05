__all__ = ["SessionRepository"]

def __getattr__(name):
    if name == "SessionRepository":
        from app.storage.repository import SessionRepository
        return SessionRepository
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
