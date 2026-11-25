"""Timer sensors: each timer on this integration's voice_timer_lists as a
sensor of its own, for dashboards.

The lists stay the timers' only source: the sensors follow them through
``async_subscribe_updates`` and hold nothing of their own. A list's timers take
numbered slots (``sensor.kitchen_timers_1``, ``_2`` ...), freed when the timer
goes, so entity ids stay put and the entity registry stays small. A finished
or cancelled timer stays for the options' retention minutes.

To remove the feature: delete this file, Platform.SENSOR and the options flow.
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback, split_entity_id
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import (
    AddConfigEntryEntitiesCallback,
    EntityPlatform,
    async_get_current_platform,
)
from homeassistant.helpers.event import (
    async_track_point_in_utc_time,
    async_track_state_added_domain,
    async_track_state_removed_domain,
)
from homeassistant.util import dt as dt_util

from .const import (
    CONF_EXPOSE_TIMER_SENSORS,
    CONF_RETENTION_MINUTES,
    DEFAULT_EXPOSE_TIMER_SENSORS,
    DEFAULT_RETENTION_MINUTES,
    DOMAIN,
)
from .satellites import VoiceIncubatorConfigEntry
from .timer_list import TimerItem, TimerListEntity
from .timer_list_const import DATA_COMPONENT, DOMAIN as TIMER_LIST_DOMAIN, TimerStatus

SLOT_UNIQUE_ID = re.compile(r"_timers_timer_\d+$")
"""A timer sensor's unique id: its list's (``..._timers``), then its slot."""

ENDED = (TimerStatus.FINISHED, TimerStatus.CANCELLED)
STATES = [TimerStatus.PAUSED.value, *(s.value for s in ENDED)]
ICONS = {
    TimerStatus.ACTIVE: "mdi:timer-outline",
    TimerStatus.PAUSED: "mdi:timer-pause-outline",
    TimerStatus.FINISHED: "mdi:timer-check-outline",
    TimerStatus.CANCELLED: "mdi:timer-cancel-outline",
}

type Key = tuple[str, str]
"""A timer sensor's list entity id and timer id."""


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VoiceIncubatorConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """A sensor for each timer, now and as the lists change."""
    # Timers do not survive a restart, so no sensor left from before has one.
    entities = er.async_get(hass)
    for registered in er.async_entries_for_config_entry(entities, entry.entry_id):
        if registered.domain == "sensor" and SLOT_UNIQUE_ID.search(registered.unique_id):
            entities.async_remove(registered.entity_id)
    TimerSensors(hass, entry, async_get_current_platform()).async_start()


def duration_label(duration: timedelta) -> str:
    """``1 h 30 min``, ``10 min``, ``45 s``: what an unnamed timer is called by."""
    hours, rest = divmod(round(duration.total_seconds()), 3600)
    minutes, seconds = divmod(rest, 60)
    parts = [f"{n} {unit}" for n, unit in ((hours, "h"), (minutes, "min"), (seconds, "s")) if n]
    return " ".join(parts) or "0 s"


