from kajet_turbo.embedding.base import Embedder, EmbedderConfig


def test_embedder_config_defaults():
    cfg = EmbedderConfig(
        backend_id="openai-large",
        type="openai",
        model="text-embedding-3-large",
        dim=3072,
        base_url="https://api.openai.com/v1",
    )
    assert cfg.query_prefix == ""
    assert cfg.passage_prefix == ""
    assert cfg.api_key is None


def test_conforming_class_satisfies_protocol():
    class Fake:
        name = "fake"
        dim = 3
        query_prefix = ""
        passage_prefix = ""

        async def embed_documents(self, texts):
            return [[0.0, 0.0, 0.0] for _ in texts]

        async def embed_query(self, text):
            return [0.0, 0.0, 0.0]

    assert isinstance(Fake(), Embedder)


def test_missing_method_fails_protocol():
    class NotAnEmbedder:
        name = "x"

    assert not isinstance(NotAnEmbedder(), Embedder)
