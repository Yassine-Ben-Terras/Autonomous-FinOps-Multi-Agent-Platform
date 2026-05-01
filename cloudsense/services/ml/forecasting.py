"""Forecasting Service — Prophet and XGBoost wrapper."""
from __future__ import annotations
from datetime import datetime
from typing import Any
import joblib
import numpy as np
import pandas as pd
import structlog
from prophet import Prophet
from prophet.diagnostics import cross_validation, performance_metrics
from sklearn.metrics import mean_absolute_error, mean_squared_error

logger = structlog.get_logger()

class ForecastingService:
    def __init__(self, model_dir: str = "/tmp/cloudsense/models") -> None:
        self.model_dir = model_dir

    def train_prophet(self, df: pd.DataFrame, changepoint_prior_scale: float = 0.05,
                      seasonality_prior_scale: float = 10.0) -> Prophet:
        if "ds" not in df.columns or "y" not in df.columns:
            raise ValueError("DataFrame must contain 'ds' and 'y' columns")
        model = Prophet(daily_seasonality=False, weekly_seasonality=True, yearly_seasonality=len(df) > 365,
                        changepoint_prior_scale=changepoint_prior_scale, seasonality_prior_scale=seasonality_prior_scale,
                        interval_width=0.95)
        model.fit(df)
        logger.info("prophet_model_trained", rows=len(df))
        return model

    def predict(self, model: Prophet, periods: int = 90, freq: str = "D") -> pd.DataFrame:
        future = model.make_future_dataframe(periods=periods, freq=freq)
        return model.predict(future)

    def evaluate(self, model: Prophet, df: pd.DataFrame, cv_horizon: str = "30 days") -> dict[str, float]:
        df_cv = cross_validation(model, initial="90 days", period="30 days", horizon=cv_horizon)
        df_p = performance_metrics(df_cv)
        return {"mae": float(df_p["mae"].mean()), "rmse": float(df_p["rmse"].mean()),
                "mape": float(df_p["mape"].mean()) * 100, "coverage": float(df_p["coverage"].mean())}

    def save_model(self, model: Prophet, name: str) -> str:
        import os
        os.makedirs(self.model_dir, exist_ok=True)
        path = os.path.join(self.model_dir, f"{name}.pkl")
        joblib.dump(model, path)
        logger.info("model_saved", path=path)
        return path

    def load_model(self, name: str) -> Prophet:
        import os
        path = os.path.join(self.model_dir, f"{name}.pkl")
        return joblib.load(path)

class XGBoostForecaster:
    def __init__(self) -> None:
        self.model = None

    def prepare_features(self, df: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
        df = df.copy()
        df["dayofweek"] = df["ds"].dt.dayofweek
        df["month"] = df["ds"].dt.month
        df["day"] = df["ds"].dt.day
        df["quarter"] = df["ds"].dt.quarter
        df["year"] = df["ds"].dt.year
        df["is_weekend"] = df["dayofweek"].isin([5, 6]).astype(int)
        for lag in [1, 7, 14]:
            df[f"lag_{lag}"] = df["y"].shift(lag)
        df["rolling_mean_7"] = df["y"].shift(1).rolling(7).mean()
        df["rolling_std_7"] = df["y"].shift(1).rolling(7).std()
        df = df.dropna()
        return df.drop(columns=["ds", "y"]), df["y"]

    def train(self, df: pd.DataFrame) -> None:
        from xgboost import XGBRegressor
        X, y = self.prepare_features(df)
        self.model = XGBRegressor(n_estimators=500, max_depth=5, learning_rate=0.05,
                                  subsample=0.8, colsample_bytree=0.8, random_state=42)
        self.model.fit(X, y)
        logger.info("xgboost_model_trained", features=X.shape[1])

    def predict(self, df: pd.DataFrame) -> np.ndarray:
        if self.model is None: raise RuntimeError("Model not trained")
        X, _ = self.prepare_features(df)
        return self.model.predict(X)
