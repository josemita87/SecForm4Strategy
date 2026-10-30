"""Pure infrastructure clients shared across the SecForm4Strategy microservices.

Each submodule wraps one external service and imports its SDK at module level, so a
service imports only the client modules whose extras it installed (package README).
"""

__version__ = '0.1.0'
