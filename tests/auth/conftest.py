import pytest

from kajet_turbo.db import Database
from kajet_turbo.repositories.oauth import OAuthRepository


@pytest.fixture
def oauth_repository(database: Database) -> OAuthRepository:
    return OAuthRepository(database.engine)
