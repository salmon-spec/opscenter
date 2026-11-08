"""Responses wire compatibility for providers predating namespace/custom tools.

Codex still owns tool execution. Only tool definitions and their wire names change.
"""
import json
import time
import uuid


def has_image(body):
    return any(isinstance(c,dict) and c.get('type')=='input_image'
               for i in body.get('input',[]) for field in ('content','output')
               for c in (i.get(field,[]) if isinstance(i.get(field),list) else []))


def chat_content(content):
    if not isinstance(content,list):return content
    converted=[]
    for c in content:
        if c['type'] in {'input_text','output_text'}:converted.append({'type':'text','text':c.get('text','')})
        elif c['type']=='input_image':converted.append({'type':'image_url','image_url':{'url':c['image_url'],
            'detail':c.get('detail') if c.get('detail') in {'auto','high','low'} else 'auto'}})
    return ''.join(c['text'] for c in converted) if all(c['type']=='text' for c in converted) else converted


def chat_request(body):
    """The existing provider's Responses image converter is incomplete."""
    messages=[];pending_images=[]
    def flush_images():
        # Parallel tool calls must receive all tool replies before another role.
        if pending_images:
            messages.append({'role':'user','content':list(pending_images)})
            pending_images.clear()
    if body.get('instructions'):messages.append({'role':'system','content':body['instructions']})
    for item in body.get('input',[]):
        kind=item.get('type','message')
        if kind=='message':
            flush_images()
            messages.append({'role':item['role'],'content':chat_content(item.get('content',''))})
        elif kind=='function_call':
            flush_images()
            call={'id':item['call_id'],'type':'function','function':{'name':item['name'],'arguments':item['arguments']}}
            if messages and messages[-1]['role']=='assistant':messages[-1].setdefault('tool_calls',[]).append(call)
            else:messages.append({'role':'assistant','content':None,'tool_calls':[call]})
        elif kind in {'function_call_output','custom_tool_call_output'}:
            output=item.get('output','')
            if isinstance(output,list):
                converted=chat_content(output)
                if isinstance(converted,list):
                    # Older Chat gateways accept images on user messages, while
                    # tool messages retain the matching call id and text receipt.
                    pending_images.append({'type':'text','text':'Images returned by tool call '+item['call_id']+':'})
                    pending_images.extend(converted)
                    output=''.join(c['text'] for c in converted if c['type']=='text') or 'Tool returned images attached in the following user message.'
                else:output=converted
            if not isinstance(output,str):output=json.dumps(output)
            messages.append({'role':'tool','tool_call_id':item['call_id'],'content':output})
    flush_images()
    result={'model':body['model'],'messages':messages,'stream':True,
            'stream_options':{'include_usage':True}}
    tools=[{'type':'function','function':{k:v for k,v in tool.items() if k in {'name','description','parameters','strict'}}}
           for tool in body.get('tools',[]) if tool['type']=='function']
    if tools:result.update(tools=tools,tool_choice='auto')
    if body.get('reasoning',{}).get('effort'):result['reasoning_effort']=body['reasoning']['effort']
    return result


async def chat_events(upstream,model):
    """Translate streaming text/function decisions; Codex executes the tools."""
    ident='resp_'+uuid.uuid4().hex
    response={'id':ident,'object':'response','created_at':int(time.time()),'model':model,'status':'in_progress','output':[]}
    yield {'type':'response.created','response':dict(response)}
    text=None;calls={};items=[];usage={};finish=None;phase=None
    async for line in upstream.aiter_lines():
        if not line.startswith('data:'):continue
        raw=line[5:].strip()
        if raw=='[DONE]':break
        try:data=json.loads(raw)
        except ValueError:continue
        if data.get('error'):
            yield {'type':'response.failed','response':{**response,'status':'failed','error':data['error']}}
            return
        if data.get('usage'):usage=data['usage']
        for choice in data.get('choices',[]):
            delta=choice.get('delta',{})
            if choice.get('finish_reason'):finish=choice['finish_reason']
            if delta.get('phase') in {'commentary','partial_answer','final_answer'}:
                phase=delta['phase']
                if text is not None:text['phase']=phase
            if delta.get('content'):
                if text is None:
                    text={'id':'msg_'+uuid.uuid4().hex,'type':'message','role':'assistant','status':'in_progress','content':[]}
                    if phase is not None:text['phase']=phase
                    items.append(text);index=len(items)-1
                    yield {'type':'response.output_item.added','output_index':index,'item':dict(text)}
                    yield {'type':'response.content_part.added','output_index':index,'item_id':text['id'],'content_index':0,'part':{'type':'output_text','text':'','annotations':[]}}
                    text['content']=[{'type':'output_text','text':'','annotations':[],'logprobs':[]}]
                text['content'][0]['text']+=delta['content']
                yield {'type':'response.output_text.delta','item_id':text['id'],'output_index':items.index(text),'content_index':0,'delta':delta['content']}
            for value in delta.get('tool_calls',[]):
                index=value.get('index',0)
                if index not in calls:
                    item={'id':'fc_'+uuid.uuid4().hex,'type':'function_call','call_id':value.get('id') or 'call_'+uuid.uuid4().hex,'name':'','arguments':'','status':'in_progress'}
                    calls[index]=item;items.append(item)
                    function=value.get('function',{});item['name']=function.get('name','')
                    yield {'type':'response.output_item.added','output_index':items.index(item),'item':dict(item)}
                else:
                    item=calls[index]
                    item['name']+=value.get('function',{}).get('name','')
                arguments=value.get('function',{}).get('arguments','')
                if arguments:
                    item['arguments']+=arguments
                    yield {'type':'response.function_call_arguments.delta','output_index':items.index(item),'item_id':item['id'],'delta':arguments}
    for index,item in enumerate(items):
        item['status']='completed'
        if item['type']=='message':
            if item.get('phase') not in {'commentary','partial_answer','final_answer'} and item['content'][0]['text'].strip():
                if calls and finish in {'tool_calls','stop'}:item['phase']='commentary'
                elif not calls and finish=='stop':item['phase']='final_answer'
            yield {'type':'response.output_text.done','output_index':index,'item_id':item['id'],'content_index':0,'text':item['content'][0]['text']}
            yield {'type':'response.content_part.done','output_index':index,'item_id':item['id'],'content_index':0,'part':item['content'][0]}
        else:yield {'type':'response.function_call_arguments.done','output_index':index,'item_id':item['id'],'arguments':item['arguments']}
        yield {'type':'response.output_item.done','output_index':index,'item':item}
    response.update(status='incomplete' if finish=='length' else 'completed',output=items,
        usage={'input_tokens':usage.get('prompt_tokens',0),'output_tokens':usage.get('completion_tokens',0),'total_tokens':usage.get('total_tokens',0)})
    if finish=='length':response['incomplete_details']={'reason':'max_output_tokens'}
    yield {'type':'response.incomplete' if finish=='length' else 'response.completed','response':response}


