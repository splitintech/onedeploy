"""Default tests are offline and never inherit provider account credentials."""
import socket
import pytest

@pytest.fixture(autouse=True)
def offline_provider_boundary(monkeypatch, tmp_path):
    for name in ('RAILWAY_TOKEN','RAILWAY_API_TOKEN','DAYTONA_API_KEY',
                 'MODAL_TOKEN_ID','MODAL_TOKEN_SECRET'):
        monkeypatch.delenv(name, raising=False)
    # Real local provider login profiles are not available to test subprocesses.
    home = tmp_path/'test-home'
    home.mkdir(exist_ok=True)
    monkeypatch.setenv('HOME',str(home))
    def no_network(*args, **kwargs):
        raise AssertionError('Offline tests must mock external socket connections')
    monkeypatch.setattr(socket.socket,'connect',no_network)
    monkeypatch.setattr(socket.socket,'connect_ex',no_network)
