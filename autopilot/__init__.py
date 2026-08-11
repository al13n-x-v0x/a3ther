"""
A3THER Autopilot — the self-healing execution engine.

``ProcessRunner`` wraps command execution and captures stdout, stderr and
exit codes. When a run fails, :mod:`autopilot.repair` extracts the failing
file boundaries from the traceback and :class:`autopilot.freaky_fix.FreakyFixLoop`
feeds the precise error context to the strongest available LLM, rewrites
the broken file, and re-runs the command — up to three attempts before it
safely reports back to the user.
"""
