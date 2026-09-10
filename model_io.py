import joblib
from pathlib import Path

def save_model(model, path: str):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, str(p))

def load_model(path: str):
    return joblib.load(path)
