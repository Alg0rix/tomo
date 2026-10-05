"""Local execution stopper for the application's startup composite callback."""
from . import host, jobs
from .backend import backend


def stop_session(session_id: str) -> None:
    # Cancel admission-fenced queued jobs BEFORE killing their current container.
    jobs.stop_session(session_id)
    backend.stop_session(session_id)
    host.stop_session(session_id)


def startup() -> None:
    import logging as _logging
    try:
        from app.runtime.storage import managed_storage_status
        status = managed_storage_status()
        if not status["bounded"]:
            # Not a failure: restricted container admission independently
            # refuses backing filesystems over quota. This is the operator
            # signal to run provision-storage.sh for a real bounded volume.
            _logging.getLogger(__name__).warning(
                "Managed storage is not on a provisioned bounded volume: %s", status["root"])
        else:
            _logging.getLogger(__name__).info(
                "Managed storage bounded volume: %s", status["root"])
    except Exception:
        _logging.getLogger(__name__).exception("Managed storage capability check failed")
    backend.startup()


def shutdown() -> None:
    backend.close()
