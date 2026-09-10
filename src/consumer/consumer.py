import logging
import signal
import sys

from .client import MqttTelemetryConsumer

logger = logging.getLogger(__name__)


def main() -> None:
    consumer = MqttTelemetryConsumer()

    def shutdown(signum, frame):
        logger.info("Received shutdown signal %s", signum)
        consumer.disconnect()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    try:
        consumer.connect()
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received")
    finally:
        consumer.disconnect()


if __name__ == "__main__":
    main()
