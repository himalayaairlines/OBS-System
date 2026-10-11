"""Standalone communication worker.

    python worker.py          run forever (production: one per server)
    python worker.py --once   run a single cycle and exit (cron, diagnostics)

When this runs, start the API with OBS_EMBEDDED_WORKER=0 so the API does not
also run its own worker thread (running both is safe, just redundant).
"""

import json
import logging
import sys
import time

from database import initialize_database
import comms_worker

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s %(name)s %(message)s")


def main():
    initialize_database()
    owner = comms_worker.worker_id()
    if "--once" in sys.argv:
        print(json.dumps(comms_worker.run_cycle(owner=owner), indent=2, default=str))
        return
    logging.info("OBS communication worker %s running every %ss. Ctrl-C to stop.",
                 owner, comms_worker.CYCLE_SECONDS)
    while True:
        try:
            comms_worker.run_cycle(owner=owner)
        except Exception:  # noqa: BLE001
            logging.exception("Cycle failed")
        time.sleep(comms_worker.CYCLE_SECONDS)


if __name__ == "__main__":
    main()
