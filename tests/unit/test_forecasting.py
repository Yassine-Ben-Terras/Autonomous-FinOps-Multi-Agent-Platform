"""Tests for Phase 3 forecasting."""
import pytest
import pandas as pd
from cloudsense.services.ml.forecasting import ForecastingService, XGBoostForecaster

def test_prophet_training():
    service = ForecastingService()
    df = pd.DataFrame({
        "ds": pd.date_range("2024-01-01", periods=30, freq="D"),
        "y": [100 + i * 2 for i in range(30)]
    })
    model = service.train_prophet(df)
    assert model is not None
    forecast = service.predict(model, periods=7)
    assert len(forecast) == 37  # 30 historical + 7 future

def test_xgboost_forecaster():
    forecaster = XGBoostForecaster()
    df = pd.DataFrame({
        "ds": pd.date_range("2024-01-01", periods=60, freq="D"),
        "y": [100 + i * 2 for i in range(60)]
    })
    forecaster.train(df)
    preds = forecaster.predict(df)
    assert len(preds) == 60 - 14  # dropped NaNs from lag features
