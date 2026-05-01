"""MLflow Model Registry Wrapper."""
from __future__ import annotations
from typing import Any
import mlflow
import structlog

logger = structlog.get_logger()

class ModelRegistry:
    def __init__(self, tracking_uri: str, experiment_name: str) -> None:
        mlflow.set_tracking_uri(tracking_uri)
        mlflow.set_experiment(experiment_name)
        self.experiment_name = experiment_name

    def log_forecast_model(self, model: Any, artifact_path: str, metrics: dict[str, float],
                           params: dict[str, Any]) -> str:
        with mlflow.start_run():
            mlflow.log_params(params)
            mlflow.log_metrics(metrics)
            if hasattr(mlflow, "prophet"):
                mlflow.prophet.log_model(model, artifact_path)
            else:
                mlflow.sklearn.log_model(model, artifact_path)
            run_id = mlflow.active_run().info.run_id
            logger.info("model_logged", run_id=run_id, artifact_path=artifact_path)
            return run_id

    def load_forecast_model(self, run_id: str, artifact_path: str = "model") -> Any:
        return mlflow.pyfunc.load_model(f"runs:/{run_id}/{artifact_path}")

    def transition_model_stage(self, model_name: str, version: int, stage: str) -> None:
        client = mlflow.tracking.MlflowClient()
        client.transition_model_version_stage(name=model_name, version=version, stage=stage)
        logger.info("model_stage_transitioned", name=model_name, version=version, stage=stage)
