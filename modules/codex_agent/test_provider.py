from provider import flatten,restore_event,restore_response
from provider import has_image,chat_request,chat_events
import asyncio
import json
import pytest


def test_namespaced_function_round_trip():
    body,mapping=flatten({'tools':[{'type':'namespace','name':'functions','tools':[
        {'type':'function','name':'exec_command','parameters':{'type':'object'}}]}],
        'input':[{'type':'function_call','name':'exec_command','namespace':'functions','arguments':'{"cmd":"pwd"}'}]})
    assert body['tools'][0]['type']=='function'
    assert body['tools'][0]['name']=='functions__exec_command'
    assert body['input'][0]['name']=='functions__exec_command' and 'namespace' not in body['input'][0]
    event=restore_event({'type':'response.output_item.done','item':{'type':'function_call','name':'functions__exec_command','arguments':'{"cmd":"pwd"}'}},mapping,set())
    assert event['item']['name']=='exec_command' and event['item']['namespace']=='functions'


def test_freeform_patch_round_trip_and_history():
    patch='*** Begin Patch\n*** End Patch'
    body,mapping=flatten({'tools':[{'type':'namespace','name':'functions','tools':[{'type':'custom','name':'apply_patch'}]}],
        'input':[{'type':'custom_tool_call','name':'apply_patch','namespace':'functions','input':patch},
                 {'type':'custom_tool_call_output','call_id':'c1','output':'done'}]})
    assert body['tools'][0]['parameters']['required']==['input']
    assert body['input'][0]['type']=='function_call'
    assert body['input'][1]['type']=='function_call_output'
    calls=set()
    event=restore_event({'type':'response.output_item.added','item':{'id':'i1','type':'function_call','name':'functions__apply_patch','arguments':''}},mapping,calls)
    assert event['item']['type']=='custom_tool_call'
    assert restore_event({'type':'response.function_call_arguments.delta','item_id':'i1','delta':'partial'},mapping,calls) is None
    result=restore_response({'output':[{'type':'function_call','name':'functions__apply_patch','arguments':body['input'][0]['arguments']}]},mapping)
    assert result['output'][0]['type']=='custom_tool_call' and result['output'][0]['input']==patch


def test_assistant_text_replay_has_gateway_required_fields():
    body,_=flatten({'input':[{'type':'message','role':'assistant','content':[{'type':'output_text','text':'done'}]}]})
    assert body['input'][0]['content']=='done'


def test_image_chat_bridge_preserves_tool_results_and_visual_input():
    body={'model':'test','input':[{'role':'user','content':[{'type':'input_text','text':'red?'},{'type':'input_image','image_url':'data:image/png;base64,abc'}]},
        {'type':'function_call','name':'functions__exec_command','call_id':'c1','arguments':'{}'},
        {'type':'function_call_output','call_id':'c1','output':'OK'}],
        'tools':[{'type':'function','name':'functions__exec_command','parameters':{'type':'object'}}]}
    assert has_image(body)
    chat=chat_request(body)
    assert chat['messages'][0]['content'][1]['image_url']['url']=='data:image/png;base64,abc'
    assert chat['messages'][1]['tool_calls'][0]['id']=='c1'
    assert chat['messages'][2]=={'role':'tool','tool_call_id':'c1','content':'OK'}
    assert chat['tools'][0]['function']['name']=='functions__exec_command'


@pytest.mark.parametrize('arguments',['{','',{'cmd':'historical'},None])
def test_chat_history_wraps_rejected_arguments_without_repair_or_execution(arguments):
    original={'model':'test','input':[
        {'type':'function_call','name':'','call_id':'rejected','arguments':arguments},
        {'type':'function_call_output','call_id':'rejected','output':'Native argument parsing failed.'}],
        'tools':[{'type':'function','name':'exec_command','parameters':{'type':'object'}}]}
    before=json.loads(json.dumps(original))
    result=chat_request(original)
    call=result['messages'][0]['tool_calls'][0]
    assert call['id']=='rejected' and call['function']['name']=='historical_invalid_tool_call'
    assert json.loads(call['function']['arguments'])=={
        '_codex_history_parse_error':True,'original_name':'','original_raw':arguments}
    reply=result['messages'][1]
    assert reply['role']=='tool' and reply['tool_call_id']=='rejected'
    assert reply['content'].startswith('Historical tool call parsing failed;')
    assert reply['content'].endswith('Native argument parsing failed.')
    assert [tool['function']['name'] for tool in result['tools']]==['exec_command']
    assert original==before


@pytest.mark.parametrize('arguments',[' { "cmd" : "pwd" } \n','{"数字":1,"escaped":"\\u4e2d"}',
                                     '[]','null','57','"quoted"'])
