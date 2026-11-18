"""Constants for the Voice Incubator integration."""

DOMAIN = "voice_incubator"

STORAGE_KEY = f"{DOMAIN}.satellites"
STORAGE_VERSION = 1

MANUFACTURER = "Open Home Foundation"
MODEL = "Voice Incubator satellite"

# Dispatcher signals for timer sensor projection (sensor.py)
SIGNAL_TIMER_LIST_ADDED = f"{DOMAIN}_timer_list_added"
SIGNAL_TIMERS_UPDATED = f"{DOMAIN}_timers_updated"

# Options flow
CONF_EXPOSE_TIMER_SENSORS = "expose_timer_sensors"
