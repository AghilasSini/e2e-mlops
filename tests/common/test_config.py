from __future__ import annotations

from pathlib import Path

import pytest

from common.config import ConfigError, load_settings


def test_defaults_load_from_repository(repo_root: Path) -> None:
    settings = load_settings(root=repo_root, env={})

    assert settings.data_path == repo_root / "data" / "raw" / "winequality-red.csv"
    assert settings.schema_path == repo_root / "schema.yaml"
    assert settings.output_dir == repo_root / "outputs"
    assert settings.model_uri == "models:/wine-quality@champion"
    assert settings.params.target.threshold == 6
    assert settings.params.split.test_size == 0.2
    assert settings.params.split.random_state == 42
    assert settings.params.model.alpha == 0.5
    assert settings.params.model.l1_ratio == 0.7
    assert settings.tracking.experiment_name == "wine-quality"
    assert settings.tracking.model_name == "wine-quality"
    assert settings.tracking.champion_alias == "champion"
    assert settings.tracking.previous_alias == "previous-champion"
    promotion = settings.params.promotion
    assert 0 < promotion.min_roc_auc <= 1
    assert 0 < promotion.min_f1 <= 1
    assert promotion.min_improvement >= 0


def test_environment_overrides_paths(app_root: Path, tmp_path: Path) -> None:
    env = {
        "DATA_PATH": "/data/wine.csv",
        "OUTPUT_DIR": "elsewhere",
        "MODEL_URI": "/models/model.joblib",
    }
    settings = load_settings(root=app_root, env=env)

    assert settings.data_path == Path("/data/wine.csv")
    assert settings.output_dir == app_root / "elsewhere"
    assert settings.model_path == app_root / "elsewhere" / "model.joblib"
    assert settings.model_uri == "/models/model.joblib"


def test_app_root_from_environment(app_root: Path) -> None:
    settings = load_settings(env={"APP_ROOT": str(app_root)})
    assert settings.root == app_root.resolve()


def test_model_uri_follows_output_dir(app_root: Path) -> None:
    config = app_root / "config" / "config.yaml"
    config.write_text(
        config.read_text().replace("model_uri: models:/wine-quality@champion", 'model_uri: ""')
    )
    settings = load_settings(root=app_root, env={"OUTPUT_DIR": "/srv/out"})
    assert settings.model_uri == "/srv/out/model.joblib"
    assert settings.metrics_path == Path("/srv/out/metrics.json")
    assert settings.version_path == Path("/srv/out/model_version")


def test_missing_config_file_is_reported(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_settings(root=tmp_path, env={})


def test_missing_params_key_is_reported(app_root: Path) -> None:
    (app_root / "params.yaml").write_text(
        "target: {name: x}\nsplit: {}\nmodel: {}\npromotion: {}\n"
    )
    with pytest.raises(ConfigError, match="missing key"):
        load_settings(root=app_root, env={})


def test_non_mapping_yaml_is_rejected(app_root: Path) -> None:
    (app_root / "params.yaml").write_text("- just\n- a list\n")
    with pytest.raises(ConfigError, match="mapping"):
        load_settings(root=app_root, env={})


def test_missing_promotion_block_is_reported(app_root: Path) -> None:
    params = app_root / "params.yaml"
    params.write_text(params.read_text().split("promotion:")[0])
    with pytest.raises(ConfigError, match="promotion"):
        load_settings(root=app_root, env={})


def test_missing_registry_model_name_is_reported(app_root: Path) -> None:
    config = app_root / "config" / "config.yaml"
    config.write_text(config.read_text().replace("model_name: wine-quality", "name: x"))
    with pytest.raises(ConfigError, match="model_name"):
        load_settings(root=app_root, env={})