def test_valid_history_json_is_preserved_byte_for_byte(arguments):
    result=chat_request({'model':'test','input':[
        {'type':'function_call','name':'exec_command','call_id':'valid','arguments':arguments}]})
    call=result['messages'][0]['tool_calls'][0]['function']
    assert call=={'name':'exec_command','arguments':arguments}


def test_mixed_valid_and_rejected_history_marks_only_matching_tool_reply():
    valid=' { "cmd": "historical only" } '
    body={'model':'test','input':[
        {'type':'function_call','name':'exec_command','call_id':'valid','arguments':valid},
        {'type':'function_call','name':'','call_id':'bad','arguments':'{'},
        {'type':'function_call_output','call_id':'valid','output':'Original successful result.'},
        {'type':'function_call_output','call_id':'bad','output':'Original native parsing error.'}]}
    original=json.loads(json.dumps(body))
    result=chat_request(body)
    calls=result['messages'][0]['tool_calls']
    assert calls[0]['function']=={'name':'exec_command','arguments':valid}
    assert calls[1]['function']['name']=='historical_invalid_tool_call'
    assert json.loads(calls[1]['function']['arguments'])['original_raw']=='{'
    replies={message['tool_call_id']:message['content'] for message in result['messages'] if message['role']=='tool'}
    assert replies['valid']=='Original successful result.'
    assert replies['bad'].startswith('Historical tool call parsing failed;')
    assert replies['bad'].endswith('Original native parsing error.')
    assert body==original


def test_new_malformed_model_arguments_remain_unmodified_for_native_rejection():
    class Stream:
        async def aiter_lines(self):
            yield 'data: '+json.dumps({'choices':[{'delta':{'tool_calls':[
                {'index':0,'id':'new-call','function':{'name':'exec_command','arguments':'{'}}]},'finish_reason':'tool_calls'}]})
            yield 'data: [DONE]'
    async def collect():return [event async for event in chat_events(Stream(),'test')]
    events=asyncio.run(collect())
    call=events[-1]['response']['output'][0]
    assert call['name']=='exec_command' and call['arguments']=='{'
    with pytest.raises(ValueError):json.loads(call['arguments'])


def test_native_image_tool_output_uses_visual_chat_bridge():
    image='data:image/png;base64,aW1hZ2U='
    for kind in ['function_call_output','custom_tool_call_output']:
        native={'model':'test','input':[
            {'type':'function_call','name':'view_image','call_id':'c1','arguments':'{}'},
            {'type':kind,'call_id':'c1','output':[
                {'type':'input_text','text':'before image'},
                {'type':'input_image','image_url':image,'detail':'high'},
                {'type':'input_text','text':'after image'}]}]}
        assert has_image(native)
        body,_=flatten(native)
        chat=chat_request(body)
        assert [m['role'] for m in chat['messages']]==['assistant','tool','user']
        receipt=chat['messages'][1]
        assert receipt['tool_call_id']=='c1' and receipt['content']=='before imageafter image'
        assert image not in receipt['content']
        assert chat['messages'][2]['content'][1:]==[
            {'type':'text','text':'before image'},
            {'type':'image_url','image_url':{'url':image,'detail':'high'}},
            {'type':'text','text':'after image'}]
        assert isinstance(native['input'][1]['output'],list)


def test_parallel_image_tools_keep_all_tool_replies_before_visual_messages():
    images=['data:image/png;base64,Zmlyc3Q=','data:image/png;base64,c2Vjb25k']
    body={'model':'test','input':[
        *[{'type':'function_call','name':'view_image','call_id':call,'arguments':'{}'} for call in ['c1','c2']],
        *[{'type':'function_call_output','call_id':call,'output':[
            {'type':'input_image','image_url':image,'detail':'original'}]} for call,image in zip(['c1','c2'],images)],
        {'type':'message','role':'user','content':[{'type':'input_text','text':'continue'}]}]}
    chat=chat_request(body)
    assert [m['role'] for m in chat['messages']]==['assistant','tool','tool','user','user']
    assert [c['id'] for c in chat['messages'][0]['tool_calls']]==['c1','c2']
    assert [m['tool_call_id'] for m in chat['messages'][1:3]]==['c1','c2']
    assert all(isinstance(m['content'],str) and not any(image in m['content'] for image in images)
               for m in chat['messages'][1:3])
    visual=[c['image_url'] for c in chat['messages'][3]['content'] if c['type']=='image_url']
    assert visual==[{'url':image,'detail':'auto'} for image in images]
    assert chat['messages'][4]['content']=='continue'


