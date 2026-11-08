from rpc import AppServer


def test_public_plan_and_diff_restore_after_server_restart(tmp_path,monkeypatch):
    monkeypatch.setenv('CODEX_GATEWAY_DATA',str(tmp_path))
    first=AppServer()
    first.emit('turn/plan/updated',{'threadId':'thread-a','turnId':'turn-a','explanation':'先验证结果',
        'plan':[{'step':'生成图片','status':'inProgress'},{'step':'验证显示','status':'pending'}]})
    first.emit('turn/diff/updated',{'threadId':'thread-a','turnId':'turn-a','diff':'diff --git a/a.txt b/a.txt'})
    second=AppServer()
    data=second.load_public('thread-a','turn-a')
    assert data['plan'][0]['step']=='生成图片' and data['planExplanation']=='先验证结果'
    assert data['diff'].startswith('diff --git')
    assert second.load_public('thread-b','turn-a')=={}
    first.emit('turn/plan/updated',{'threadId':'../escape','turnId':'turn-a','plan':[]})
    assert not (tmp_path.parent/'escape').exists()
