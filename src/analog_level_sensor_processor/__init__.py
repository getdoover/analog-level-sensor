from pydoover.processor import run_app

from .application import AnalogLevelSensorProcessorApplication


def handler(event, context):
    """Lambda handler entry point."""
    return run_app(
        AnalogLevelSensorProcessorApplication(),
        event,
        context,
    )
