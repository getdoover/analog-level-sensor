from pydoover.docker import run_app

from .application import AnalogLevelSensorDeviceApplication


def main():
    """
    Run the Analog Level Sensor application
    """
    run_app(AnalogLevelSensorDeviceApplication())
