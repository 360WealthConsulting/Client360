"""Client360 observability foundation (E1.5).

Central, environment-aware logging configuration for the application. This
package configures the ``client360`` logger namespace only — it does NOT touch
the root logger or uvicorn's loggers, so it changes log *formatting*, never
application behavior.

``log_redaction`` is the one exception, and only in the safe direction: it attaches a FILTER to
uvicorn's access logger so credential-bearing query parameters cannot be written to disk. A filter
adds no handler and changes no level, so uvicorn keeps owning its own logging.
"""

from app.observability.log_redaction import install_log_redaction
from app.observability.logging import APP_LOGGER, configure_logging

__all__ = ["APP_LOGGER", "configure_logging", "install_log_redaction"]
