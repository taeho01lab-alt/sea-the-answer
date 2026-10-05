"""Role4 tools retain app authorization, CSRF and audit behavior."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from service import role4, role4_calculations
from service.storage import Audit
from service.tests.test_workflows import app, login

QUERY = {'dataset': 'real_annual', 'year_start': 2020, 'year_end': 2025}
CALC = {'scope': {'dataset': 'real_annual', 'vessel_id': 'REAL:IMO:1234567', 'year': 2020}}

@pytest.mark.parametrize('path,body', [
    ('/api/role4/query', QUERY),
    ('/api/role4/calculate', CALC),
    ('/api/role4/ask', {'question': '기록 조회', 'scope': QUERY}),
    ('/api/role4/factors', None),
])
def test_role4_requires_app_admin(app, path, body):
    def request(client, **kwargs):
        return client.get(path, **kwargs) if body is None else client.post(path, json=body, **kwargs)
    assert request(TestClient(app)).status_code == 401
    for name in ('captain', 'crew'):
        assert request(login(app, name)).status_code == 403
    if body is not None:
        assert request(login(app), headers={'X-CSRF-Token': ''}).status_code == 403

def test_admin_tools_share_role4_logic_and_record_audit(app, monkeypatch):
    monkeypatch.setattr(role4, 'query', lambda engine, body: {
        'rows': [], 'total': 0, 'warnings': [], 'dataset': body.dataset})
    monkeypatch.setattr(role4_calculations, 'calculate', lambda engine, body: {
        'version': 'test', 'status': 'blocked', 'record_count': 0})
    client = login(app)
    assert client.get('/api/role4/factors').json()['factors']['HFO'] == '3.114'
    assert client.post('/api/role4/query', json=QUERY).json()['total'] == 0
    assert client.post('/api/role4/calculate', json=CALC).json()['status'] == 'blocked'
    result = client.post('/api/role4/ask', json={'question': '기록 조회', 'scope': QUERY})
    assert result.status_code == 200
    assert result.json()['result']['rows'] == []
    with app.state.sessions() as db:
        actions = {entry.action for entry in db.scalars(select(Audit))}
    assert {'role4.query', 'role4.calculate', 'role4.ask'} <= actions
