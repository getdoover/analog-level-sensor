from pydoover import notifications


class AnalogLevelSensorDeviceNotifications(notifications.Notifications):
    """Notifications this app can send.

    Declaring them publishes a schema with the app, which is what lets an
    operator turn this notification off for themselves without also losing
    every other notification from the device.
    """

    level_alarm = notifications.Notification(
        # Overridden on every send with the reading and the bound it crossed;
        # this is the fallback wording and what the site shows in the picker.
        "The level has crossed its alarm setpoint",
        display_name="Level alarm",
        description=(
            "Sent when the reading crosses the configured alarm setpoint, and "
            "again on each renotify interval while it stays there."
        ),
        severity=notifications.NotificationSeverity.Warn,
    )


AnalogLevelSensorNotifications = AnalogLevelSensorDeviceNotifications


def export():
    from pathlib import Path

    AnalogLevelSensorDeviceNotifications.export(
        Path(__file__).parents[2] / "doover_config.json", "analog_level_sensor"
    )
