"""Small, local heuristics for summarizing an incident message."""


def explain_error(error: str) -> tuple[str, list[str]]:
    """Return a cautious explanation and checks based on recognizable terms."""

    text = error.lower()
    if any(term in text for term in ("credential", "unauthorized", "401", "invalid token", "authentication")):
        return (
            "The request appears to be failing authentication or authorization. A credential mismatch can occur when the caller and service use different client IDs, secrets, token audiences, scopes, or environment-specific credentials.",
            ["Compare the caller's credential identity and environment with the service configuration; do not paste secrets into this tool.", "Check token expiry, audience, scopes, clock skew, and any recent secret rotation.", "Use the request/correlation ID to find the corresponding authentication decision in both applications."],
        )
    if any(term in text for term in ("timeout", "timed out", "deadline exceeded", "504", "gateway timeout")):
        return (
            "The request exceeded a time limit somewhere in the call path. The slow segment may be the caller, a service, a downstream dependency, or an orchestration step.",
            ["Compare timestamps and durations across caller and service logs using the same correlation ID.", "Check dependency latency, retries, connection pool saturation, and configured client/load-balancer timeouts.", "Determine whether the operation completed despite the caller timing out before retrying it."],
        )
    if any(term in text for term in ("5xx", "500", "502", "503", "internal server error")):
        return (
            "The error suggests a server-side failure or an unavailable upstream. The first failing service in the request path is the key to finding the cause.",
            ["Trace the correlation ID through each service and identify the first error, not only the final response.", "Check recent deployment/configuration changes and dependency health around the event time.", "Look for exception class, stack trace, retry exhaustion, or circuit-breaker events."],
        )
    return (
        "No specific error family was recognized. Use the request ID, exact message, and event time to trace the failure through the mapped services.",
        ["Locate the first failing service and inspect its surrounding log events.", "Compare the request and response at the caller and service boundary.", "Check for recent deployment, configuration, credential, or dependency changes."],
    )
