from pydoover.docker import Application

from common.common_app import CommonAnalogLevelSensorApplication

from .app_config import AnalogLevelSensorDeviceConfig
from .app_tags import AnalogLevelSensorDeviceTags
from .app_ui import AnalogLevelSensorDeviceUI


class AnalogLevelSensorDeviceApplication(
    Application,
    CommonAnalogLevelSensorApplication,
):
    config: AnalogLevelSensorDeviceConfig
    tags: AnalogLevelSensorDeviceTags

    config_cls = AnalogLevelSensorDeviceConfig
    tags_cls = AnalogLevelSensorDeviceTags
    ui_cls = AnalogLevelSensorDeviceUI

    async def setup(self):
        if self.config.power_pin.value is not None:
            await self.platform_iface.set_do(int(self.config.power_pin.value), True)

        freq = self.config.polling_frequency.value
        if freq and freq > 0:
            self.loop_target_period = 1 / freq

    async def main_loop(self):
        result = await self.platform_iface.fetch_ai(int(self.config.ai_pin.value))

        if self.config.power_pin.value is not None:
            await self.platform_iface.set_do(int(self.config.power_pin.value), True)

        await self.handle_update(result)

    async def on_shutdown_at(self, _seconds: int):
        if self.config.power_pin.value is not None:
            await self.platform_iface.set_do(int(self.config.power_pin.value), False)


AnalogLevelSensorApplication = AnalogLevelSensorDeviceApplication
