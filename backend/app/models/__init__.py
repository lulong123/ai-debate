__all__ = ["Angle", "DiscussionSession", "Message", "MessageRole", "SessionStatus"]


def __getattr__(name):
    if name in __all__:
        from app.models import session as _session
        return getattr(_session, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
