import logging
from typing import Any

from pydoover.models import MessageCreateEvent
from pydoover.processor import Application

from common.common_app import CommonAnalogLevelSensorApplication

from .app_config import AnalogLevelSensorProcessorConfig
from .app_tags import AnalogLevelSensorProcessorTags
from .app_ui import AnalogLevelSensorProcessorUI


log = logging.getLogger(__name__)


class AnalogLevelSensorProcessorApplication(
    Application,
    CommonAnalogLevelSensorApplication,
):
    config: AnalogLevelSensorProcessorConfig
    tags: AnalogLevelSensorProcessorTags
    ui: AnalogLevelSensorProcessorUI

    config_cls = AnalogLevelSensorProcessorConfig
    tags_cls = AnalogLevelSensorProcessorTags
    ui_cls = AnalogLevelSensorProcessorUI

    async def setup(self):
        self._input_channel, self._input_path = self._parse_input_message_path(
            self.config.input_message_path.value
        )

    async def on_message_create(self, event: MessageCreateEvent):
        if event.channel.name != self._input_channel:
            return

        result = self._extract_path_value(event.message.data or {}, self._input_path)
        if result is None:
            log.info(
                "No analog input reading found at %s in message on %s.",
                ".".join(self._input_path),
                event.channel.name,
            )
            return

        try:
            reading = self._coerce_reading(result)
        except (TypeError, ValueError):
            log.warning(
                "Analog input reading at %s in message on %s is not numeric: %r",
                ".".join(self._input_path),
                event.channel.name,
                result,
            )
            return

        await self.handle_update(reading)

    @staticmethod
    def _parse_input_message_path(value: str) -> tuple[str, list[str]]:
        if not value or not isinstance(value, str):
            raise ValueError("Input Message Path must be a string like '$channel.key'.")

        path = value.strip()
        if not path.startswith("$"):
            raise ValueError("Input Message Path must start with '$channel'.")

        body = path[1:]
        if ":" in body:
            channel, raw_path = body.split(":", 1)
        elif "." in body:
            channel, raw_path = body.split(".", 1)
        else:
            raise ValueError(
                "Input Message Path must include a value path after the channel."
            )

        channel = channel.strip()
        parts = [part.strip() for part in raw_path.split(".") if part.strip()]
        if not channel or not parts:
            raise ValueError(
                "Input Message Path must include both a channel and value path."
            )
        return channel, parts

    @staticmethod
    def _extract_path_value(data: Any, path: list[str]):
        value = data
        for part in path:
            if isinstance(value, dict):
                value = value.get(part)
            elif isinstance(value, list):
                try:
                    value = value[int(part)]
                except (ValueError, IndexError):
                    return None
            else:
                return None

            if value is None:
                return None
        return value

    @staticmethod
    def _coerce_reading(value: Any) -> float:
        if isinstance(value, bool):
            raise TypeError("boolean is not a valid analog input reading")
        return float(value)
