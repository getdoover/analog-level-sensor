"""Operator Sensor Calibration (1 October 2026).

Operators tune the 4-20 mA conversion on site, from this app's cloud UI and the
local HMI, without a redeploy:

| Value / RPC / tag | Meaning                     | Range                  | Default without an operator value |
|-------------------|-----------------------------|------------------------|-----------------------------------|
| ``zero_m``        | minimum level (m)           | -100 <= z < span       | config ``sensor_minimum_metres``  |
| ``span_m``        | maximum level (m)           | 0 < s <= 100, s > zero | config ``sensor_maximum_metres``  |
| ``fluid_density`` | kg/m³ (submersible scaling) | 500..2500              | config ``fluid_density`` (1000)   |

Always metres, whatever ``depth_units`` is. The input endpoints (4 / 20 mA) and
the volume curve stay config-only. The zero and span map the input range onto
metres the way the config fields always have: for a Submersible or a Radar
Inverted the zero is the level at the minimum input (4 mA) and the span the
level at the maximum input (20 mA); a Radar reads inverted, so its zero is the
level at 20 mA and its span the level at 4 mA. The density only scales a
submersible reading.

The zero may be negative: the minimum level is below the tank datum when the
sensor's minimum-level end sits below it (a submersible / hydrostatic
transmitter mounted below or offset from the tank floor, or whose 4 mA point is
below it; a Radar whose 20 mA end reaches below the floor). The level then
reads below 0 m near that end, and the percentage and volume follow it
unclamped, as they already do for any level below the configured empty level.
The span (the maximum level) stays above 0 m: a range wholly below the datum
is not a tank level. So in both orientations it is the minimum-level end that
may be negative: the 4 mA end for a Submersible or Radar Inverted, the 20 mA
end for a Radar.

Everything is gated on the ``operator_calibration_enabled`` config field
("Operator Sensor Calibration", default off). Off: the "Sensor Calibration"
submodule is hidden, the RPCs are refused with ``UNAVAILABLE``, the readback
tags are never published and the conversion uses the config exactly as before.

On: the value in effect is the operator value when a valid one is set, else the
config default, read on every reading, so a change applies to the next reading.
Each value is stored in ui_cmds under its element name, so it survives a restart
and the cloud input shows an HMI change. The readback tags (``zero_m``,
``span_m``, ``fluid_density``, ``operator_calibration``) carry the values in
effect for the HMI. No notification is sent on a change.

"Reset to configured values" (``reset_calibration``) clears the operator values.
So that the cloud inputs show what is in effect, it writes each config default
into ui_cmds, together with a ``<name>_default`` marker holding the same number:
a ui_cmds value equal to its marker is "no operator value", so a later change to
the config default still takes effect (and is written back so the input shows
it), where a plain stored number would have become an operator value.
"""

from __future__ import annotations

import logging
import math
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, NamedTuple

from pydoover.rpc import RPCError

from common.common_app import config_fluid_density

if TYPE_CHECKING:
    from .application import AnalogLevelSensorDeviceApplication

log = logging.getLogger(__name__)

RESET_ELEMENT = "reset_calibration"
UNAVAILABLE_MESSAGE = "Enable Operator Sensor Calibration on the sensor app"

# Bounded wait for the ui_cmds aggregate at startup (see _await_ui_cmds_sync).
UI_CMDS_SYNC_TIMEOUT_SECS = 10
UI_CMDS_SYNC_POLL_SECS = 0.25

DECIMALS = 4


