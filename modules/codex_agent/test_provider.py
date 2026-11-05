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
