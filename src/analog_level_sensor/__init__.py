from pydoover.docker import run_app

from .application import AnalogLevelSensorApplication


def main():
    """
    Run the Analog Level Sensor application
    """
    run_app(AnalogLevelSensorApplication())
