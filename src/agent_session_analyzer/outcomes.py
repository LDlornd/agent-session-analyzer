"""Classify recorded trial failures without importing SDKs or reading files."""


def agent_timed_out(result: dict | None) -> bool:
    """Recognize an exhausted agent budget, not an environment/tool/API timeout."""
    if not result:
        return False
    agent = result.get("agent") or {}
    reason = agent.get("finish_reason", result.get("finish_reason"))
    if reason in {"timeout", "timed_out"}:
        return True
    return any(
        error.get("stage") == "agent"
        and error.get("type") == "TimeoutError"
        and "智能体时间预算" in (error.get("message") or "")
        for error in result.get("errors") or [] if isinstance(error, dict)
    )


def trial_failure_type(result: dict | None) -> str | None:
    """Keep the first process failure; a later cleanup error is secondary."""
    if not result:
        return None
    errors = [error for error in result.get("errors") or [] if isinstance(error, dict)]
    primary = next((error for error in errors if error.get("stage") != "cleanup"),
                   errors[0] if errors else {})
    stage = primary.get("stage")
    if stage in {"prepare", "environment", "setup"}:
        return "environment_failure"
    if agent_timed_out(result):
        return "agent_timeout"
    if stage == "agent":
        return "agent_failure"
    if stage == "evaluate":
        return "evaluation_failure"
    if stage == "cleanup":
        return "cleanup_failure"
    if errors or result.get("status") in {"error", "failed", "cancelled", "interrupted"}:
        return "failure"
    reason = (result.get("agent") or {}).get("finish_reason", result.get("finish_reason"))
    if reason in {"error", "failed", "cancelled", "interrupted", "stuck", "max_iterations", "max_steps"}:
        return "agent_failure"
    return None
