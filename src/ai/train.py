import argparse
import json
import pandas as pd
from pathlib import Path
from sklearn.model_selection import train_test_split
from sklearn.metrics import classification_report, roc_auc_score, confusion_matrix
from sklearn.preprocessing import StandardScaler
import lightgbm as lgb
from .data import (
    load_json_training_data,
    load_csv_training_data,
    prepare_ml_features,
    ML_FEATURE_COLUMNS
)
from .model_io import save_model
from sklearn.ensemble import IsolationForest

def main(args):
    """
    Train anomaly detection model from JSON or CSV data.
    
    For JSON: Expects rows with all DB columns and a 'label' field (0=healthy, 1=anomaly)
    For CSV: Can optionally compute labels or expect a label column
    """
    print("="*70)
    print("Starting Model Training")
    print("="*70)
    
    # Load training data
    if args.data.endswith('.json'):
        print(f"\n📥 Loading JSON training data from {args.data}...")
        features_df, labels = load_json_training_data(args.data)
        X_raw = features_df
    else:
        print(f"\n📥 Loading CSV training data from {args.data}...")
        df = load_csv_training_data(args.data)
        # If CSV has labels, use them; otherwise try to compute
        if 'label' in df.columns:
            labels = df['label'].copy()
            X_raw = df.drop('label', axis=1)
        else:
            # For CSV without labels, would need to compute them
            # For now, require labels
            raise ValueError("CSV data must include 'label' column or use JSON format")
    
    print(f"   - Loaded {len(X_raw)} samples")
    print(f"   - Available columns: {len(X_raw.columns)}")
    
    # Prepare ML features
    print(f"\n🔧 Preparing features...")
    X, feature_cols = prepare_ml_features(X_raw)
    print(f"   - Selected {len(feature_cols)} numeric features")
    print(f"   - Features: {feature_cols[:5]}..." if len(feature_cols) > 5 else f"   - Features: {feature_cols}")
    
    # Handle missing values
    X = X.fillna(0)
    
    # Print label distribution
    print(f"\n📊 Label distribution:")
    label_counts = labels.value_counts().sort_index()
    for label_val, count in label_counts.items():
        label_name = "healthy" if label_val == 0 else "anomaly"
        pct = count / len(labels) * 100
        print(f"   - {label_val} ({label_name}): {count} samples ({pct:.1f}%)")
    
    # Check if we have both classes
    if labels.nunique() < 2:
        print(f"\n⚠️  WARNING: Labels contain only one class. Using unsupervised anomaly detector.")
        # Train an IsolationForest on the features
        iso = IsolationForest(random_state=42, n_estimators=100, contamination=0.1)
        iso.fit(X)
        # Save model with metadata
        save_model({
            "model": iso,
            "type": "unsupervised",
            "feature_columns": feature_cols,
            "scaler": None,
            "model_version": "1.0"
        }, args.out)
        print(f"\n✅ Unsupervised model saved to {args.out}")
        return

    # Split data for training and testing
    print(f"\n🔀 Splitting data (80/20 train/test)...")
    X_train, X_test, y_train, y_test = train_test_split(
        X, labels, test_size=0.2, random_state=42, stratify=labels
    )
    print(f"   - Training samples: {len(X_train)}")
    print(f"   - Test samples: {len(X_test)}")
    
    # Normalize features
    print(f"\n📏 Normalizing features with StandardScaler...")
    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    X_test_scaled = scaler.transform(X_test)
    
    # Train LightGBM model
    print(f"\n🚀 Training LightGBM classifier...")
    model = lgb.LGBMClassifier(
        n_estimators=100,
        learning_rate=0.1,
        max_depth=7,
        min_child_samples=5,
        random_state=42,
        verbose=-1,
        num_leaves=31
    )
    model.fit(
        X_train_scaled, 
        y_train, 
        eval_set=[(X_test_scaled, y_test)],
        callbacks=[lgb.log_evaluation(period=0)]
    )
    
    # Evaluate model
    print(f"\n📈 Model Evaluation:")
    y_pred = model.predict(X_test_scaled)
    y_proba = model.predict_proba(X_test_scaled)[:, 1]
    
    print("\n" + "="*70)
    print("Classification Report:")
    print("="*70)
    print(classification_report(y_test, y_pred, target_names=['healthy', 'anomaly']))
    
    # Confusion matrix
    cm = confusion_matrix(y_test, y_pred)
    print(f"\nConfusion Matrix:")
    print(f"                Predicted")
    print(f"                Healthy  Anomaly")
    print(f"Actual Healthy    {cm[0,0]:5d}    {cm[0,1]:5d}")
    print(f"       Anomaly    {cm[1,0]:5d}    {cm[1,1]:5d}")
    
    try:
        auc = roc_auc_score(y_test, y_proba)
        print(f"\n🎯 ROC-AUC Score: {auc:.4f}")
    except Exception as e:
        print(f"\n(ROC-AUC not available: {e})")
    
    # Feature importance (top 10)
    feature_importance = pd.DataFrame({
        'feature': feature_cols,
        'importance': model.feature_importances_
    }).sort_values('importance', ascending=False)
    
    print(f"\n⭐ Top 10 Important Features:")
    for idx, row in feature_importance.head(10).iterrows():
        print(f"   {row['feature']:40s}: {row['importance']:8.4f}")
    
    # Save model with metadata
    print(f"\n💾 Saving model...")
    model_metadata = {
        "model": model,
        "type": "supervised",
        "feature_columns": feature_cols,
        "scaler": scaler,
        "model_version": "1.0",
        "training_info": {
            "total_samples": len(X),
            "training_samples": len(X_train),
            "test_samples": len(X_test),
            "feature_count": len(feature_cols),
            "roc_auc": float(auc) if 'auc' in locals() else None,
            "accuracy": float((y_pred == y_test).mean())
        }
    }
    save_model(model_metadata, args.out)
    
    print(f"\n✅ Model successfully saved to {args.out}")
    print(f"   - Type: Supervised LightGBM")
    print(f"   - Features: {len(feature_cols)}")
    print(f"   - Accuracy: {(y_pred == y_test).mean():.2%}")
    print("="*70)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description='Train WiFi congestion anomaly detection model')
    p.add_argument('--data', required=True, help='Path to training data (JSON or CSV)')
    p.add_argument('--out', default='model_artifacts/model.pkl', help='Output model path')
    args = p.parse_args()
    main(args)
