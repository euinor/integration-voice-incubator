"""Tests for the modular Voice Incubator timer sensor platform."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import ATTR_DEVICE_CLASS, ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.voice_incubator.const import (
    CONF_EXPOSE_TIMER_SENSORS,
    DOMAIN,
    SIGNAL_TIMERS_UPDATED,
)


def _make_timer(
    timer_id: str = "01M7J7CPR7MY4X39T2NXZC2EZ8",
    name: str | None = "Pasta",
    status: str = "active",
    created_duration: int = 600,
    total_duration: int = 600,
    remaining: float = 540.0,
    created_at: datetime | None = None,
    finishes_at: datetime | None = None,
    ended_at: datetime | None = None,
) -> dict[str, Any]:
    """Build a canonical Voice Incubator timer payload."""
    now = created_at or dt_util.utcnow()
    if status == "active" and finishes_at is None:
        finishes_at = now + timedelta(seconds=remaining)
    if status in ("finished", "cancelled") and ended_at is None:
        ended_at = now

    return {
        "timer_id": timer_id,
        "name": name,
        "status": status,
        "created_duration": created_duration,
        "total_duration": total_duration,
        "created_at": now.isoformat(),
        "finishes_at": finishes_at.isoformat() if finishes_at else None,
        "ended_at": ended_at.isoformat() if ended_at else None,
        "remaining": remaining,
    }


def _seed_timer_list(
    hass: HomeAssistant,
    list_entity_id: str,
    timers: list[dict[str, Any]],
) -> None:
    """Attach test timers to the in-memory voice_timer_list store and notify listeners.

    Note for coding agent: align the attribute/lookup path below with the
    concrete VoiceTimerList entity/manager storage in `voice_timer_list.py`.
    """
    domain_data = hass.data.setdefault(DOMAIN, {})
    timer_lists = domain_data.setdefault("timer_lists", {})
    timer_list = timer_lists.get(list_entity_id)
    if timer_list is not None:
        timer_list.timers = list(timers)
    async_dispatcher_send(hass, SIGNAL_TIMERS_UPDATED, list_entity_id)


@pytest.fixture
async def setup_integration(hass: HomeAssistant) -> MockConfigEntry:
    """Set up a loaded Voice Incubator config entry with timer sensors enabled."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Voice Incubator",
        data={},
        options={CONF_EXPOSE_TIMER_SENSORS: True},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


@pytest.mark.asyncio
async def test_timer_sensor_creation_named_and_unnamed(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    setup_integration: MockConfigEntry,
) -> None:
    """Test creating TIMESTAMP sensors for named and unnamed active timers."""
    list_entity_id = "voice_timer_list.kitchen_timers"
    now = datetime(2026, 11, 18, 12, 0, 0, tzinfo=timezone.utc)
    finishes_named = now + timedelta(minutes=10)
    finishes_unnamed = now + timedelta(seconds=45)

    timers = [
        _make_timer(
            timer_id="01TIMER_NAMED",
            name="Pasta",
            status="active",
            created_duration=600,
            total_duration=600,
            remaining=600.0,
            created_at=now,
            finishes_at=finishes_named,
        ),
        _make_timer(
            timer_id="01TIMER_UNNAMED",
            name=None,
            status="active",
            created_duration=45,
            total_duration=45,
            remaining=45.0,
            created_at=now,
            finishes_at=finishes_unnamed,
        ),
    ]

    _seed_timer_list(hass, list_entity_id, timers)
    await hass.async_block_till_done()

    named_reg = entity_registry.async_get_entity_id(
        "sensor", DOMAIN, f"{setup_integration.entry_id}_01TIMER_NAMED"
    )
    unnamed_reg = entity_registry.async_get_entity_id(
        "sensor", DOMAIN, f"{setup_integration.entry_id}_01TIMER_UNNAMED"
    )
    assert named_reg is not None
    assert unnamed_reg is not None

    named_state = hass.states.get(named_reg)
    assert named_state is not None
    assert named_state.name == "Pasta"
    assert named_state.attributes[ATTR_DEVICE_CLASS] == SensorDeviceClass.TIMESTAMP
    assert dt_util.parse_datetime(named_state.state) == finishes_named
    assert named_state.attributes["timer_id"] == "01TIMER_NAMED"
    assert named_state.attributes["status"] == "active"
    assert named_state.attributes["list_entity_id"] == list_entity_id

    unnamed_state = hass.states.get(unnamed_reg)
    assert unnamed_state is not None
    assert unnamed_state.name == "45s Timer"
    assert unnamed_state.attributes[ATTR_DEVICE_CLASS] == SensorDeviceClass.TIMESTAMP
    assert dt_util.parse_datetime(unnamed_state.state) == finishes_unnamed