def test_text_tool_content_remains_text_without_extra_visual_message():
    body={'model':'test','input':[
        {'type':'function_call','name':'read_file','call_id':'c1','arguments':'{}'},
        {'type':'function_call_output','call_id':'c1','output':[
            {'type':'input_text','text':'first\n'},{'type':'input_text','text':'second'}]}]}
    assert not has_image(body)
    chat=chat_request(body)
    assert len(chat['messages'])==2
    assert chat['messages'][1]=={'role':'tool','tool_call_id':'c1','content':'first\nsecond'}


def test_chat_stream_produces_executable_responses_call_and_final_text():
    class Stream:
        async def aiter_lines(self):
            chunks=[{'choices':[{'delta':{'tool_calls':[{'index':0,'id':'c1','function':{'name':'functions__exec_command','arguments':'{"cmd":'}}]}}]},
                    {'choices':[{'delta':{'tool_calls':[{'index':0,'function':{'arguments':'"pwd"}'}}]},'finish_reason':'tool_calls'}]},
                    {'choices':[],'usage':{'prompt_tokens':10,'completion_tokens':2,'total_tokens':12}}]
            for c in chunks:yield 'data: '+json.dumps(c)
            yield 'data: [DONE]'
    async def collect():return [e async for e in chat_events(Stream(),'test')]
    events=asyncio.run(collect())
    item=next(e['item'] for e in events if e['type']=='response.output_item.done')
    assert item['call_id']=='c1' and json.loads(item['arguments'])=={'cmd':'pwd'}
    assert events[-1]['response']['status']=='completed' and events[-1]['response']['usage']['total_tokens']==12


@pytest.mark.parametrize('content,calls,finish,phase,expected',[
    ('正在检查文件。',True,'tool_calls',None,'commentary'),
    ('正在检查文件。',True,'stop',None,'commentary'),
    ('检查已经完成。',False,'stop',None,'final_answer'),
    (' \n ',True,'tool_calls',None,None),
    (' \n ',False,'stop',None,None),
    ('答复被截断',False,'length',None,None),
    ('工具被截断',True,'length',None,None),
    ('结束原因未知',False,None,None,None),
    ('工具结束原因未知',True,None,None,None),
    ('已经确认的部分结果',True,'tool_calls','partial_answer','partial_answer'),
    ('公开进展',False,'stop','commentary','commentary'),
    ('完成结果',False,'stop','invalid','final_answer'),
])
def test_chat_stream_labels_only_real_public_text(content,calls,finish,phase,expected):
    class Stream:
        async def aiter_lines(self):
            delta={'content':content}
            if phase is not None:delta['phase']=phase
            yield 'data: '+json.dumps({'choices':[{'delta':delta}]})
            if calls:
                yield 'data: '+json.dumps({'choices':[{'delta':{'tool_calls':[
                    {'index':0,'id':'c1','function':{'name':'read_file','arguments':'{}'}}]}}]})
            yield 'data: '+json.dumps({'choices':[{'delta':{},'finish_reason':finish}]})
            yield 'data: [DONE]'
    async def collect():return [e async for e in chat_events(Stream(),'test')]
    events=asyncio.run(collect())
    item=next(e['item'] for e in events if e['type']=='response.output_item.done' and e['item']['type']=='message')
    assert item.get('phase')==expected
    assert item['content'][0]['text']==content
    assert next(i for i in events[-1]['response']['output'] if i['type']=='message')==item
    added=next(e['item'] for e in events if e['type']=='response.output_item.added' and e['item']['type']=='message')
    assert added.get('phase')==(phase if phase in {'commentary','partial_answer','final_answer'} else None)


def test_chat_stream_preserves_late_valid_phase():
    class Stream:
        async def aiter_lines(self):
            for delta,finish in [({'content':'确认的结果'},None),({'phase':'partial_answer'},'stop')]:
                yield 'data: '+json.dumps({'choices':[{'delta':delta,'finish_reason':finish}]})
            yield 'data: [DONE]'
    async def collect():return [e async for e in chat_events(Stream(),'test')]
    events=asyncio.run(collect())
    assert events[-1]['response']['output'][0]['phase']=='partial_answer'


def test_chat_stream_early_eof_fails_without_completing_partial_tools():
    class Stream:
        async def aiter_lines(self):
            yield 'data: '+json.dumps({'choices':[{'delta':{'content':'正在检查',
                'tool_calls':[{'index':0,'id':'c1','function':{'name':'functions__exec_command','arguments':'{"cmd":'}}]}}]})
    async def collect():return [event async for event in chat_events(Stream(),'test')]
    events=asyncio.run(collect())
    assert events[-1]['type']=='response.failed' and events[-1]['response']['status']=='failed'
    assert events[-1]['response']['error']['code']=='server_error'
    assert not any(event['type'] in {'response.completed','response.output_item.done','response.function_call_arguments.done'} for event in events)