class TimerSensors:
    """The timer sensors of one config entry, kept to its lists' timers."""

    def __init__(self, hass: HomeAssistant, entry: VoiceIncubatorConfigEntry, platform: EntityPlatform) -> None:
        self.hass = hass
        self.entry = entry
        self.platform = platform
        self._lists: dict[str, tuple[TimerListEntity, CALLBACK_TYPE]] = {}
        """Each list followed, by entity id, and how to stop."""
        self._sensors: dict[Key, VoiceTimerSensor] = {}
        self._expiries: list[CALLBACK_TYPE] = []
        self._lock = asyncio.Lock()

    @callback
    def async_start(self) -> None:
        on_unload = self.entry.async_on_unload
        on_unload(async_track_state_added_domain(self.hass, TIMER_LIST_DOMAIN, self.async_schedule_sync))
        on_unload(async_track_state_removed_domain(self.hass, TIMER_LIST_DOMAIN, self.async_schedule_sync))
        on_unload(self.entry.add_update_listener(self._async_options_updated))
        on_unload(self._async_stop)
        self.async_schedule_sync()

    async def _async_options_updated(self, hass: HomeAssistant, entry: VoiceIncubatorConfigEntry) -> None:
        """Applied as they are: a reload would lose every timer."""
        self.async_schedule_sync()

    @callback
    def async_schedule_sync(self, *_: Any) -> None:
        self.hass.async_create_task(self._async_sync())

    def _timer_lists(self) -> dict[str, TimerListEntity]:
        """This entry's lists that are in Home Assistant."""
        return {
            entity.entity_id: entity
            for entity in self.hass.data[DATA_COMPONENT].entities
            if entity.hass is not None
            and entity.platform is not None
            and entity.platform.config_entry is not None
            and entity.platform.config_entry.entry_id == self.entry.entry_id
        }

    async def _async_sync(self) -> None:
        """Make the sensors what the lists' timers say. Safe to run any time."""
        async with self._lock:
            if self.entry.state not in (ConfigEntryState.SETUP_IN_PROGRESS, ConfigEntryState.LOADED):
                return  # Unloading: _async_stop cleans up.
            lists = self._timer_lists()
            self._follow(lists)

            now = dt_util.utcnow()
            options = self.entry.options
            retention = timedelta(minutes=options.get(CONF_RETENTION_MINUTES, DEFAULT_RETENTION_MINUTES))
            wanted: dict[Key, tuple[TimerListEntity, TimerItem]] = {}
            for expiry in self._expiries:
                expiry()
            self._expiries = []
            if options.get(CONF_EXPOSE_TIMER_SENSORS, DEFAULT_EXPOSE_TIMER_SENSORS):
                for entity_id, timer_list in lists.items():
                    for item in timer_list.timers:
                        if item.status in ENDED:
                            until = (item.ended_at or now) + retention
                            if until <= now:
                                continue
                            self._expiries.append(
                                async_track_point_in_utc_time(self.hass, self.async_schedule_sync, until)
                            )
                        wanted[(entity_id, item.timer_id)] = (timer_list, item)

            for key in set(self._sensors) - set(wanted):
                await self._async_remove(self._sensors.pop(key))
            added: list[VoiceTimerSensor] = []
            for key, (timer_list, item) in wanted.items():
                if (sensor := self._sensors.get(key)) is not None:
                    sensor.async_update_item(item)
                    continue
                used = {s.slot for (entity_id, _), s in self._sensors.items() if entity_id == key[0]}
                slot = next(n for n in range(1, len(used) + 2) if n not in used)
                sensor = self._sensors[key] = VoiceTimerSensor(timer_list, item, slot)
                added.append(sensor)
            if added:
                await self.platform.async_add_entities(added)

    @callback
    def _follow(self, lists: dict[str, TimerListEntity]) -> None:
        """Subscribe to each list's changes; drop lists that went or were
        replaced by a new object."""
        for entity_id, (timer_list, unsubscribe) in list(self._lists.items()):
            if lists.get(entity_id) is not timer_list:
                unsubscribe()
                del self._lists[entity_id]
        for entity_id, timer_list in lists.items():
            if entity_id not in self._lists:
                self._lists[entity_id] = (timer_list, timer_list.async_subscribe_updates(self.async_schedule_sync))

    async def _async_remove(self, sensor: VoiceTimerSensor) -> None:
        """Out of Home Assistant and the entity registry, freeing its slot."""
        if sensor.hass is not None:
            await sensor.async_remove(force_remove=True)
        self._unregister(sensor)

    @callback
    def _unregister(self, sensor: VoiceTimerSensor) -> None:
        entities = er.async_get(self.hass)
        if (entity_id := entities.async_get_entity_id("sensor", DOMAIN, sensor.unique_id or "")) is not None:
            entities.async_remove(entity_id)

    @callback
    def _async_stop(self) -> None:
        """Unloaded: the timers are gone with their lists, and so are their sensors."""
        for _, unsubscribe in self._lists.values():
            unsubscribe()
        self._lists.clear()
        for expiry in self._expiries:
            expiry()
        self._expiries = []
        for sensor in self._sensors.values():
            self._unregister(sensor)
        self._sensors.clear()


class VoiceTimerSensor(SensorEntity):
    """One timer: when it finishes while it runs, else paused, finished or
    cancelled."""

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_translation_key = "timer"

    def __init__(self, timer_list: TimerListEntity, item: TimerItem, slot: int) -> None:
        self.timer_list = timer_list
        self.slot = slot
        self._attr_unique_id = f"{timer_list.unique_id}_timer_{slot}"
        self.entity_id = f"sensor.{split_entity_id(timer_list.entity_id)[1]}_{slot}"
        self._attr_device_info = timer_list.device_info
        if item.name:
            self._attr_name = item.name
        else:
            self._attr_translation_placeholders = {"duration": duration_label(item.created_duration)}
        self._set(item)

    def _set(self, item: TimerItem) -> None:
        self.item = item
        active = item.status is TimerStatus.ACTIVE
        self._attr_device_class = SensorDeviceClass.TIMESTAMP if active else SensorDeviceClass.ENUM
        self._attr_options = None if active else STATES
        self._attr_native_value = item.finishes_at if active else item.status.value
        self._attr_icon = ICONS[item.status]
        self._attr_extra_state_attributes = {
            "timer_id": item.timer_id,
            "status": item.status.value,
            "created_duration": item.created_duration.total_seconds(),
            "total_duration": item.total_duration.total_seconds(),
            "finishes_at": _iso(item.finishes_at),
            "ended_at": _iso(item.ended_at),
            "paused_remaining": item.paused_remaining.total_seconds() if item.paused_remaining else None,
            "timer_list": self.timer_list.entity_id,
        }

    @callback
    def async_update_item(self, item: TimerItem) -> None:
        self._set(item)
        if self.hass is not None:
            self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # An area's list is in its area on no device: put its timers there too.
        entities = er.async_get(self.hass)
        if (
            self.device_info is None
            and self.registry_entry is not None
            and self.registry_entry.area_id is None
            and (timer_list := entities.async_get(self.timer_list.entity_id)) is not None
            and timer_list.area_id is not None
        ):
            entities.async_update_entity(self.entity_id, area_id=timer_list.area_id)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None
