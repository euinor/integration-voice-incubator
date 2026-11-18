# AGENTS.md — Architectural Specification & Implementation Guide

## Objective
Implement a modular, upstream-ready feature for `OHF-Voice/integration-voice-incubator` that projects active and recently finished timers from the existing `voice_timer_list` platform into individual Home Assistant `sensor` entities (`SensorDeviceClass.TIMESTAMP`), with bidirectional service support and an options-flow toggle.

## Core Design Constraints (Non-Negotiable)
1. **Unidirectional Adapter Architecture:** The existing `voice_timer_list` implementation remains the **single source of truth** for timer state, lifecycle scheduling, and WebSocket communication with the Voice Incubator app. Core timer modules must **never** import `sensor.py` or manipulate `EntityRegistry` directly.
2. **Decoupled via Dispatcher Signals:** Communicate state mutations from `voice_timer_list` to `sensor.py` strictly via `homeassistant.helpers.dispatcher` (`async_dispatcher_send` / `async_dispatcher_connect`).
3. **Easy Upstream Rollback:** All entity projection logic must live in a self-contained `sensor.py` gated behind a config entry option (`OptionsFlowWithReload`), so the entire feature can be disabled at runtime or removed cleanly in a single commit if replaced by a future native UI.
4. **Target Compatibility:** Home Assistant Core `2025.10+`, matching existing repository typing, async conventions, `strings.json` / `translations/`, and `pytest` patterns.

---

## Step 0: Inspect Existing Codebase First
Before writing code, read all files in `custom_components/voice_incubator/` and `tests/` to align with exact module names, data structures, and conventions:
- Inspect `__init__.py` and `const.py` for existing platforms, domain constants, and `hass.data` / `entry.runtime_data` layout.
- Inspect the `voice_timer_list` platform module to see how timer dictionaries/objects (`timer_id`, `name`, `status`, `created_duration`, `total_duration`, `finishes_at`, `ended_at`, `remaining`) are stored, mutated, and pruned.
- Inspect `services.yaml` and existing service handlers to match exact action names and schemas (e.g., adjust, pause, resume, cancel).
- Inspect `config_flow.py` and `strings.json` to see how config entries and options are currently structured.

---

## Step 1: Dispatcher Signals (`const.py` & `voice_timer_list`)
1. Add signal constants in `const.py`:
   - `SIGNAL_TIMER_LIST_ADDED = f"{DOMAIN}_timer_list_added"`
   - `SIGNAL_TIMERS_UPDATED = f"{DOMAIN}_timers_updated"`
   - `CONF_EXPOSE_TIMER_SENSORS = "expose_timer_sensors"` (default `True`)
2. In the `voice_timer_list` entity/manager:
   - Emit `async_dispatcher_send(self.hass, SIGNAL_TIMER_LIST_ADDED, self.entity_id)` when a timer list entity is added to Home Assistant.
   - Emit `async_dispatcher_send(self.hass, SIGNAL_TIMERS_UPDATED, self.entity_id)` at the end of every mutation method (timer created, updated/adjusted, paused, resumed, finished, cancelled, and pruned).

---

## Step 2: Declarative Timer Sensor Platform (`custom_components/voice_incubator/sensor.py`)
Create `sensor.py` implementing a reconciliation manager and `VoiceTimerSensor(SensorEntity)`:

1. **Idempotent Reconciliation (`_async_sync_timers`):**
   - Subscribe to `SIGNAL_TIMER_LIST_ADDED` and `SIGNAL_TIMERS_UPDATED` (cleaning up subscriptions via `async_on_remove`).
   - On startup and on every signal, inspect all active `voice_timer_list` instances and reconcile a map of `(list_entity_id, timer_id) -> VoiceTimerSensor`.
