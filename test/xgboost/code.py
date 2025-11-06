import xgboost as xgb
import pandas as pd
import numpy as np
from typing import Dict
import json
import os

model_path = "./model.json"
model = None

def load(path: str):
    global model
    global model_path
    if os.path.exists(path):
        model = xgb.Booster()
        model.load_model(path)
        model_path = path
        print("✅ Model loaded from", path)
        return {"status":"loaded"}
    else:
        model = None
        print("No model found, please call train first")
        return {"status":"model not found"}

def train(dataset: str, target: str):
    global model
    global model_path
    df = pd.read_csv(dataset)
    y = df[target]
    X = df.drop(columns=[target]).select_dtypes(include=["number"])
    dtrain = xgb.DMatrix(X, label=y)

    model = xgb.train(params={"objective": "reg:squarederror"}, dtrain=dtrain, num_boost_round=10)
    model.save_model(model_path)

    # сохраняем порядок признаков
    with open("features.json", "w") as f:
        json.dump(list(X.columns), f)

    return {"status": "trained", "features": list(X.columns)}


def predict(features: Dict[str, float]):
    import numpy as np
    import pandas as pd
    import json
    import os

    global model

    if model is None:
        return {"status": "error", "message": "Model not loaded. Call 'load' or 'train' first."}

    features_file = "features.json"
    if not os.path.exists(features_file):
        return {"status": "error", "message": "Missing features.json. Train model first."}

    try:
        with open(features_file, "r") as f:
            expected_features = json.load(f)
    except Exception as e:
        return {"status": "error", "message": f"Failed to load feature names: {e}"}

    missing = [f for f in expected_features if f not in features]
    if missing:
        return {"status": "error", "message": f"Missing features: {missing}"}

    try:
        df = pd.DataFrame([features], columns=expected_features)
        dmatrix = xgb.DMatrix(df)
        preds = model.predict(dmatrix)
        return {
            "status": "ok",
            "predictions": preds.tolist()
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Prediction failed: {str(e)}"
        }

