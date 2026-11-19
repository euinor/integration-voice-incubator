"""Timer sensors: each timer on a voice_timer_list as a sensor of its own."""

from datetime import timedelta
from typing import Any

from freezegun.api import FrozenDateTimeFactory

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er
from homeassistant.util import dt as dt_util

from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
from pytest_homeassistant_custom_component.typing import WebSocketGenerator

DOMAIN = "voice_incubator"
KITCHEN = "voice_timer_list.kitchen_timers"


async def _setup(hass: HomeAssistant, **options: Any) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, title="Voice Incubator", options=options)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _sync(client, satellites: list[dict]) -> list[dict]:
    await client.send_json_auto_id({"type": "voice_incubator/satellites/sync", "satellites": satellites})
    msg = await client.receive_json()
    assert msg["success"], msg
    return msg["result"]["satellites"]


async def _kitchen(hass: HomeAssistant, hass_ws_client: WebSocketGenerator, **options: Any) -> tuple[MockConfigEntry, str]:
    """A satellite in the Kitchen, so its timers are on the Kitchen's list."""
    kitchen = ar.async_get(hass).async_create("Kitchen")
    entry = await _setup(hass, **options)
    client = await hass_ws_client(hass)
    [satellite] = await _sync(client, [{"id": "one", "name": "Counter"}])
    dr.async_get(hass).async_update_device(satellite["device_id"], area_id=kitchen.id)
    await hass.async_block_till_done()
    [satellite] = await _sync(client, [{"id": "one", "name": "Counter"}])
    assert satellite["timer_list"] == KITCHEN
    return entry, kitchen.id


async def _timer(hass: HomeAssistant, service: str, data: dict[str, Any], entity_id: str = KITCHEN) -> Any:
    response = await hass.services.async_call(
        "voice_timer_list",
        service,
        {"entity_id": entity_id, **data},
        blocking=True,
        return_response=service == "create_timer",
    )
    await hass.async_block_till_done()
    return response[entity_id]["timer_id"] if response else None


async def test_a_timer_is_a_sensor(hass: HomeAssistant, hass_ws_client: WebSocketGenerator) -> None:
    _, area_id = await _kitchen(hass, hass_ws_client)
    timer_id = await _timer(hass, "create_timer", {"name": "Pasta", "duration": {"minutes": 10}})
    await _timer(hass, "create_timer", {"duration": {"minutes": 1, "seconds": 30}})

    pasta = hass.states.get("sensor.kitchen_timers_1")
    assert pasta.name == "Pasta"
    assert pasta.attributes["device_class"] == "timestamp"
    finishes_at = dt_util.parse_datetime(pasta.attributes["finishes_at"])
    assert dt_util.parse_datetime(pasta.state) == finishes_at.replace(microsecond=0)  # The state is to the second.
    assert pasta.attributes["timer_id"] == timer_id
    assert pasta.attributes["status"] == "active"
    assert pasta.attributes["created_duration"] == 600
    assert pasta.attributes["timer_list"] == KITCHEN
    assert pasta.attributes["icon"] == "mdi:timer-outline"
    assert er.async_get(hass).async_get("sensor.kitchen_timers_1").area_id == area_id

    assert hass.states.get("sensor.kitchen_timers_2").name == "1 min 30 s timer"


