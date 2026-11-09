import json
from pathlib import Path

from fastapi import HTTPException
import pytest

from gateway_history import cleanup_public_history,copy_public_history


def record(state,kind,ident,name,data):
    path=state/kind/ident/(name+'.json');path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(data,ensure_ascii=False),encoding='utf8');return path


def test_copy_matches_turns_remaps_images_and_preserves_source(tmp_path):
    old=str(tmp_path/'old'/'dog.png');new=str(tmp_path/'new'/'dog.png')
    image=record(tmp_path,'images','source','image-a',{'id':'image-a','turn_id':'turn-a','path':old,'description':'小狗'})
    record(tmp_path,'images','source','image-b',{'id':'image-b','turn_id':'turn-b','path':old})
    plan=record(tmp_path,'progress','source','turn-a',{'plan':[{'step':'生成图片','status':'completed'}],
        'planExplanation':'已验证','diff':'diff --git a/a b/a','private':'do not copy'})
    record(tmp_path,'progress','source','turn-b',{'plan':[]})
    original={image:image.read_bytes(),plan:plan.read_bytes()}
    copy_public_history(tmp_path,'source','copy',{'turn-a':'new-turn'},{old:new})
    copied=json.loads((tmp_path/'images'/'copy'/'image-a.json').read_text(encoding='utf8'))
    assert copied=={'id':'image-a','turn_id':'new-turn','path':new,'description':'小狗'}
    copied_plan=json.loads((tmp_path/'progress'/'copy'/'new-turn.json').read_text(encoding='utf8'))
    assert set(copied_plan)=={'plan','planExplanation','diff'}
    assert {path.name for path in (tmp_path/'images'/'copy').iterdir()}=={'image-a.json'}
    assert {path.name for path in (tmp_path/'progress'/'copy').iterdir()}=={'new-turn.json'}
    assert all(path.read_bytes()==data for path,data in original.items())


def test_same_project_keeps_image_paths_and_empty_history_is_supported(tmp_path):
    image=str(tmp_path/'workspace'/'dog.png')
    record(tmp_path,'images','source','image',{'turn_id':'turn-a','path':image})
    copy_public_history(tmp_path,'source','copy',{'turn-a':'turn-a'})
    assert json.loads((tmp_path/'images'/'copy'/'image.json').read_text())['path']==image
    copy_public_history(tmp_path,'empty','empty-copy',{})
    assert list((tmp_path/'progress'/'empty-copy').iterdir())==[]


def test_existing_target_is_not_overwritten_or_removed(tmp_path):
    existing=record(tmp_path,'progress','copy','user',{'diff':'keep'})
    with pytest.raises(HTTPException) as error:copy_public_history(tmp_path,'source','copy',{})
    assert error.value.status_code==409 and existing.read_text()=='{"diff": "keep"}'
    assert not (tmp_path/'images'/'copy').exists()


def test_bad_json_removes_only_new_target_directories(tmp_path):
    source=record(tmp_path,'images','source','image',{'turn_id':'turn-a','path':'/workspace/dog.png'})
    bad=tmp_path/'progress'/'source'/'turn-a.json';bad.parent.mkdir(parents=True);bad.write_text('{broken')
    with pytest.raises(HTTPException) as error:copy_public_history(tmp_path,'source','copy',{'turn-a':'turn-new'})
    assert error.value.status_code==502 and source.exists() and bad.read_text()=='{broken'
    assert not (tmp_path/'images'/'copy').exists() and not (tmp_path/'progress'/'copy').exists()


def test_progress_is_bounded_and_only_public_keys_survive(tmp_path):
    record(tmp_path,'progress','source','turn-a',{'plan':[{'step':'中'*2000,'status':'invented'}]*60,
        'planExplanation':'中'*2000,'diff':'中'*100000,'reasoning':'never copy'})
    copy_public_history(tmp_path,'source','copy',{'turn-a':'turn-new'})
    copied=json.loads((tmp_path/'progress'/'copy'/'turn-new.json').read_text(encoding='utf8'))
    assert len(copied['plan'])==50 and copied['plan'][0]['status']=='pending'
    assert len(copied['plan'][0]['step'].encode())<=1000 and len(copied['planExplanation'].encode())<=1000
    assert len(copied['diff'].encode())<=256*1024 and 'reasoning' not in copied


@pytest.mark.parametrize('source,target,mapping',[('../source','copy',{}),('source','source',{}),
    ('source','copy',{'../turn':'new'}),('source','copy',{'a':'same','b':'same'})])
def test_invalid_identifiers_and_turn_maps_do_not_write(tmp_path,source,target,mapping):
    with pytest.raises(HTTPException) as error:copy_public_history(tmp_path,source,target,mapping)
    assert error.value.status_code==422 and list(tmp_path.iterdir())==[]


def test_relative_image_mapping_cannot_be_written(tmp_path):
    with pytest.raises(HTTPException) as error:copy_public_history(tmp_path,'source','copy',{}, {'old.png':'new.png'})
    assert error.value.status_code==422 and list(tmp_path.iterdir())==[]


def test_cleanup_removes_flat_metadata_and_preserves_unknown_user_directories(tmp_path):
    record(tmp_path,'images','copy','image',{'turn_id':'turn-a'})
    record(tmp_path,'progress','copy','turn-a',{'plan':[]})
    assert cleanup_public_history(tmp_path,'copy') is True
    assert not (tmp_path/'images'/'copy').exists() and not (tmp_path/'progress'/'copy').exists()
    user=tmp_path/'images'/'copy'/'user-folder';user.mkdir(parents=True)
    keep=user/'keep.txt';keep.write_text('user data')
    assert cleanup_public_history(tmp_path,'copy') is False and keep.read_text()=='user data'
    assert cleanup_public_history(tmp_path,'../source') is False


def test_symlink_source_metadata_is_rejected_without_touching_target(tmp_path):
    outside=tmp_path/'outside';outside.mkdir();(outside/'secret.json').write_text('{"turn_id":"turn-a"}')
    parent=tmp_path/'images';parent.mkdir()
    try:(parent/'source').symlink_to(outside,target_is_directory=True)
    except OSError:pytest.skip('Symlink creation unavailable on this platform')
    with pytest.raises(HTTPException):copy_public_history(tmp_path,'source','copy',{'turn-a':'new-turn'})
    assert not (parent/'copy').exists() and (outside/'secret.json').exists()
