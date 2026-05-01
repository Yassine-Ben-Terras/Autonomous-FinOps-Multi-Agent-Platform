"""Anomaly Detection Service."""
from __future__ import annotations
from typing import Any
import numpy as np
import pandas as pd
import structlog
from prophet import Prophet
from sklearn.ensemble import IsolationForest

logger = structlog.get_logger()

class AnomalyDetectionService:
    def detect_prophet(self, df: pd.DataFrame, interval_width: float = 0.95) -> pd.DataFrame:
        model = Prophet(daily_seasonality=False, weekly_seasonality=True, yearly_seasonality=False, interval_width=interval_width)
        model.fit(df)
        forecast = model.predict(df[["ds"]])
        merged = df.merge(forecast[["ds", "yhat", "yhat_lower", "yhat_upper"]], on="ds")
        merged["anomaly"] = (merged["y"] < merged["yhat_lower"]) | (merged["y"] > merged["yhat_upper"])
        merged["anomaly_score"] = np.abs(merged["y"] - merged["yhat"]) / (merged["yhat_upper"] - merged["yhat_lower"])
        return merged

    def detect_isolation_forest(self, df: pd.DataFrame, contamination: float = 0.05) -> pd.DataFrame:
        features = pd.DataFrame({
            "y": df["y"], "y_diff": df["y"].diff().fillna(0),
            "y_roll_mean": df["y"].rolling(7, min_periods=1).mean(),
            "y_roll_std": df["y"].rolling(7, min_periods=1).std().fillna(0),
        })
        clf = IsolationForest(contamination=contamination, random_state=42)
        df = df.copy()
        df["anomaly"] = clf.fit_predict(features) == -1
        df["anomaly_score"] = clf.decision_function(features) * -1
        return df

    def detect_both(self, df: pd.DataFrame) -> pd.DataFrame:
        p_df = self.detect_prophet(df)
        i_df = self.detect_isolation_forest(df)
        df = df.copy()
        df["anomaly_prophet"] = p_df["anomaly"]
        df["anomaly_iforest"] = i_df["anomaly"]
        df["anomaly"] = df["anomaly_prophet"] & df["anomaly_iforest"]
        df["anomaly_score"] = (p_df["anomaly_score"] + i_df["anomaly_score"]) / 2
        return df
