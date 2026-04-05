import logging

from tm1_git_py.progress_reporting import (
    CallbackProgressSink,
    CompositeProgressSink,
    LoggingProgressSink,
    ProgressEvent,
)


def test_callback_progress_sink_accepts_callable_and_forwards_event():
    events = []
    sink = CallbackProgressSink(lambda event: events.append(event))
    event = ProgressEvent.make(kind="scope_update", scope="overall", current=10, total=100, unit="B")
    sink.on_event(event)
    assert events == [event]


def test_callback_progress_sink_accepts_on_progress_object():
    class Handler:
        def __init__(self):
            self.events = []

        def on_progress(self, event):
            self.events.append(event)

    handler = Handler()
    sink = CallbackProgressSink(handler)
    event = ProgressEvent.make(kind="activity", scope="worker", activity="reading")
    sink.on_event(event)
    assert handler.events == [event]


def test_composite_and_logging_progress_sink_emit():
    records = []

    class CaptureHandler(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    logger = logging.getLogger("tm1_git_py.tests.progress")
    logger.setLevel(logging.DEBUG)
    capture = CaptureHandler()
    logger.addHandler(capture)
    logger.propagate = False

    callback_events = []
    composite = CompositeProgressSink(
        [
            LoggingProgressSink(logger),
            CallbackProgressSink(lambda event: callback_events.append(event)),
        ]
    )
    event = ProgressEvent.make(kind="scope_start", scope="file", path="x.json", activity="reading")
    composite.on_event(event)
    composite.close()

    assert callback_events == [event]
    assert any("progress | scope_start file" in message for message in records)

    logger.removeHandler(capture)