def flatten(body):
    body = json.loads(json.dumps(body))
    mapping = {}
    tools = []

    def add(tool, namespace=None):
        if tool['type'] == 'namespace':
            for child in tool.get('tools', []):
                add(child, tool['name'])
            return
        tool = dict(tool)
        if tool['type'] in {'function','custom'}:
            name = tool['name']
            alias = (namespace+'__' if namespace else '')+name
            mapping[alias] = {'name':name,'namespace':namespace,'custom':tool['type']=='custom'}
            if tool['type']=='custom':
                description=tool.get('description','')
                if name=='apply_patch':
                    description='Edit workspace files by applying a patch. Call this function with JSON {"input":"*** Begin Patch\\n*** Add File: path.txt\\n+new line\\n*** End Patch"}. Use Add File, Update File or Delete File headers. For updates use @@ context hunks. The input must include actual newline characters. This is an executable function call; printing patch text or XML does not edit files.'
                tool = {'type':'function','name':alias,'description':
                        'Call this function through the tool channel with a JSON object whose input field is the raw tool input string. Do not print XML or simulated tool calls.\n'+description,
                        'parameters':{'type':'object','properties':{'input':{'type':'string'}},
                                      'required':['input'],'additionalProperties':False},'strict':True}
            else:
                tool['name'] = alias
                tool.pop('defer_loading',None)
        tools.append(tool)

    for tool in body.get('tools',[]):
        add(tool)
    body['tools'] = tools
    for item in body.get('input',[]):
        # Older Responses gateways require complete output_text objects on replay.
        for content in item.get('content',[]) if isinstance(item.get('content'),list) else []:
            if content.get('type')=='output_text':
                content.setdefault('annotations',[])
                content.setdefault('logprobs',[])
        content=item.get('content')
        if item.get('role')=='assistant' and isinstance(content,list) and all(c.get('type') in {'output_text','refusal'} for c in content):
            item['content']=''.join(c.get('text',c.get('refusal','')) for c in content)
        namespace = item.pop('namespace',None)
        if item.get('type') in {'function_call','custom_tool_call'}:
            item['name'] = (namespace+'__' if namespace else '')+item['name']
        if item.get('type')=='custom_tool_call':
            item['type']='function_call'
            item['arguments']=json.dumps({'input':item.pop('input','')})
        elif item.get('type')=='custom_tool_call_output':
            item['type']='function_call_output'
    choice=body.get('tool_choice')
    if isinstance(choice,dict) and choice.get('name'):
        namespace=choice.pop('namespace',None)
        choice['name']=(namespace+'__' if namespace else '')+choice['name']
        if choice.get('type')=='custom':choice['type']='function'
    return body,mapping


def restore_item(item,mapping):
    if item.get('type')!='function_call':return item
    original=mapping.get(item.get('name'))
    if not original:return item
    item=dict(item)
    item['name']=original['name']
    if original['namespace']:item['namespace']=original['namespace']
    if original['custom']:
        arguments=item.pop('arguments','')
        try:item['input']=json.loads(arguments).get('input','') if arguments else ''
        except (ValueError,AttributeError):item['input']=arguments
        item['type']='custom_tool_call'
    return item


def restore_response(response,mapping):
    response=dict(response)
    response['output']=[restore_item(i,mapping) for i in response.get('output',[])]
    return response


def restore_event(event,mapping,custom_ids):
    kind=event.get('type','')
    if kind in {'response.output_item.added','response.output_item.done'}:
        event=dict(event)
        event['item']=restore_item(event['item'],mapping)
        if event['item'].get('type')=='custom_tool_call':custom_ids.add(event['item']['id'])
    if kind in {'response.function_call_arguments.delta','response.function_call_arguments.done'} and event.get('item_id') in custom_ids:
        return None
    if 'response' in event:
        event=dict(event);event['response']=restore_response(event['response'],mapping)
    return event
