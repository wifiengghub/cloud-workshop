import os
import time
import psycopg2
from psycopg2.extras import RealDictCursor

from .data import ML_FEATURE_COLUMNS


def get_conn(retries: int = 1, retry_delay: int = 5):
    dsn = os.getenv("TS_DB_DSN")
    for attempt in range(1, retries + 1):
        try:
            if dsn:
                return psycopg2.connect(dsn, cursor_factory=RealDictCursor)
            return psycopg2.connect(
                host=os.getenv("TS_HOST", os.getenv("DB_HOST", "host.docker.internal")),
                port=int(os.getenv("TS_PORT", os.getenv("DB_PORT", 5433))),
                user=os.getenv("TS_USER", os.getenv("DB_USER", "postgres")),
                password=os.getenv("TS_PASS", os.getenv("DB_PASSWORD", "postgres")),
                dbname=os.getenv("TS_DB", os.getenv("DB_NAME", "telemetry")),
                cursor_factory=RealDictCursor,
            )
        except psycopg2.OperationalError:
            if attempt == retries:
                raise
            time.sleep(retry_delay)


def _table_name() -> str:
    return os.getenv("DB_TABLE", os.getenv("TS_TABLE", "telemetry"))


def fetch_window_features(conn, interval_minutes=None):
    """
    Fetch the latest telemetry snapshot per (device_id, radio) for inference.

    Important: do NOT average counters like tx_retries/tx_failed over a time
    window. Training samples are per-message snapshots (e.g. tx_retries=0 on
    a healthy poll). Averaging mixes stale high values into an otherwise
    healthy latest row and causes false anomalies.

    Env:
      POLL_LOOKBACK_MINUTES  - only include devices with a row newer than this
                               (default 2)
      DB_TABLE / TS_TABLE    - table name (default telemetry)
    """
    lookback = int(
        interval_minutes
        if interval_minutes is not None
        else os.getenv("POLL_LOOKBACK_MINUTES", "2")
    )
    # Keep a slightly wider scan window so DISTINCT ON can pick latest row,
    # but only return devices that updated within lookback.
    scan_minutes = max(lookback, int(os.getenv("WINDOW_MINUTES", "5")))
    table = _table_name()
    feature_cols = ",\n      ".join(ML_FEATURE_COLUMNS)

    sql = f"""
    SELECT
      device_id,
      radio,
      timestamp,
      {feature_cols}
    FROM (
      SELECT
        device_id,
        radio,
        timestamp,
        {feature_cols},
        ROW_NUMBER() OVER (
          PARTITION BY device_id, radio
          ORDER BY timestamp DESC
        ) AS rn
      FROM public.{table}
      WHERE timestamp > NOW() - INTERVAL '{scan_minutes} minutes'
    ) ranked
    WHERE rn = 1
      AND timestamp > NOW() - INTERVAL '{lookback} minutes'
    ORDER BY device_id, radio;
    """

    with conn.cursor() as cur:
        cur.execute(sql)
        return cur.fetchall()
