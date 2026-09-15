"""Alembic ownership filters for TaskPilot metadata."""


def include_name(name: str | None, type_: str, parent_names: dict[str, str | None]) -> bool:
    """Exclude public/default objects before Alembic reflects them."""

    if type_ == "schema":
        return name == "taskpilot"
    if type_ == "table":
        return parent_names.get("schema_name") == "taskpilot"
    return True


def include_object(object_, name, type_, reflected, compare_to):  # type: ignore[no-untyped-def]
    """Defensive post-reflection filter for TaskPilot-owned tables."""

    if type_ == "table":
        return getattr(object_, "schema", None) == "taskpilot" and (
            not reflected or compare_to is not None
        )
    if type_ == "schema":
        return name == "taskpilot"
    return True
