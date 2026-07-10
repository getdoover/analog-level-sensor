# Alarm System — Design Specification

This describes the configurable alarm built for the **4-20mA Sensor** app
(`~/4-20ma-sensor`, branch `bump-pydoover-1.9.0`). It exists so the same alarm
can be implemented in **analog-level-sensor** with identical behaviour, wording,
and config surface.

The reference implementation is:

| File | Role |
|---|---|
| `src/sensor_4_20ma/alarm.py` | Pure logic: `evaluate()` + debouncing `Alarm` |
| `src/sensor_4_20ma/app_config.py` | `AlarmConfig` + fallback properties |
| `src/sensor_4_20ma/app_ui.py` | The two sliders, and mode-driven visibility |
| `src/sensor_4_20ma/application.py` | Wiring: read sliders → evaluate → notify |
| `tests/test_alarm.py`, `tests/test_app_alarm.py` | Behaviour + regression tests |

Requires **pydoover >= 1.9.0**. `analog-level-sensor` already locks 1.9.0.

---

## 1. What it does

An operator turns the alarm on in config, picks one of three types, and sets the
alarm point(s) with a slider in the UI. When the tracked reading crosses a bound
and stays across it, the app sends a notification through Doover's built-in
notification system.

| Alarm type | Slider | Triggers when |
|---|---|---|
| Greater Than | single handle | `reading > point` |
| Less Than | single handle | `reading < point` |
| Allowed Range | dual handle | `reading > high` **or** `reading < low` |

The config sets the slider's **bounds** (the span it can be dragged across).
The UI slider sets the **alarm point(s)** within those bounds. These are two
different things and conflating them is the easiest mistake to make here.

---

## 2. Config surface

Nested under an `alarm` object in the app schema.

```python
class AlarmConfig(config.Object):
    alarm_enabled     = config.Boolean("Alarm Enabled", default=False)
    alarm_type        = config.Enum("Alarm Type", choices=AlarmType,
                                    default=AlarmType.greater_than)
    slider_min        = config.Number("Alarm Slider Minimum", default=None)
    slider_max        = config.Number("Alarm Slider Maximum", default=None)
    grace_period      = config.Number("Alarm Grace Period (s)", default=30.0, minimum=0.0)
    renotify_interval = config.Number("Alarm Re-notify Interval (s)", default=900.0, minimum=0.0)
```

`slider_min`/`slider_max` are optional. When left unset they fall back to the
sensor's own physical range, exposed as properties on the schema:

```python
@property
def alarm_slider_min(self) -> float:
    value = self.alarm.slider_min.value
    return self.min_range.value if value is None else value
```

**In analog-level-sensor there is no `min_range`/`max_range`.** You must decide
what the slider spans and what the alarm actually tracks — level in metres,
filled percentage, or volume. That choice is yours to make; it is the one part
of this spec that does not port mechanically. Whatever you pick, keep the same
config field names and display names so the two apps read alike.

### Published key names come from the *display name*, not the attribute

This surprises people. `sanitize_display_name()` generates the key that appears
in `doover_config.json` and in any injected deployment config:

| Attribute | Display name | Published key |
|---|---|---|
| `slider_min` | `Alarm Slider Minimum` | `alarm_slider_minimum` |
| `grace_period` | `Alarm Grace Period (s)` | `alarm_grace_period_s` |
| `renotify_interval` | `Alarm Re-notify Interval (s)` | `alarm_renotify_interval_s` |

So a test that injects a deployment config must use `alarm_slider_minimum`, not
`slider_min`. Keep the display names identical across both apps and the keys
match for free.

---

## 3. UI: two sliders, not one

`ui.Slider` has a `dual_slider` flag. The two forms report **different value
shapes**:

- single handle → a plain number, e.g. `85.0`
- dual handle → a two-element list, e.g. `[1000, 4000]`

Because of that, use **two separate elements** and hide the one the current mode
does not need:

```python
alarm_point = ui.Slider("Alarm Point", name="alarm_point",
                        dual_slider=False, inverted=False, hidden=True)
alarm_range = ui.Slider("Allowed Range", name="alarm_range",
                        dual_slider=True,  inverted=False, hidden=True)
```

