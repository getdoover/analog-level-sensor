from pathlib import Path

from pydoover import config
from pydoover.config import ApplicationPosition
from pydoover.processor import ManySubscriptionConfig

from common.common_config import CommonAnalogLevelSensorConfig


class AnalogLevelSensorProcessorConfig(CommonAnalogLevelSensorConfig):
    input_message_path = config.String(
        "Input Message Path",
        description=(
            "Message path containing the raw analog reading in the configured input units. "
            "Use '$channel.path.to.value', for example '$on_dm_event.analog_input_v'."
        ),
        default="$on_dm_event.analog_input_v",
        position=16,
    )
    subscription = ManySubscriptionConfig(
        default=["on_dm_event"],
        advanced=True,
        position=17,
    )

    position = ApplicationPosition(position=18)


def export():
    AnalogLevelSensorProcessorConfig.export(
        Path(__file__).parents[2] / "doover_config.json",
        "analog_level_sensor_processor",
    )