@pytest.mark.asyncio
async def test_timer_sensor_timestamp_updates_and_lifecycle_transitions(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    setup_integration: MockConfigEntry,
) -> None:
    """Test updating finishes_at and transitioning across active, paused, and finished."""
    list_entity_id = "voice_timer_list.kitchen_timers"
    timer_id = "01TIMER_LIFECYCLE"
    base_time = datetime(2026, 11, 18, 12, 0, 0, tzinfo=timezone.utc)
    initial_finish = base_time + timedelta(minutes=5)

    # 1. Active state
    _seed_timer_list(
        hass,
        list_entity_id,
        [_make_timer(timer_id=timer_id, name="Eggs", finishes_at=initial_finish)],
    )
    await hass.async_block_till_done()

    sensor_entity_id = entity_registry.async_get_entity_id(
        "sensor", DOMAIN, f"{setup_integration.entry_id}_{timer_id}"
    )
    assert sensor_entity_id is not None
    state = hass.states.get(sensor_entity_id)
    assert state is not None
    assert dt_util.parse_datetime(state.state) == initial_finish

    # 2. Adjusted timestamp (+1 minute)
    extended_finish = initial_finish + timedelta(minutes=1)
    _seed_timer_list(
        hass,
        list_entity_id,
        [
            _make_timer(
                timer_id=timer_id,
                name="Eggs",
                total_duration=360,
                remaining=360.0,
                finishes_at=extended_finish,
            )
        ],
    )
    await hass.async_block_till_done()

    state = hass.states.get(sensor_entity_id)
    assert state is not None
    assert state.attributes[ATTR_DEVICE_CLASS] == SensorDeviceClass.TIMESTAMP
    assert dt_util.parse_datetime(state.state) == extended_finish

    # 3. Paused state (clears TIMESTAMP device_class so UI stops counting down)
    _seed_timer_list(
        hass,
        list_entity_id,
        [
            _make_timer(
                timer_id=timer_id,
                name="Eggs",
                status="paused",
                remaining=215.0,
                finishes_at=None,
            )
        ],
    )
    await hass.async_block_till_done()

    state = hass.states.get(sensor_entity_id)
    assert state is not None
    assert state.state == "paused"
    assert ATTR_DEVICE_CLASS not in state.attributes
    assert state.attributes["remaining"] == 215.0

    # 4. Finished state (retained in list window)
    ended_time = base_time + timedelta(minutes=6)
    _seed_timer_list(
        hass,
        list_entity_id,
        [
            _make_timer(
                timer_id=timer_id,
                name="Eggs",
                status="finished",
                remaining=0.0,
                finishes_at=None,
                ended_at=ended_time,
            )
        ],
    )
    await hass.async_block_till_done()

    state = hass.states.get(sensor_entity_id)
    assert state is not None
    assert state.state == "finished"
    assert ATTR_DEVICE_CLASS not in state.attributes
    assert state.attributes["ended_at"] == ended_time.isoformat()


@pytest.mark.asyncio
async def test_entity_registry_cleanup_on_cancel_prune_and_startup(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    setup_integration: MockConfigEntry,
) -> None:
    """Test cancelled, pruned, and orphaned timer sensors are purged from EntityRegistry."""
    list_entity_id = "voice_timer_list.kitchen_timers"

    # Pre-seed an orphaned registry entry from before reload/restart
    orphan_entry = entity_registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{setup_integration.entry_id}_01ORPHAN_TIMER",
        config_entry=setup_integration,
    )
    await hass.config_entries.async_reload(setup_integration.entry_id)
    await hass.async_block_till_done()
    assert entity_registry.async_get(orphan_entry.entity_id) is None

    # Create two active timers
    _seed_timer_list(
        hass,
        list_entity_id,
        [
            _make_timer(timer_id="01KEEP_ME", name="Tea"),
            _make_timer(timer_id="01CANCEL_ME", name="Toast"),
        ],
    )
    await hass.async_block_till_done()

    cancel_entity_id = entity_registry.async_get_entity_id(
        "sensor", DOMAIN, f"{setup_integration.entry_id}_01CANCEL_ME"
    )
    keep_entity_id = entity_registry.async_get_entity_id(
        "sensor", DOMAIN, f"{setup_integration.entry_id}_01KEEP_ME"
    )
    assert cancel_entity_id is not None
    assert keep_entity_id is not None

    # Mark Toast as cancelled -> must disappear immediately from state and registry
    _seed_timer_list(
        hass,
        list_entity_id,
        [
            _make_timer(timer_id="01KEEP_ME", name="Tea"),
            _make_timer(timer_id="01CANCEL_ME", name="Toast", status="cancelled"),
        ],
    )
    await hass.async_block_till_done()

    assert hass.states.get(cancel_entity_id) is None
    assert entity_registry.async_get(cancel_entity_id) is None
    assert hass.states.get(keep_entity_id) is not None

    # Prune Tea completely from the list -> must also disappear from state and registry
    _seed_timer_list(hass, list_entity_id, [])
    await hass.async_block_till_done()

    assert hass.states.get(keep_entity_id) is None
    assert entity_registry.async_get(keep_entity_id) is None


