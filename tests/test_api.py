from fastapi.testclient import TestClient

from neobots.app import create_app
from neobots.config import Config


def test_authentication_csrf_and_no_secret_exposure(tmp_path):
    config=Config(data_dir=tmp_path,password="test-password",api_key="NEVER-RETURN-THIS",model="test-model",model_url="https://example.invalid/v1")
    with TestClient(create_app(config,start_worker=False)) as client:
        assert client.get('/api/state').status_code==401
        assert client.post('/api/login',json={'password':'wrong'}).status_code==401
        assert client.post('/api/login',json={'password':'test-password'}).status_code==200
        response=client.get('/api/state')
        assert response.status_code==200
        assert 'NEVER-RETURN-THIS' not in response.text
        assert response.headers['x-frame-options']=='DENY'
        assert client.post('/api/bots',headers={'Origin':'https://evil.invalid'},json={'name':'Bad'}).status_code==403
        assert client.post('/api/bots',json={'name':'Good','role':'Research'}).status_code==200


def test_approval_decision_cannot_be_submitted_twice(tmp_path):
    app=create_app(Config(data_dir=tmp_path,password="pw"),start_worker=False)
    with TestClient(app) as client:
        client.post('/api/login',json={'password':'pw'})
        store=app.state.store
        bot=store.all('SELECT * FROM bots')[0]
        task=store.create_task(bot['id'],'Run command')
        store.status(task['id'],'awaiting_approval')
        store.execute("INSERT INTO approvals VALUES('approval',?,'call','run_command','{}','pending',1)",(task['id'],))
        assert client.post('/api/approvals/approval/approved').status_code==200
        assert client.post('/api/approvals/approval/approved').status_code==409
        assert store.task(task['id'])['status']=='queued'


def test_missing_model_configuration_does_not_enqueue_fake_work(tmp_path):
    app=create_app(Config(data_dir=tmp_path,password="pw",model_url="",model="",api_key=""),start_worker=False)
    with TestClient(app) as client:
        client.post('/api/login',json={'password':'pw'})
        bot=client.get('/api/state').json()['bots'][0]
        result=client.post('/api/bots/'+bot['id']+'/tasks',json={'prompt':'Hello'})
        assert result.status_code==409
        assert not app.state.store.all('SELECT * FROM tasks')