def _finite(value) -> float | None:
    """*value* as a finite float, or None."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _round(number: float) -> float:
    # + 0.0 turns -0.0 (e.g. -0.00004 rounded) into 0.0, which prints as "0".
    return round(number, DECIMALS) + 0.0


def _number(value) -> float | None:
    """*value* as a finite float rounded to 4 dp, or None."""
    number = _finite(value)
    return None if number is None else _round(number)


def _config_zero(config):
    return config.sensor_min_m.value


def _config_span(config):
    return config.sensor_max_m.value


@dataclass(frozen=True)
class CalibrationValue:
    name: str
    """UI element, RPC method, ui_cmds key and readback tag."""
    label: str
    """Operator text for logs and errors."""
    units: str
    minimum: float
    maximum: float
    min_inclusive: bool
    max_inclusive: bool
    default: Callable[[object], object]
    """The value without an operator value, from the deployment config."""

    @property
    def marker(self) -> str:
        """ui_cmds key marking the stored value as the config default."""
        return f"{self.name}_default"

    def parse(self, value) -> float | None:
        """*value* as a float within range (rounded to 4 dp), or None.

        Both the value as given and the rounded one must be in range: 100.00004
        is not a span of at most 100, and a span of 0.00004 would be stored as
        0, which is not above the minimum.
        """
        number = _finite(value)
        if number is None:
            return None
        rounded = _round(number)
        if self._in_range(number) and self._in_range(rounded):
            return rounded
        return None

    def _in_range(self, number: float) -> bool:
        above = number >= self.minimum if self.min_inclusive else number > self.minimum
        below = number <= self.maximum if self.max_inclusive else number < self.maximum
        return above and below

    def config_default(self, config) -> float | None:
        """The config default as a float (None when the config has none)."""
        try:
            value = self.default(config)
        except (AttributeError, KeyError, TypeError, ValueError):
            return None
        if value is None or isinstance(value, bool):
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None

    def describe_range(self) -> str:
        low = "" if self.min_inclusive else "above "
        high = "" if self.max_inclusive else "below "
        return (
            f"{low}{self.minimum:g} to {high}{self.maximum:g}"
            f"{' ' + self.units if self.units else ''}"
        )


ZERO = CalibrationValue(
    name="zero_m",
    label="zero",
    units="m",
    # Negative: the minimum level may sit below the tank datum (module docstring).
    minimum=-100.0,
    maximum=100.0,
    min_inclusive=True,
    max_inclusive=False,
    default=_config_zero,
)
SPAN = CalibrationValue(
    name="span_m",
    label="span",
    units="m",
    minimum=0.0,
    maximum=100.0,
    min_inclusive=False,
    max_inclusive=True,
    default=_config_span,
)
DENSITY = CalibrationValue(
    name="fluid_density",
    label="fluid density",
    units="kg/m³",
    minimum=500.0,
    maximum=2500.0,
    min_inclusive=True,
    max_inclusive=True,
    default=config_fluid_density,
)

VALUES: tuple[CalibrationValue, ...] = (ZERO, SPAN, DENSITY)
BY_NAME = {v.name: v for v in VALUES}

# The one ui_cmds handler for the three value RPCs (application.on_calibration_value).
RPC_PATTERN = re.compile("(?:" + "|".join(re.escape(v.name) for v in VALUES) + r")\Z")


class Effective(NamedTuple):
    """The values the conversion uses now."""

    zero_m: float | None
    span_m: float | None
    fluid_density: float


# (value, marker) as read from ui_cmds, each a 4 dp float or None.
Observed = tuple[float | None, float | None]


class OperatorValue:
    """One calibration value's operator setting on the running app.

    The same pattern as sia-injection-controller's alarm delays. Every write
    (cloud / HMI RPC, reset) goes through :class:`SensorCalibration`, which
    writes ui_cmds; the value in effect is held here rather than read back
    from ``ui_manager.values``, which only changes when an aggregate event
    arrives.

    A ui_cmds value is adopted when it differs from the one last seen there:
    at start-up once ui_cmds has synced (before that, or with none set, the
    config default applies), and when it is changed directly in the aggregate.
    An invalid ui_cmds value is ignored. A value equal to its ``_default``
    marker means "no operator value" (see the module docstring).

    The app's own writes come back as aggregate events in the order they were
    written, so each write is kept until its echo arrives (``_pending``). An
    echo is adopted only when no newer write is still waiting, so two quick
    writes never put the older value back in effect. Anything else is a direct
    edit and is adopted. An echo not seen within ``ECHO_TIMEOUT_SECS`` is
    forgotten.

    If ui_cmds first delivers a value after a local write (it synced late,
    e.g. offline at boot), that value predates the write: the write stays in
    effect and is stored again by :meth:`SensorCalibration.maintain`.

    Offline reboot: when ui_cmds has not synced at start-up, the last value in
    effect, persisted in the readback tag, is used (:meth:`restore`) until
    ui_cmds delivers.
    """

    ECHO_TIMEOUT_SECS = 60.0

    def __init__(self, calibration: SensorCalibration, spec: CalibrationValue):
        self.calibration = calibration
        self.spec = spec
        # In effect (None = no operator value, the config default applies) and
        # the ui_cmds (value, marker) last seen.
        self._value: float | None = None
        self._seen: Observed | None = None
        # This app's writes not yet echoed back, oldest first: (observed, when).
        self._pending: list[tuple[Observed, float]] = []
        self._unsynced_write = False
        self._repersist = False
        # Restored from the readback tag at an offline boot; dropped if ui_cmds
        # then delivers without a value for this setting.
        self._provisional = False

    # -- reading ---------------------------------------------------------------

    def default(self) -> float | None:
        return self.spec.config_default(self.calibration.config)

    def _observed(self) -> Observed | None:
        values = self.calibration.ui_values()
        if self.spec.name not in values:
            return None
        return (
            self.spec.parse(values.get(self.spec.name)),
            _number(values.get(self.spec.marker)),
        )

    @staticmethod
    def _operator_value(observed: Observed) -> float | None:
        value, marker = observed
        return None if value == marker else value

    def setting(self) -> float | None:
        """The operator value in effect, or None when none is set."""
        observed = self._observed()
        if (
            observed is None
            and self._provisional
            and self.calibration.ui_cmds_delivered()
        ):
            # ui_cmds has synced and holds no value: the restored one was the
            # default of an older config.
            self._provisional = False
            self._value = None
        if self._pending:
            expired = time.monotonic() - self.ECHO_TIMEOUT_SECS
            self._pending = [p for p in self._pending if p[1] >= expired]
        if observed != self._seen:
            self._seen = observed
            if observed is not None and observed[0] is not None:
                stored = self._operator_value(observed)
                echoed = next(
                    (i for i, (o, _t) in enumerate(self._pending) if o == observed),
                    None,
                )
                if echoed is not None:
                    # Our own write; a newer one still on its way wins.
                    del self._pending[: echoed + 1]
                    if not self._pending:
                        self._value = stored
                elif self._unsynced_write and stored != self._value:
                    self._repersist = True
                else:
                    self._value = stored
                self._unsynced_write = False
                self._provisional = False
        return self._value

    def value(self) -> float | None:
        """The value in effect: the operator value, else the config default."""
        setting = self.setting()
        return setting if setting is not None else self.default()

    def check(self, value) -> float:
        number = self.spec.parse(value)
        if number is None:
            raise RPCError(
                "INVALID",
                f"the {self.spec.label} must be {self.spec.describe_range()}, "
                f"got {value!r}",
            )
        return number

    # -- start-up --------------------------------------------------------------

    def restore(self, persisted) -> None:
        """Start-up: if ui_cmds did not sync, carry the value in effect before
        the reboot over from its readback tag.

        Only a valid value other than the config default is taken (the tag
        holds the default when no operator value was set). ui_cmds overrides it
        as soon as it delivers.
        """
        if self.calibration.ui_cmds_synced or self._observed() is not None:
            return
        number = self.spec.parse(persisted)
        if number is None:
            return
        default = self.default()
        if default is not None and number == round(default, DECIMALS):
            return
        self._value = number
        self._provisional = True
        log.warning(
            "ui_cmds not synced: using the last %s in effect, %s %s, from the "
            "%s tag until it does",
            self.spec.label,
            number,
            self.spec.units,
            self.spec.name,
        )

    # -- writing ---------------------------------------------------------------

    def _begin_write(self) -> None:
        # Take in the ui_cmds value first, so one not yet seen is not adopted
        # over this write on the next read.
        self.setting()
        if self._observed() is None:
            self._unsynced_write = True
        self._provisional = False

    def _payload(self) -> dict:
        """The ui_cmds keys for the value in effect, expecting their echo."""
        if self._value is not None:
            observed: Observed = (self._value, None)
        else:
            default = self.default()
            number = None if default is None else round(default, DECIMALS)
            observed = (number, number)
        last = self._pending[-1][0] if self._pending else self._seen
        if observed[0] is not None and observed != last:
            self._pending.append((observed, time.monotonic()))
        return {self.spec.name: observed[0], self.spec.marker: observed[1]}

    def snapshot(self) -> tuple:
        """The state a write changes, for :meth:`rollback` if it fails."""
        return (
            self._value,
            self._seen,
            list(self._pending),
            self._unsynced_write,
            self._repersist,
            self._provisional,
        )

    def rollback(self, state: tuple) -> None:
        """Undo a :meth:`set_payload` / :meth:`reset_payload` whose ui_cmds
        write failed, so the value in effect is the one before it."""
        (
            self._value,
            self._seen,
            pending,
            self._unsynced_write,
            self._repersist,
            self._provisional,
        ) = state
        self._pending = list(pending)

    def set_payload(self, number: float) -> dict:
        self._begin_write()
        self._value = number
        return self._payload()

    def reset_payload(self) -> dict:
        self._begin_write()
        self._value = None
        return self._payload()

    def maintenance_payload(self) -> dict | None:
        """ui_cmds keys to write again, if any.

        - a late ui_cmds sync delivered an older value over a local write;
        - ui_cmds shows a reset value that is no longer the config default
          (the config changed since), so the cloud input would be stale.
        """
        self.setting()
        if self._repersist:
            self._repersist = False
            log.info(
                "ui_cmds synced an older %s; keeping %s", self.spec.label, self._value
            )
            return self._payload()
        observed = self._seen
        if (
            self._value is None
            and not self._pending
            and observed is not None
            and observed[0] is not None
            and observed[0] == observed[1]
        ):
            default = self.default()
            if default is not None and round(default, DECIMALS) != observed[0]:
                return self._payload()
        return None


class SensorCalibration:
    """The three operator values, their RPCs and readback tags."""

    def __init__(self, app: AnalogLevelSensorDeviceApplication):
        self.app = app
        self.values = {spec.name: OperatorValue(self, spec) for spec in VALUES}
        self.ui_cmds_synced = False
        self._inverted_logged: tuple | None = None

    @property
    def config(self):
        return self.app.config

    @property
    def enabled(self) -> bool:
        return self.app.config.operator_calibration_enabled

    def ui_values(self) -> dict:
        try:
            values = self.app.ui_manager.values
        except AttributeError:
            return {}
        return values if isinstance(values, dict) else {}

    def ui_cmds_delivered(self) -> bool:
        return bool(self.ui_values())

    # -- the values in effect ----------------------------------------------------

    def effective(self) -> Effective:
        zero = self.values[ZERO.name]
        span = self.values[SPAN.name]
        zero_m, span_m = zero.value(), span.value()
        if (
            zero_m is not None
            and span_m is not None
            and zero_m >= span_m
            and (zero.setting() is not None or span.setting() is not None)
        ):
            # Only reachable by a direct ui_cmds edit or a config change under
            # an operator value (the RPCs refuse it): an inverted range would
            # read backwards, so fall back to the config for both.
            state = (zero_m, span_m)
            if self._inverted_logged != state:
                self._inverted_logged = state
                log.warning(
                    "Operator zero %s m is not below span %s m; using the "
                    "configured zero and span",
                    zero_m,
                    span_m,
                )
            zero_m, span_m = zero.default(), span.default()
        return Effective(zero_m, span_m, self.values[DENSITY.name].value())

    # -- start-up and loop -------------------------------------------------------

    async def startup(self) -> None:
        """Before anything publishes. Off: clear readback tags a previous
        enabled run left behind (a no-op on a device that never enabled it).
        On: wait (bounded) for ui_cmds, restore from the tags if it did not
        sync, then publish."""
        if not self.enabled:
            await self._clear_tags()
            return
        self.ui_cmds_synced = await self._await_ui_cmds_sync()
        for name, setting in self.values.items():
            setting.restore(self.app.tags[name].get())
        await self.publish()

    async def maintain(self) -> None:
        """Every loop while enabled: rewrite ui_cmds where needed and publish
        the readback tags."""
        if not self.enabled:
            return
        payload = {}
        for setting in self.values.values():
            keys = setting.maintenance_payload()
            if keys:
                payload.update(keys)
        if payload:
            try:
                await self._write(payload)
            except Exception as e:  # noqa: BLE001 - a UI write must not stop the loop
                log.warning("Could not store the sensor calibration: %s", e)
        await self.publish()

    async def publish(self) -> None:
        effective = self.effective()
        await self.app.tags.zero_m.set(effective.zero_m)
        await self.app.tags.span_m.set(effective.span_m)
        await self.app.tags.fluid_density.set(effective.fluid_density)
        await self.app.tags.operator_calibration.set(True)

    async def _clear_tags(self) -> None:
        for name in (*self.values, "operator_calibration"):
            tag = self.app.tags[name]
            if tag.get() is not None:
                await tag.set(None)

    async def _await_ui_cmds_sync(self) -> bool:
        """pydoover subscribes ui_cmds but does not wait for it before
        ``setup()``; until it syncs ``ui_manager.values`` is empty. Never
        raises: on timeout the readback tags carry the values over."""
        try:
            synced = await self.app.device_agent.wait_for_channels_sync(
                ["ui_cmds"],
                timeout=UI_CMDS_SYNC_TIMEOUT_SECS,
                inter_wait=UI_CMDS_SYNC_POLL_SECS,
            )
        except Exception as e:  # noqa: BLE001 - must not stop start-up
            log.warning("Could not wait for ui_cmds sync: %s", e)
            return False
        if not synced:
            log.warning(
                "ui_cmds did not sync within %ss; sensor calibration from the "
                "readback tags until it does",
                UI_CMDS_SYNC_TIMEOUT_SECS,
            )
        return bool(synced)

    # -- RPCs --------------------------------------------------------------------

    def _require_enabled(self) -> None:
        if not self.enabled:
            raise RPCError("UNAVAILABLE", UNAVAILABLE_MESSAGE)

    async def request(self, name: str, value) -> float:
        """Set one value. Out of range, or a zero not below the span:
        ``INVALID`` and the value in effect is unchanged."""
        self._require_enabled()
        setting = self.values[name]
        number = setting.check(value)
        current = self.effective()
        zero = number if name == ZERO.name else current.zero_m
        span = number if name == SPAN.name else current.span_m
        if (
            name in (ZERO.name, SPAN.name)
            and zero is not None
            and span is not None
            and not zero < span
        ):
            raise RPCError(
                "INVALID",
                f"the zero ({zero:g} m) must be below the span ({span:g} m)",
            )
        saved = {setting: setting.snapshot()}
        await self._commit(setting.set_payload(number), saved)
        await self.publish()
        log.info(
            "Sensor %s set to %s %s", setting.spec.label, number, setting.spec.units
        )
        return number

    async def reset(self) -> dict:
        """Clear every operator value: back to the config defaults."""
        self._require_enabled()
        saved = {setting: setting.snapshot() for setting in self.values.values()}
        payload = {}
        for setting in self.values.values():
            payload.update(setting.reset_payload())
        await self._commit(payload, saved)
        await self.publish()
        log.info("Sensor calibration reset to the configured values")
        return self.effective()._asdict()

    async def _commit(self, payload: dict, saved: dict) -> None:
        """Store an RPC's change. If ui_cmds cannot be written (e.g. the DDA
        is down) the change is undone and the RPC fails, so the conversion,
        the readback tags and what a restart comes back to all keep the value
        from before it, and the operator's error is true."""
        try:
            await self._write(payload)
        except Exception as e:
            for setting, state in saved.items():
                setting.rollback(state)
            log.warning("Could not store the sensor calibration: %s", e)
            raise RPCError(
                "UNAVAILABLE", "could not store the sensor calibration, try again"
            ) from e

    async def _write(self, payload: dict) -> None:
        """One ui_cmds aggregate write, so a value and its marker (and a
        reset's three values) arrive together in one echo."""
        await self.app.update_channel_aggregate(
            "ui_cmds", {self.app.ui_manager.app_key: payload}
        )
