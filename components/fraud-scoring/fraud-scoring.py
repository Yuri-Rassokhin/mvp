import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import LabelEncoder
from xgboost import XGBClassifier
from sklearn.metrics import roc_auc_score
import numpy as np
from typing import Dict

model = None
encoders = None
X_train, X_test, y_train, y_test, X = None, None, None, None, None

def train():
    global model
    global encoders
    global X_train, X_test, y_train, y_test, X

    # 1. Загрузка
    df = pd.read_csv("/home/ubuntu/mvp/components/fraud-scoring/PS_20174392719_1491204439457_log.csv")

    # 2. Базовая подготовка
    y = df["isFraud"]
    X = df.drop(columns=["isFraud", "isFlaggedFraud", "nameOrig", "nameDest"])

    # Категориальные фичи (например, 'type')
    for col in X.select_dtypes(include=["object"]).columns:
        le = LabelEncoder()
        X[col] = le.fit_transform(X[col])

    X_train, X_test, y_train, y_test = train_test_split(
    X, y, test_size=0.2, stratify=y, random_state=42
    )

    # 3. XGBoost с учётом имбаланса
    scale_pos_weight = (len(y_train) - y_train.sum()) / y_train.sum()

    model = XGBClassifier(
        n_estimators=300,
        max_depth=6,
        learning_rate=0.1,
        subsample=0.8,
        colsample_bytree=0.8,
        scale_pos_weight=scale_pos_weight,
        eval_metric="aucpr",
        tree_method="hist"
    )

    model.fit(X_train, y_train)

    for col in X.select_dtypes(include=["object"]).columns:
        le = LabelEncoder()
        X[col] = le.fit_transform(X[col])
        encoders[col] = le

    with open("/tmp/log", "a") as f:
        f.write(f"{encoders}")

    return {"status": "trained"}

def quality():
    global model
    global X_test

    proba = model.predict_proba(X_test)[:, 1]
    return {"AUC-ROC": f"{roc_auc_score(y_test, proba)}"}

def influence():
    global model
    global X

    importances = model.feature_importances_
    feat_imp = sorted(zip(X.columns, importances), key=lambda x: x[1], reverse=True)

    res = {}
    for name, score in feat_imp[:15]:
        res[name] = float(score)

    return res



def score(transaction: Dict):
    global model
    global encoders

    import pandas as pd

    # Превращаем транзакцию в DataFrame
    row = pd.DataFrame([transaction])

    # Применяем те же LabelEncoder'ы
    for col, le in encoders.items():
        if col in row:
            row[col] = le.transform([row[col].iloc[0]])

    # Предикт: вероятность fraud
    proba = model.predict_proba(row)[0][1]
    return {"score": float(proba)}