2. **State & Device Class Mapping:**
   - `status == "active"`:
     - `device_class = SensorDeviceClass.TIMESTAMP`
     - `native_value`: timezone-aware UTC `datetime` parsed from `finishes_at` (`homeassistant.util.dt.parse_datetime` / `dt_util.as_utc`). Never pass a raw string when `device_class` is `TIMESTAMP`.
   - `status == "paused"`:
     - `device_class = None`
     - `native_value = "paused"`
   - `status == "finished"`:
     - `device_class = None`
     - `native_value = "finished"` (keep entity alive only while the entry remains in `voice_timer_list`'s retention window).
   - `status == "cancelled"` or pruned from `voice_timer_list`:
     - Immediately remove the entity from Home Assistant (`await entity.async_remove()`) **and** delete its entry from `EntityRegistry` (`er.async_get(hass).async_remove(entity.entity_id)`).
3. **Entity Metadata & Device/Area Binding:**
   - `unique_id`: `f"{entry.entry_id}_{timer_id}"`
   - `name`: `timer["name"]` if non-empty; otherwise format a clean duration label from `created_duration` (e.g., `"10m Timer"` or `"45s Timer"`).
   - `icon`: `"mdi:timer-outline"` (`active`), `"mdi:timer-pause-outline"` (`paused`), `"mdi:timer-check-outline"` (`finished`).
   - Bind `device_info` (and area assignment) to match the parent `voice_timer_list` entity's device/area so area-scoped dashboards include the timer sensors automatically.
   - `extra_state_attributes`: expose `timer_id`, `status`, `total_duration`, `created_duration`, `remaining`, `finishes_at`, `ended_at`, and `list_entity_id`.
4. **Orphan Cleanup on Startup:**
   - During `async_setup_entry` in `sensor.py`, query `EntityRegistry` for `sensor` entities belonging to this `config_entry.entry_id` whose `timer_id` is no longer active/retained in `voice_timer_list`, and remove them so restarted HA instances never accumulate stale timer sensors.

---

## Step 3: Service Target Delegation (`__init__.py` & `services.yaml`)
1. Update the existing timer control service schemas in `services.yaml` (adjust, pause, resume, cancel) so `target.entity` accepts `domain: ["voice_timer_list", "sensor"]` and make `timer_id` optional when targeting a `sensor` entity directly.
2. Add a small target-resolution helper in the service handler module:
   - When a service call targets one or more `sensor.*` entities owned by `voice_incubator`, resolve `(list_entity_id, timer_id)` from each sensor and delegate directly to the existing `voice_timer_list` action handler.
   - Preserve 100% backwards compatibility when targeting `voice_timer_list.*` with an explicit `timer_id`.

---

## Step 4: Config Entry Lifecycle & Options Flow (`__init__.py`, `config_flow.py`, `strings.json`)
1. Implement `OptionsFlowWithReload` in `config_flow.py` exposing a boolean toggle for `CONF_EXPOSE_TIMER_SENSORS` (with matching labels in `strings.json` and `translations/en.json`).
2. In `__init__.py`:
   - Include `Platform.SENSOR` in forwarded platforms only when `entry.options.get(CONF_EXPOSE_TIMER_SENSORS, True)` is `True`.
   - When `False`, run a cleanup pass in `async_setup_entry` that removes any existing `voice_incubator` `sensor` entities from `EntityRegistry`.

---

## Step 5: Tests & Documentation
1. Add comprehensive unit tests in `tests/test_sensor.py` using the repo's existing `pytest-homeassistant-custom-component` fixtures covering:
   - Spawning a `TIMESTAMP` sensor when a timer starts, with proper `datetime` `native_value` and attributes.
   - Updating `finishes_at` when time is added/subtracted.
   - Transitioning through `paused` and `finished` states.
   - Immediate removal from both state machine and `EntityRegistry` when cancelled or pruned.
   - Purging orphaned timer sensors on integration reload/restart.
   - Calling timer adjustment and cancel services directly against `entity_id: sensor.<timer>`.
   - Toggling `expose_timer_sensors` off via options flow and verifying `EntityRegistry` cleanup.
2. Document the timer `sensor` entities, attributes, direct service targeting, and configuration option in `GUIDE.md`.

---

## Acceptance Criteria
- `pytest` passes with zero errors or warnings on new code.
- `voice_timer_list` has zero imports from `sensor.py`.
- Ephemeral `timer_id` ULIDs never remain in `EntityRegistry` after cancellation, pruning, or disabling the option.
