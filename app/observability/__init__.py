"""Client360 observability foundation (E1.5).

Central, environment-aware logging configuration for the application. This
package configures the ``client360`` logger namespace only — it does NOT touch
the root logger or uvicorn's loggers, so it changes log *formatting*, never
application behavior.
"""

from app.observability.logging import APP_LOGGER, configure_logging

__all__ = ["APP_LOGGER", "configure_logging"]