Do **not** use one element and flip `dual_slider` based on config. The value is
persisted per element name in the `ui_cmds` aggregate, so switching modes would
leave a number where the frontend expects a list (or the reverse), and the
element breaks.

Bounds, units and visibility are applied at runtime in `UI.setup()`:

```python
def _setup_alarm(self):
    alarm_type = self.config.alarm_type
    enabled = self.config.alarm.alarm_enabled.value
    is_range = alarm_type is AlarmType.allowed_range

    for slider in (self.alarm_point, self.alarm_range):
        slider.min_val = self.config.alarm_slider_min
        slider.max_val = self.config.alarm_slider_max
        slider.units = self.config.measurement_units.value

    self.alarm_point.hidden = not enabled or is_range
    self.alarm_range.hidden = not enabled or not is_range

    if alarm_type is AlarmType.greater_than:
        self.alarm_point.display_name = "High Alarm Point"
    elif alarm_type is AlarmType.less_than:
        self.alarm_point.display_name = "Low Alarm Point"
```

The exported UI schema carries the constructor defaults (`min=0, max=100`);
`setup()` overrides them once real config is present. That is the normal
semi-static pattern, the same one `multiplot.hidden` already uses.

---

## 4. Alarm logic

Two pieces, deliberately separated so the logic is testable without a device.

### `evaluate()` — pure, no timing

```python
def evaluate(value, alarm_type, point=None, low=None, high=None) -> Breach | None
```

Returns a `Breach(direction, bound)` or `None`. Key design point:

> **An Allowed Range breach reports the bound that was actually crossed.**

That is what collapses three modes into two directions. Crossing the upper bound
is `exceeded`; crossing the lower is `dropped below` — whether that bound came
from a single-point slider or the top/bottom of a range. Every alarm therefore
carries one direction and one number, and the message reads identically in all
three modes.

Returns `None` when the reading is `None`, or when the bound it needs is unset.
"No bound configured" is a normal, silent state — not an error.

### `Alarm` — debouncing

```python
alarm.update(breach) -> bool   # True means "notify now"
```

- A breach must persist for `grace_period` seconds before the first
  notification. This stops a reading hovering on a bound from firing every
  sample.
- While the breach continues, re-notify every `renotify_interval` seconds.
- Returning within bounds calls `clear()`. The next breach serves its **own**
  full grace period, rather than notifying instantly.

Uses `time.monotonic()`, not `time.time()`, so a clock adjustment on the device
cannot strand an alarm mid-grace-period.

---

## 5. Notification

```python
await self.send_notification(
    self._alarm_message(reading, breach),
    title=f"{self.app_display_name} alarm",
    severity=NotificationSeverity.Warn,
)
```

`send_notification` is on `pydoover.docker.Application`; import
`NotificationSeverity` from `pydoover.models`. Severity levels are
Trace/Debug/Info/Warn/Critical — subscribers only receive notifications at or
above their subscription severity.

### Message format — keep this byte-identical across both apps

```
{app_display_name} has {exceeded|dropped below} {bound}{units} with a value of {reading}{units}
```

```python
def _alarm_message(self, reading, breach):
    units = self.config.measurement_units.value
    suffix = f" {units}" if units else ""
    return (
        f"{self.app_display_name} has {breach.direction.value} "
        f"{breach.bound:g}{suffix} with a value of {reading:g}{suffix}"
    )
```

Real output:

```
4-20mA Sensor has exceeded 4000 L with a value of 4500 L
4-20mA Sensor has dropped below 1000 L with a value of 300 L
4-20mA Sensor has exceeded 3000 with a value of 4200      # no units configured
```

`:g` trims float noise, so `4200.0` renders as `4200` and `120.5` stays `120.5`.

**There is no device name.** The docker `Application` only receives
`APP_DISPLAY_NAME` and `AGENT_ID` (a numeric ID) from its deployment config —
there is no agent/device *name* anywhere on `Application` or `device_agent`. The
assumption is that Doover's notification fan-out already identifies the source
device. If it turns out it does not, both apps need revisiting together. Do not
add a device-name config field to one app only.

---

