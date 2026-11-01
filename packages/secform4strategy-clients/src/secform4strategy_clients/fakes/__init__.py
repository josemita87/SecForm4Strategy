"""Typed in-memory port implementations for the services' tests, one module per port.

Ports are Protocols, so a fake that drifts from its port would pass silently; every fake is
therefore bound to its port at module level (``_conforms: Port = Fake(...)``), and
``make type-check`` fails the moment one drifts. Fakes record their calls so a failing test
reads clearly. Each module needs only its port's extra. Mocking is banned repo-wide; a fake
arrives when a test needs it.
"""
