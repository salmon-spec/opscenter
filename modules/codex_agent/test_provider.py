from provider import flatten,restore_event,restore_response
from provider import has_image,chat_request,chat_events
import asyncio
import json


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
