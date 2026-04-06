from pydoover.docker import run_app

from .application import AnalogLevelSensorApplication
from .app_config import AnalogLevelSensorConfig

def main():
    """
    Run the application.
    """
    run_app(AnalogLevelSensorApplication(config=AnalogLevelSensorConfig()))