## 6. Three API traps

These cost real debugging time. All three are load-bearing.

### 6.1 An untouched slider raises `KeyError`

`UICommandsManager.get_value()` raises `KeyError` when an element has no stored
value **and** no `default`. These sliders have no `default` (a static default is
meaningless when the bounds come from config). So an operator who enables the
alarm before ever dragging the slider would crash `main_loop` on every sample.

Guard it, and treat unset as "no bound":

```python
@staticmethod
def _slider_value(slider):
    try:
        return slider.value
    except KeyError:
        return None
```

### 6.2 `config.Enum.value` type depends on how you declared `choices`

- `choices=SomeEnumType` → pydoover builds a lookup and `.value` returns the
  **enum member**.
- `choices=["a", "b"]` (a plain list) → the lookup is `None` and `.value`
  returns the **raw string**.

The 4-20mA app passes an `EnumType`, so it normalises defensively:

```python
@property
def alarm_type(self) -> AlarmType:
    value = self.alarm.alarm_type.value
    return value if isinstance(value, AlarmType) else AlarmType(value)
```

**analog-level-sensor currently uses the plain-list style** (see `SensorType` and
`InputUnits` in `src/common/common_config.py`, which are constant-holder classes,
not `enum.Enum`). If you follow that local style, `.value` is a plain string and
you compare against string constants. Either style is fine — but pick one, and
do not assume `.value` gives you a member.

### 6.3 A dual slider's value may arrive reversed

Sort it. Never assume index 0 is the low bound:

```python
low, high = sorted(value)
```

And validate the shape before unpacking — a mode change or a stale aggregate can
hand you something that is not a two-element list:

```python
if not isinstance(value, (list, tuple)) or len(value) != 2:
    return None, None, None
```

---

## 7. Where this goes in analog-level-sensor

That repo has two apps (`analog_level_sensor`, `analog_level_sensor_processor`)
sharing `src/common/`. The alarm logic is app-agnostic, so:

- `src/common/alarm.py` — `evaluate()`, `Alarm`, `AlarmType`, `Breach`, verbatim.
- `src/common/common_config.py` — `AlarmConfig`, plus the slider-bound fallback
  properties on the shared schema.
- `src/common/common_ui.py` — the two sliders and `_setup_alarm()`.
- `src/common/common_app.py` — `_slider_value`, `_alarm_bounds`, `_check_alarm`,
  `_alarm_message`.

Decide deliberately whether **both** apps get the alarm or only the sensor app.
Adding it to the shared config schema publishes the fields to both, since each
app exports its own block in `doover_config.json`.

Adding config fields changes the published schema, so re-run:

```
doover config-schema validate
doover ui-schema validate
```

and commit the regenerated `doover_config.json`, or the `validate-schema` CI job
will drift.

---

## 8. Testing

`evaluate()` and `Alarm` are pure — test them directly. Inject a fake clock by
assigning over the instance attribute (`_now` is a `staticmethod`, so an instance
attribute shadows it cleanly):

```python
alarm = Alarm(grace_period=30.0, renotify_interval=900.0)
alarm._now = FakeClock()
```

For the application wiring, borrow the real methods onto a stub rather than
constructing an `Application` (which needs a device agent connection):

```python
class StubApp:
    app_display_name = "4-20mA Sensor"
    _slider_value = staticmethod(Sensor420maApplication._slider_value)
    _alarm_bounds  = Sensor420maApplication._alarm_bounds
    _check_alarm   = Sensor420maApplication._check_alarm
```

Note the `staticmethod()` re-wrap — accessing `App._slider_value` off the class
unwraps the descriptor, and re-binding the bare function would make it take
`self`.

Cases worth covering, all present in `tests/test_app_alarm.py`:

- untouched slider does not crash the loop (regression for 6.1)
- each of the three modes produces the exact expected message string
- Allowed Range reports the crossed bound, both directions
- a reversed `[high, low]` slider value still reads correctly (regression for 6.3)
- a reading inside the range is silent
- a disabled alarm never notifies, even on a breach
- units omitted cleanly when `measurement_units` is unset
- grace period, re-notify interval, and clear-then-rearm timing
