# Meter architecture

MattsAssistant has two independent config-entry families. Meter entries own
Recorder-backed sensor calculations. Light Group+ entries own one native light
entity and do not create or edit scenes.

## Light Group+ architecture

A Light Group+ config entry stores a name, ordered member lights, ordered native
scenes, one default scene and a `default` or `last` turn-on behavior. The light
platform maps each scene's visible Home Assistant name to an effect. Duplicate
visible scene names are rejected because `light.turn_on(effect=...)` needs an
unambiguous mapping.

`LightGroupPlus` is a standalone `LightEntity`. It deliberately does not inherit
Home Assistant's internal `LightGroup` class, whose state updates replace the
effect list with effects advertised by member bulbs. The entity subscribes to
member state changes, reports on when any member is on, averages the brightness
of members that are on, and forwards off commands to every member.

Turning on always activates a configured scene. An explicit effect selects its
scene. A plain turn-on selects either the default scene or the last selected
scene, with the default as fallback. A supplied brightness is then forwarded
uniformly to all members. This V1 behavior does not scale individual scene
brightness values.

The last effect uses Home Assistant's restore-state data. It survives integration
reloads and Home Assistant restarts without an `input_select`, helper, or config
entry write. Scenes remain native Home Assistant scenes and retain their normal
editing and storage lifecycle.

## Configuration model

One config entry owns one measurement type, one optional price timeline,
individual sources and named groups:

```python
{
    "configuration_type": "electricity_energy",
    "source_entity_ids": ["sensor.dishwasher_energy"],
    "attach_entity_ids": ["sensor.dishwasher_energy"],
    "groups": [
        {
            "id": "stable-generated-id",
            "name": "Housekeeping",
            "entity_ids": ["sensor.dishwasher_energy"],
        }
    ],
    "price_entity_id": "input_number.electricity_price",
}
```

Entity selection is intentional. Every individual source is represented by a
virtual meter device. When requested, that device links to the physical source
through `via_device`. The physical device is not the measurement boundary
because it may expose several independent meters or rate sensors.

Group IDs remain stable when names change. Individual target IDs use the source
entity registry entry ID when available. Generated sensor unique IDs also
include the config entry ID, which allows the same source to appear in separate
pricing configurations. Home Assistant's entity platform treats the source-based
entity ID as a suggestion, resolves collisions and preserves user renames.

## Measurement normalization

`models.py` maps each configuration type to a `MeasurementSpec`. The spec owns
the accepted source device class and state classes, Recorder unit class,
canonical source unit, result unit and result device class.

Cumulative energy and volume statistics become normalized cumulative series
directly. Hourly mean power in kW is integrated into kWh. Hourly mean volume flow
in m³/h is integrated into m³. Missing hourly rate statistics break continuity;
the integration keeps only the newest continuous suffix rather than presenting
an incomplete interval as complete.

## Module boundaries

`config_flow.py` owns the multi-step UI. It selects a source type first, then
individual sensors, per-source device links and any number of explicit groups.
The options flow uses the same steps and keeps group IDs stable.

`discovery.py` is the Home Assistant state and entity-registry boundary. It
validates configured entities and resolves them into immutable `MeterSource` and
`MeterTarget` values. It performs no automatic device discovery.

`coordinator.py` owns live state for one physical input. A single
`MeterSourceRuntime` extends closed Recorder history with the current cumulative
state or integrates live rate changes. `PriceRuntime` owns the price timeline.

`runtime.py` owns the config-entry lifecycle and shared derived data. It creates
one runtime per physical source, caches each individual or aggregate target
series once, and fans changes out to all derived sensor entities. This keeps the
number of Recorder readers and aggregate calculations independent from the
number of generated windows.

`periods.py` contains pure calculations. `CumulativeSeries` calculates lifetime,
civil and rolling totals, combines source series, and derives a cost series from
a price timeline.

`sensor.py` maps targets and metrics to Home Assistant entities. Each individual
source and each group gets a virtual device. An individual virtual device may
link to its physical source device as its parent.

`recorder.py` is the only module that queries or imports Recorder data. It reads
closed source hours and price changes, then resumes derived statistics after the
last imported hour.

`migration.py` is the compatibility boundary for unpublished config-entry
formats and their entity unique IDs.

## Correctness choices

- Aggregates use only history shared by every member.
- Recorder failures do not stop live tracking.
- Open Recorder hours are never backfilled.
- Rate history with a gap restarts after the gap.
- Price changes inside an hourly source bucket mark the result as estimated.
- A group becomes unavailable when any member lacks a usable current series.
