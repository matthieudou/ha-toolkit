# HA Toolkit architecture

HA Toolkit has two independent config-entry families. Meter entries own
Recorder-backed sensor calculations. Light Group+ entries own one native light
entity and do not create or edit scenes.

## Light Group+ architecture

A Light Group+ config entry stores a name, ordered member lights, and ordered
native scenes. The light platform maps each scene's visible Home Assistant name
to an effect. Duplicate
visible scene names are rejected because `light.turn_on(effect=...)` needs an
unambiguous mapping.

`LightGroupPlus` extends Home Assistant's internal `LightGroup` class and restores
its scene-backed effect list after every native group-state update. Home Assistant
therefore owns member tracking, state and attribute aggregation, supported modes,
and command forwarding. Tests protect this intentional internal API dependency.

Turning on always activates a configured scene. An explicit effect selects its
scene, while a plain turn-on selects the first configured scene. Other supported
light attributes are then forwarded through the native group implementation.
The current effect is runtime-only and is not restored after a reload or restart.
Scenes remain native Home Assistant scenes and retain their normal editing and
storage lifecycle.

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

`energy/models.py` maps each configuration type to a `MeasurementSpec`. The spec owns
the accepted source device class and state classes, Recorder unit class,
canonical source unit, result unit and result device class.

Cumulative energy and volume statistics become normalized cumulative series
directly. Hourly mean power in kW is integrated into kWh. Hourly mean volume flow
in m³/h is integrated into m³. Missing hourly rate statistics break continuity;
the integration keeps only the newest continuous suffix rather than presenting
an incomplete interval as complete.

## Module seams

The root `config_flow.py` adapter routes setup and options flows to a feature
family. `energy/configuration.py` owns meter schemas and validation, while
`lights/configuration.py` owns Light Group+ schemas and validation.

`energy/discovery.py` is the Home Assistant state and entity-registry seam. It
validates configured entities and resolves them into immutable `MeterSource` and
`MeterTarget` values. It performs no automatic device discovery.

`energy/coordinator.py` owns live state for one physical input. A single
`MeterSourceRuntime` extends closed Recorder history with the current cumulative
state or integrates live rate changes. `PriceRuntime` owns the price timeline.

`energy/runtime.py` owns the config-entry lifecycle and shared derived data. It creates
one runtime per physical source, caches each individual or aggregate target
series once, and fans changes out to all derived sensor entities. This keeps the
number of Recorder readers and aggregate calculations independent from the
number of generated windows.

`energy/periods.py` contains pure calculations. `CumulativeSeries` calculates lifetime,
civil and rolling totals, combines source series, and derives a cost series from
a price timeline.

The root `sensor.py` adapter delegates to `energy/sensor.py`, which maps targets
and metrics to Home Assistant entities. Each individual
source and each group gets a virtual device. An individual virtual device may
link to its physical source device as its parent.

`energy/recorder.py` is the only module that queries or imports Recorder data. It reads
closed source hours and price changes, then resumes derived statistics after the
last imported hour.

The root `light.py` adapter delegates to `lights/group.py`, which owns the native
light group and its scene-backed effects.

## Correctness choices

- Aggregates use only history shared by every member.
- Recorder failures do not stop live tracking.
- Open Recorder hours are never backfilled.
- Rate history with a gap restarts after the gap.
- Price changes inside an hourly source bucket mark the result as estimated.
- A group becomes unavailable when any member lacks a usable current series.