async def test_ended_timers_stay_for_the_retention_minutes(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator, freezer: FrozenDateTimeFactory
) -> None:
    await _kitchen(hass, hass_ws_client, retention_minutes=5)
    entities = er.async_get(hass)
    await _timer(hass, "create_timer", {"name": "Tea", "duration": {"minutes": 3}})
    toast = await _timer(hass, "create_timer", {"name": "Toast", "duration": {"minutes": 10}})

    freezer.tick(timedelta(minutes=3, seconds=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.kitchen_timers_1").state == "finished"
    await _timer(hass, "cancel_timer", {"timer_id": toast})
    assert hass.states.get("sensor.kitchen_timers_2").state == "cancelled"
    assert hass.states.get("button.kitchen_timers_2_resume").state == "unavailable"  # Greyed out.

    freezer.tick(timedelta(minutes=4))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.kitchen_timers_2").state == "cancelled"
    freezer.tick(timedelta(minutes=1, seconds=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.kitchen_timers_1") is None
    assert entities.async_get("sensor.kitchen_timers_1") is None

    # Removed from its list: gone at once.
    await _timer(hass, "remove_timer", {"timer_id": toast})
    assert hass.states.get("sensor.kitchen_timers_2") is None
    assert entities.async_get("sensor.kitchen_timers_2") is None


async def test_slots_are_reused(hass: HomeAssistant, hass_ws_client: WebSocketGenerator) -> None:
    await _kitchen(hass, hass_ws_client, retention_minutes=0)
    entities = er.async_get(hass)
    first = await _timer(hass, "create_timer", {"name": "One", "duration": {"minutes": 5}})
    await _timer(hass, "create_timer", {"name": "Two", "duration": {"minutes": 5}})
    assert hass.states.get("sensor.kitchen_timers_2").name == "Two"

    for _ in range(3):
        await _timer(hass, "cancel_timer", {"timer_id": first})
        assert hass.states.get("sensor.kitchen_timers_1") is None
        first = await _timer(hass, "create_timer", {"name": "Again", "duration": {"minutes": 5}})
        assert hass.states.get("sensor.kitchen_timers_1").name == "Again"
    assert hass.states.get("sensor.kitchen_timers_3") is None
    assert len([k for k in entities.deleted_entities if k[0] == "sensor"]) <= 2


async def test_options_apply_without_losing_timers(hass: HomeAssistant, hass_ws_client: WebSocketGenerator) -> None:
    entry, _ = await _kitchen(hass, hass_ws_client)
    await _timer(hass, "create_timer", {"name": "Rice", "duration": {"minutes": 15}})
    assert hass.states.get("sensor.kitchen_timers_1") is not None

    flow = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        flow["flow_id"], {"expose_timer_sensors": False, "retention_minutes": 5}
    )
    await hass.async_block_till_done()
    assert result["type"] == "create_entry"
    assert hass.states.get("sensor.kitchen_timers_1") is None
    assert er.async_get(hass).async_get("sensor.kitchen_timers_1") is None
    assert hass.states.get(KITCHEN).state == "1"  # Still running: no reload.

    flow = await hass.config_entries.options.async_init(entry.entry_id)
    await hass.config_entries.options.async_configure(flow["flow_id"], {"expose_timer_sensors": True, "retention_minutes": 5})
    await hass.async_block_till_done()
    assert hass.states.get("sensor.kitchen_timers_1").name == "Rice"


async def test_stale_timer_sensors_are_removed_at_setup(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, title="Voice Incubator")
    entry.add_to_hass(hass)
    entities = er.async_get(hass)
    stale = entities.async_get_or_create("sensor", DOMAIN, "area_kitchen_timers_timer_1", config_entry=entry)
    button = entities.async_get_or_create("button", DOMAIN, "area_kitchen_timers_timer_1_pause", config_entry=entry)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entities.async_get(stale.entity_id) is None
    assert entities.async_get(button.entity_id) is None


async def test_a_lists_sensors_go_with_it(hass: HomeAssistant, hass_ws_client: WebSocketGenerator) -> None:
    kitchen = ar.async_get(hass).async_create("Kitchen")
    entry = await _setup(hass)
    client = await hass_ws_client(hass)
    [satellite] = await _sync(client, [{"id": "one", "name": "Counter"}])
    own = satellite["timer_list"]
    await _timer(hass, "create_timer", {"name": "Desk", "duration": {"minutes": 5}}, entity_id=own)
    sensor = hass.states.get("sensor.counter_timers_1")
    assert sensor.name == "Counter Desk"  # On the satellite's device.

    # Into an area: its own list goes, and its timers' sensors with it.
    dr.async_get(hass).async_update_device(satellite["device_id"], area_id=kitchen.id)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.counter_timers_1") is None
    assert er.async_get(hass).async_get("sensor.counter_timers_1") is None

    # Unloaded: nothing left behind.
    await _timer(hass, "create_timer", {"duration": {"minutes": 5}})
    assert hass.states.get("sensor.kitchen_timers_1") is not None
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert er.async_get(hass).async_get("sensor.kitchen_timers_1") is None


async def test_two_way_sync(hass: HomeAssistant, hass_ws_client: WebSocketGenerator) -> None:
    """An action on a sensor is the list's own: the app's subscription hears it,
    and the sensor follows the list, as it does a change made by voice."""
    await _kitchen(hass, hass_ws_client)
    timer_id = await _timer(hass, "create_timer", {"name": "Bread", "duration": {"minutes": 20}})
    app = await hass_ws_client(hass)
    await app.send_json_auto_id({"type": "voice_timer_list/item/subscribe", "entity_id": KITCHEN})
    assert (await app.receive_json())["success"]
    assert (await app.receive_json())["event"]["type"] == "timers"

    async def act(service: str, **data: Any) -> str:
        await hass.services.async_call(
            DOMAIN, service, {"entity_id": "sensor.kitchen_timers_1", **data}, blocking=True
        )
        await hass.async_block_till_done()
        event = (await app.receive_json())["event"]
        assert event["timer"]["timer_id"] == timer_id
        return event["event_type"]

    def state() -> str:
        return hass.states.get("sensor.kitchen_timers_1").state

    finishes_at = dt_util.parse_datetime(state())
    assert await act("add_time", duration={"minutes": 5}) == "time_changed"
    assert dt_util.parse_datetime(state()) == finishes_at + timedelta(minutes=5)
    assert await act("subtract_time", duration={"minutes": 2}) == "time_changed"
    assert dt_util.parse_datetime(state()) == finishes_at + timedelta(minutes=3)
    assert await act("pause_timer") == "paused"
    assert state() == "paused"
    assert await act("resume_timer") == "unpaused"
    assert dt_util.parse_datetime(state()) is not None
    assert await act("cancel_timer") == "cancelled"
    assert state() == "cancelled"


async def test_each_timer_has_its_own_buttons(hass: HomeAssistant, hass_ws_client: WebSocketGenerator) -> None:
    """A timer's buttons act on it alone, the app hears them, they grey out
    (unavailable) when they don't apply, and they go with the timer."""
    _, area_id = await _kitchen(hass, hass_ws_client, retention_minutes=0)
    await _timer(hass, "create_timer", {"name": "Pasta", "duration": {"minutes": 10}})
    sauce = await _timer(hass, "create_timer", {"duration": {"minutes": 5}})
    app = await hass_ws_client(hass)
    await app.send_json_auto_id({"type": "voice_timer_list/item/subscribe", "entity_id": KITCHEN})
    assert (await app.receive_json())["success"]
    assert (await app.receive_json())["event"]["type"] == "timers"

    def states(slot: int) -> dict[str, str]:
        return {a: hass.states.get(f"button.kitchen_timers_{slot}_{a}").state != "unavailable" for a in
                ("pause", "resume", "cancel", "add_minute", "subtract_minute")}

    async def press(slot: int, action: str) -> str:
        await hass.services.async_call(
            "button", "press", {"entity_id": f"button.kitchen_timers_{slot}_{action}"}, blocking=True
        )
        await hass.async_block_till_done()
        event = (await app.receive_json())["event"]
        assert event["timer"]["timer_id"] == sauce  # Timer 2's button: timer 2.
        return event["event_type"]

    assert hass.states.get("button.kitchen_timers_1_pause").name == "Pause Pasta timer"
    assert hass.states.get("button.kitchen_timers_2_add_minute").name == "Add 1 min to 5 min timer"
    assert er.async_get(hass).async_get("button.kitchen_timers_2_pause").area_id == area_id
    assert states(2) == {"pause": True, "resume": False, "cancel": True, "add_minute": True, "subtract_minute": True}

    assert await press(2, "add_minute") == "time_changed"
    assert await press(2, "subtract_minute") == "time_changed"
    assert await press(2, "pause") == "paused"
    assert states(2) == {"pause": False, "resume": True, "cancel": True, "add_minute": True, "subtract_minute": True}
    assert hass.states.get("sensor.kitchen_timers_1").attributes["status"] == "active"  # Untouched.
    assert await press(2, "resume") == "unpaused"
    assert await press(2, "cancel") == "cancelled"

    # Retention 0: gone at once, buttons and all.
    assert hass.states.get("button.kitchen_timers_2_pause") is None
    assert er.async_get(hass).async_get("button.kitchen_timers_2_pause") is None
