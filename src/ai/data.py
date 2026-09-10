import pandas as pd
import numpy as np
import json
from pathlib import Path
from typing import Dict, List, Any, Tuple

# WiFi congestion metrics from telemetry
CONGESTION_METRICS = [
    'channel_utilization_pct',
    'cca_busy_pct',
    'tx_airtime_pct',
    'rx_airtime_pct',
    'noise_floor_dbm',
    'neighbor_ap_count',
    'strong_neighbor_ap_count',
    'same_channel_ap_count',
    'strong_same_channel_ap_count',
    'obss_utilization_pct',
    'interference_utilization_pct'
]

# All numeric features that can be used for ML model training
# These come directly from the radio_stats table
ML_FEATURE_COLUMNS = [
    'channel_utilization_pct',
    'tx_airtime_pct',
    'rx_airtime_pct',
    'cca_busy_pct',
    'noise_floor_dbm',
    'client_count',
    'active_client_count',
    'avg_rssi_dbm',
    'min_rssi_dbm',
    'avg_snr_db',
    'min_snr_db',
    'avg_tx_rate_mbps',
    'avg_rx_rate_mbps',
    'tx_retries',
    'tx_failed',
    'tx_airtime_client_pct',
    'rx_airtime_client_pct',
    'avg_mcs',
    'min_mcs',
    'avg_nss',
    'weak_client_count',
    'neighbor_ap_count',
    'strong_neighbor_ap_count',
    'same_channel_ap_count',
    'strong_same_channel_ap_count',
    'obss_utilization_pct',
    'interference_utilization_pct',
    'bandwidth_mhz',
    'channel',
    'frequency_mhz',
]

def load_json_training_data(json_path: str) -> Tuple[pd.DataFrame, pd.Series]:
    """
    Load training data from JSON file with labels.
    
    JSON format:
    {
      "rows": [
        {
          "timestamp": "...",
          "device_id": "...",
          ...all db columns...,
          "label": 0 or 1
        },
        ...
      ]
    }
    
    Args:
        json_path: Path to JSON training file
        
    Returns:
        Tuple of (features_df, labels_series)
    """
    with open(json_path, 'r') as f:
        data = json.load(f)
    
    rows = data.get('rows', [])
    if not rows:
        raise ValueError(f"No rows found in JSON file: {json_path}")
    
    df = pd.DataFrame(rows)
    
    # Extract labels
    if 'label' not in df.columns:
        raise ValueError("JSON data must include 'label' column (0=healthy, 1=anomaly)")
    
    labels = df['label'].copy()
    
    # Remove non-feature columns
    exclude_cols = {'label', 'timestamp', 'scenario', 'schema_version', 'network_type', 'ifname'}
    feature_cols = [col for col in df.columns if col not in exclude_cols]
    
    features_df = df[feature_cols].copy()
    
    return features_df, labels


def load_csv_training_data(csv_path: str) -> pd.DataFrame:
    """
    Load training data from CSV file.
    
    Args:
        csv_path: Path to CSV training file
        
    Returns:
        DataFrame with telemetry data
    """
    df = pd.read_csv(csv_path)
    df['timestamp'] = pd.to_datetime(df.get('timestamp', df.get('ts', None)))
    return df


def build_features_from_df(df, window_minutes=5):
    """
    Build features from telemetry data for congestion anomaly detection.
    
    Uses WiFi-specific metrics from the congestion table.
    For each device/radio, computes statistics over the window.
    
    Args:
        df: DataFrame with telemetry data (should include device_id, radio, timestamp, and WiFi metrics)
        window_minutes: Aggregation window in minutes
    
    Returns:
        DataFrame with engineered features
    """
    df['timestamp'] = pd.to_datetime(df.get('timestamp', df.get('ts', None)))
    out = []
    
    # Group by device and radio for multi-band analysis
    group_keys = ['device_id', 'radio'] if 'radio' in df.columns else ['device_id']
    
    for group_val, g in df.groupby(group_keys):
        group_dict = {}
        if isinstance(group_val, tuple):
            for key, val in zip(group_keys, group_val):
                group_dict[key] = val
        else:
            group_dict[group_keys[0]] = group_val
            
        g_sorted = g.sort_values('timestamp')
        
        # Build features for all available congestion metrics
        for metric in CONGESTION_METRICS:
            if metric in g.columns:
                vals = g_sorted[metric].dropna().values
                if len(vals) > 0:
                    group_dict[f'{metric}_mean'] = float(np.mean(vals))
                    group_dict[f'{metric}_max'] = float(np.max(vals))
                    group_dict[f'{metric}_min'] = float(np.min(vals))
                    group_dict[f'{metric}_std'] = float(np.std(vals))
                    # Trend: is metric increasing?
                    if len(vals) > 1:
                        group_dict[f'{metric}_trend'] = float(vals[-1] - vals[0])
                    else:
                        group_dict[f'{metric}_trend'] = 0.0
        
        out.append(group_dict)
    
    return pd.DataFrame(out)


def build_features_for_inference(telemetry_row: Dict[str, Any]) -> Dict[str, float]:
    """
    Extract and normalize features from a single telemetry row for inference.
    
    Args:
        telemetry_row: Single telemetry measurement dict
        
    Returns:
        Dictionary of feature values ready for model inference
    """
    features = {}
    
    # Extract all available congestion metrics
    for metric in CONGESTION_METRICS:
        if metric in telemetry_row:
            val = telemetry_row[metric]
            if val is not None:
                features[metric] = float(val)
    
    return features


def prepare_ml_features(df: pd.DataFrame, feature_columns: List[str] = None) -> pd.DataFrame:
    """
    Prepare ML features from radio_stats rows.
    
    Selects numeric columns, handles missing values, and ensures consistency.
    
    Args:
        df: DataFrame with radio_stats data
        feature_columns: List of feature columns to use (defaults to ML_FEATURE_COLUMNS)
        
    Returns:
        DataFrame with prepared features
    """
    if feature_columns is None:
        feature_columns = ML_FEATURE_COLUMNS
    
    # Select only available columns
    available_cols = [col for col in feature_columns if col in df.columns]
    
    X = df[available_cols].copy()
    
    # Fill missing values with 0
    X = X.fillna(0)
    
    # Ensure all numeric
    for col in X.columns:
        X[col] = pd.to_numeric(X[col], errors='coerce').fillna(0)
    
    return X, available_cols