@pytest.mark.asyncio
async def test_service_delegation_targeting_sensor_entity(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    setup_integration: MockConfigEntry,
) -> None:
    """Test timer control services resolve sensor targets to (list_entity_id, timer_id)."""
    list_entity_id = "voice_timer_list.kitchen_timers"
    timer_id = "01SERVICE_TARGET"

    _seed_timer_list(
        hass,
        list_entity_id,
        [_make_timer(timer_id=timer_id, name="Roast")],
    )
    await hass.async_block_till_done()

    sensor_entity_id = entity_registry.async_get_entity_id(
        "sensor", DOMAIN, f"{setup_integration.entry_id}_{timer_id}"
    )
    assert sensor_entity_id is not None

    # Patch the underlying timer list action dispatcher/handler to verify delegation
    with patch(
        "custom_components.voice_incubator.voice_timer_list.async_handle_timer_action",
        new_callable=AsyncMock,
    ) as mock_handler:
        await hass.services.async_call(
            DOMAIN,
            "adjust_timer",
            {ATTR_ENTITY_ID: sensor_entity_id, "seconds": 60},
            blocking=True,
        )
        mock_handler.assert_awaited_once_with(
            hass,
            action="adjust_timer",
            list_entity_id=list_entity_id,
            timer_id=timer_id,
            seconds=60,
        )

        mock_handler.reset_mock()
        await hass.services.async_call(
            DOMAIN,
            "cancel_timer",
            {ATTR_ENTITY_ID: sensor_entity_id},
            blocking=True,
        )
        mock_handler.assert_awaited_once_with(
            hass,
            action="cancel_timer",
            list_entity_id=list_entity_id,
            timer_id=timer_id,
        )


@pytest.mark.asyncio
async def test_options_flow_toggle_disables_and_purges_sensors(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    setup_integration: MockConfigEntry,
) -> None:
    """Test toggling expose_timer_sensors off purges sensors and toggling on restores them."""
    list_entity_id = "voice_timer_list.kitchen_timers"
    timer_id = "01OPT_TOGGLE"

    _seed_timer_list(
        hass,
        list_entity_id,
        [_make_timer(timer_id=timer_id, name="Rice")],
    )
    await hass.async_block_till_done()

    unique_id = f"{setup_integration.entry_id}_{timer_id}"
    sensor_entity_id = entity_registry.async_get_entity_id("sensor", DOMAIN, unique_id)
    assert sensor_entity_id is not None

    # Disable individual timer sensors via Options Flow
    result = await hass.config_entries.options.async_init(setup_integration.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={CONF_EXPOSE_TIMER_SENSORS: False},
    )
    await hass.async_block_till_done()

    assert result["type"] == "create_entry"
    assert setup_integration.options[CONF_EXPOSE_TIMER_SENSORS] is False
    assert hass.states.get(sensor_entity_id) is None
    assert entity_registry.async_get(sensor_entity_id) is None

    # Re-enable individual timer sensors via Options Flow
    result = await hass.config_entries.options.async_init(setup_integration.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={CONF_EXPOSE_TIMER_SENSORS: True},
    )
    await hass.async_block_till_done()

    assert result["type"] == "create_entry"
    assert setup_integration.options[CONF_EXPOSE_TIMER_SENSORS] is True
    restored_entity_id = entity_registry.async_get_entity_id("sensor", DOMAIN, unique_id)
    assert restored_entity_id is not None
    assert hass.states.get(restored_entity_id) is not None
