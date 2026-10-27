"""The kill switch (blueprint §5): the repository variable AGENTS_PAUSED.

Thrown when its value is "true". Every run and every Dispatcher pass checks it at start. When
it can't be read for any reason except "not set", it counts as thrown: the system fails safe.
The Owner throws it with `gh variable set AGENTS_PAUSED --body true`, or the CLI's pause.
"""

VARIABLE = "AGENTS_PAUSED"


def paused(gh):
    """(thrown, reason)."""
    try:
        variable = gh.api("GET", f"repos/{gh.repo}/actions/variables/{VARIABLE}")
    except SystemExit as e:
        if "HTTP 404" in str(e) or "Not Found" in str(e):
            return False, f"{VARIABLE} is not set"
        return True, f"the kill switch can't be read, so it counts as thrown: {e}"
    value = str(variable.get("value", "")).strip().lower()
    return value == "true", f"{VARIABLE} is {value!r}"
