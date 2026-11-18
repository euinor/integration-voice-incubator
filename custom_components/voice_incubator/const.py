"""Constants for the Voice Incubator integration."""

DOMAIN = "voice_incubator"

STORAGE_KEY = f"{DOMAIN}.satellites"
STORAGE_VERSION = 1

MANUFACTURER = "Open Home Foundation"
MODEL = "Voice Incubator satellite"

# Options: timer sensors (sensor.py)
CONF_EXPOSE_TIMER_SENSORS = "expose_timer_sensors"
DEFAULT_EXPOSE_TIMER_SENSORS = True
CONF_RETENTION_MINUTES = "retention_minutes"
"""How long a finished or cancelled timer's sensor stays."""
DEFAULT_RETENTION_MINUTES = 5
