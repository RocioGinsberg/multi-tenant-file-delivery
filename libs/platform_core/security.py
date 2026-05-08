"""Platform-level security primitives.

Pure validation functions with no framework dependencies.
These can be used by any service (FastAPI, CLI, batch jobs) to enforce
security invariants at system boundaries.
"""


def validate_object_key(object_key: str) -> None:
    """Reject path-traversal attempts in S3/storage object keys.

    Raises ValueError on invalid input.
    """
    if ".." in object_key or object_key.startswith("/"):
        raise ValueError("invalid object_key: path traversal detected")
    if "\x00" in object_key:
        raise ValueError("invalid object_key: null byte")
