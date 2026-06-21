import json

from kajet_turbo.embedding.base import EmbedderConfig
from kajet_turbo.embedding.registry import BackendDef, load_registry

_TWO = json.dumps(
    {
        "openai-large": {
            "type": "openai",
            "model": "text-embedding-3-large",
            "dim": 3072,
            "base_url": "https://api.openai.com/v1",
        },
        "mmlw": {
            "type": "hf",
            "model": "sdadas/mmlw-retrieval-roberta-large",
            "dim": 1024,
            "base_url": "https://api-inference.huggingface.co",
            "query_prefix": "zapytanie: ",
        },
    }
)


def test_parses_backends_and_default():
    reg = load_registry({"EMBEDDING_BACKENDS": _TWO, "EMBEDDING_DEFAULT_BACKEND": "mmlw"})
    assert set(reg.backends) == {"openai-large", "mmlw"}
    assert reg.default_id == "mmlw"
    assert reg.backends["mmlw"].query_prefix == "zapytanie: "
    assert reg.backends["openai-large"].dim == 3072


def test_get_none_returns_default():
    reg = load_registry({"EMBEDDING_BACKENDS": _TWO, "EMBEDDING_DEFAULT_BACKEND": "mmlw"})
    assert reg.get(None).backend_id == "mmlw"


def test_get_unknown_falls_back_to_default():
    reg = load_registry({"EMBEDDING_BACKENDS": _TWO, "EMBEDDING_DEFAULT_BACKEND": "mmlw"})
    assert reg.get("does-not-exist").backend_id == "mmlw"


def test_default_falls_back_to_first_backend_when_unset():
    reg = load_registry({"EMBEDDING_BACKENDS": _TWO})
    assert reg.default_id == "openai-large"  # insertion order


def test_empty_env_is_empty_registry():
    reg = load_registry({})
    assert reg.backends == {}
    assert reg.default_id is None
    assert reg.get(None) is None


def test_to_config_binds_key():
    d = BackendDef(
        backend_id="x",
        type="openai",
        model="m",
        dim=8,
        base_url="http://h/v1",
        passage_prefix="passage: ",
    )
    cfg = d.to_config("sk-key")
    assert isinstance(cfg, EmbedderConfig)
    assert cfg.api_key == "sk-key"
    assert cfg.passage_prefix == "passage: "
