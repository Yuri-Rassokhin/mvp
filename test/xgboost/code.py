import xgboost as xgb
import pandas as pd
import numpy as np
from typing import List
import os

MODEL_PATH = "model.json"
model = None

def load():
    global model
    if os.path.exists(MODEL_PATH):
        model = xgb.Booster()
        model.load_model(MODEL_PATH)
        print("✅ Model loaded from", MODEL_PATH)
    else:
        model = None
        print("No model found, please call train first")

def train(dataset: str, target: str):
    global model
    df = pd.read_csv(dataset)
    y = df[target]
    X = df.drop(columns=[target])
    dtrain = xgb.DMatrix(X, label=y)

    model = xgb.train(params={"objective": "reg:squarederror"}, dtrain=dtrain, num_boost_round=10)
    model.save_model(MODEL_PATH)
    return {"status": "trained", "features": list(X.columns)}

def predict(data: List[List[float]]):
    global model
    if not model:
        return {"error": "model not loaded"}

    dmatrix = xgb.DMatrix(np.array(data))
    preds = model.predict(dmatrix)
    return preds.tolist()

