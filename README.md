# MattsAssistant

MattsAssistant is a Home Assistant custom integration that adds calendar,
rolling and lifetime totals to utility meters and instantaneous rate sensors.
It supports electricity, water and gas, named aggregates, historical
reconstruction and time-varying unit prices. It also provides Light Group+,
a native light group whose effects activate configured Home Assistant scenes.

## Light Group+

Light Group+ creates a standard `light` entity from several member lights. Its
effect list contains the visible names of selected native Home Assistant scenes.
Calling `light.turn_on` with an effect activates the matching scene. Turning the
group on without an effect activates either the configured default scene or the
last selected scene.

The group is on when at least one member is on. Turning it off targets every
member. A brightness supplied with `light.turn_on` is applied uniformly after
the selected scene. The integration remembers the last effect without a helper,
while scenes remain editable through Home Assistant.

## Configuration types

Each config entry handles one source type and one optional unit price:

- cumulative electricity energy;
- instantaneous electricity power, integrated into energy;
- cumulative water volume;
- instantaneous water flow, integrated into volume;
- cumulative gas volume;
- instantaneous gas flow, integrated into volume.

Sources are selected as sensor entities, not devices. This supports devices with
several channels, such as a two-valve water controller or a multi-socket power
strip. A source device does not need a `switch` entity.

An individual source receives a virtual MattsAssistant meter containing its
derived sensors. By default, the meter is linked below the physical source
device, but it can remain standalone. Named groups create virtual
MattsAssistant devices containing the sum of their selected members. A source
may belong to any number of groups and does not need to be selected as an
individual source.

Create separate config entries when sources use different prices or represent
different source types.

## Metrics

Every individual source and named group receives ten sensors:

- lifetime total;
- today, this week, this month, this quarter and this year;
- the last 24 hours, 7 days, 3 calendar months and 365 days.

Electricity results use kWh. Water and gas results use m³. Derived sensors use
`state_class: total` and the matching Home Assistant device class.

Three rolling months means the same local date and time three months earlier,
with the day clamped when needed. It is not treated as 90 days.

## Pricing

Each config entry accepts an optional numeric entity containing the current
price per kWh or m³. When configured, MattsAssistant creates ten matching
monetary sensors in the Home Assistant currency.

The price may change over time. MattsAssistant applies the active price to later
increments, so tariff changes do not revalue older consumption. Tariff
schedules, supplier APIs and automations remain outside the integration.

## Compatible sources

Cumulative configurations accept `total` and `total_increasing` sensors with the
matching Home Assistant device class and a convertible unit. Rate configurations
accept `measurement` sensors with `power` or `volume_flow_rate` device classes.
The config flow filters candidates and validates their current metadata before
saving them.

## History

Recorder is the source of truth. MattsAssistant reads closed hourly statistics,
normalizes cumulative meter resets and integrates hourly means for power and
flow sources. It reconstructs only periods supported by continuous history and
never imports the open Recorder hour.

Historical price changes come from the selected price entity's Recorder state
history. Source statistics are hourly, so a tariff boundary inside an hour uses
a linear estimate for that hour. The `history_complete` and `estimated`
attributes describe the current result.

## Installation with HACS

1. Add this repository as a custom HACS repository with category `Integration`.
2. Install MattsAssistant and restart Home Assistant.
3. Add MattsAssistant from **Settings > Devices & services**.
4. Choose a meter source type or Light Group+. Meter configurations accept
   individual sensor rows, named groups and an optional current price. Light
   Group+ accepts member lights, scenes, a default scene and turn-on behavior.

The minimum supported Home Assistant version is 2026.2.

## Development

The test suite requires Python 3.13.2.

```bash
uv sync
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

The architecture is documented in
[`docs/architecture.md`](docs/architecture.md).
