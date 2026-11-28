import signal

from .cli import app

if hasattr(signal, "SIGBREAK"):
    # Windows: jobs are cancelled with Ctrl+Break; treat it like Ctrl+C so an
    # interrupted run is recorded as interrupted and can be resumed.
    signal.signal(signal.SIGBREAK, signal.default_int_handler)

app(prog_name="extractor-eval")
