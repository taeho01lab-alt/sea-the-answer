import pytest
from fastapi.testclient import TestClient
from service.role4_standalone import create_app

TOKEN = 'test-only-role4-token-12345678901234567890'

@pytest.fixture
def app():
    application = create_app('sqlite://', TOKEN)
    yield application
    application.state.engine.dispose()

def login(app):
    return TestClient(app, headers={'Authorization': 'Bearer ' + TOKEN})
