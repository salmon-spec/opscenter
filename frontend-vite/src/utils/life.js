export const CONFIG = {
  subscriptions: { title: '订阅与续费', createLabel: '新建订阅', endpoint: '/subscriptions' },
  'home-inventory': { title: '家庭物品', createLabel: '登记物品 / 位置', endpoint: '/items' },
  'life-reminders': { title: '生活待办', createLabel: '新建待办', endpoint: '/tasks' },
}

export function operationId() {
  const bytes = crypto.getRandomValues(new Uint8Array(16))
  bytes[6] = (bytes[6] & 15) | 64
  bytes[8] = (bytes[8] & 63) | 128
  const text = [...bytes].map(x => x.toString(16).padStart(2, '0')).join('')
  return `${text.slice(0,8)}-${text.slice(8,12)}-${text.slice(12,16)}-${text.slice(16,20)}-${text.slice(20)}`
}

export function formFields(kind, editing) {
  if(kind === 'subscriptions') return [
    {key:'name',label:'订阅名称',required:true},
    {key:'price',label:'每期金额',type:'number',min:0,step:'0.01',required:true},
    {key:'currency_id',label:'币种',type:'select',catalog:'currencies',numeric:true,required:true},
    {key:'frequency',label:'每几个周期',type:'number',min:1,maxNumber:365,required:true},
    {key:'cycle',label:'周期单位',type:'select',numeric:true,required:true,options:[{value:1,label:'天'},{value:2,label:'周'},{value:3,label:'月'},{value:4,label:'年'}]},
    {key:'next_payment',label:'下次付款日期',type:'date',required:true},
    {key:'start_date',label:'起始日期',type:'date'},
    {key:'cancellation_date',label:'取消截止日期',type:'date'},
    {key:'category_id',label:'分类',type:'select',catalog:'categories',numeric:true},
    {key:'payment_method_id',label:'付款方式别名',type:'select',catalog:'payment_methods',numeric:true},
    {key:'url',label:'商家网址',type:'url'},
    {key:'auto_renew',label:'商家自动续订（仅登记）',type:'checkbox'},
    {key:'inactive',label:'已停用',type:'checkbox'},
    {key:'reminder_enabled',label:'启用到期提醒',type:'checkbox'},
    {key:'notes',label:'备注',type:'textarea'},
  ]
  if(kind === 'home-inventory') return [
    {key:'name',label:'名称',required:true},
    {key:'entityTypeId',label:'类型（物品或位置）',type:'select',catalog:'types',required:true},
    {key:'parentId',label:'所在位置',type:'select',catalog:'locations'},
    {key:'tagIds',label:'分类标签（可多选）',type:'multiple',catalog:'tags'},
    {key:'quantity',label:'数量',type:'number',min:0,required:true},{key:'assetId',label:'资产编号（数字，可用短横线分组）',pattern:'[0-9-]*',max:15},{key:'insured',label:'已投保',type:'checkbox'},
    {key:'manufacturer',label:'品牌'}, {key:'modelNumber',label:'型号'}, {key:'serialNumber',label:'序列号'},
    {key:'purchaseDate',label:'购买日期',type:'date'},
    {key:'purchasePrice',label:'购买金额',type:'number',min:0,step:'0.01'},
    {key:'purchaseFrom',label:'购买商家'},
    {key:'soldDate',label:'转售日期',type:'date'},{key:'soldPrice',label:'转售金额',type:'number',min:0,step:'0.01'},{key:'soldTo',label:'转售买家'},{key:'soldNotes',label:'转售备注',type:'textarea'},
    {key:'warrantyExpires',label:'保修到期日期',type:'date'},
    {key:'lifetimeWarranty',label:'终身保修',type:'checkbox'},
    {key:'archived',label:'已归档',type:'checkbox'},
    {key:'reminder_enabled',label:'启用保修提醒',type:'checkbox'},
    {key:'warrantyDetails',label:'保修说明',type:'textarea'},
    {key:'description',label:'分类 / 描述',type:'textarea',max:1000},
    {key:'notes',label:'备注',type:'textarea'},
  ]
  return [
    {key:'title',label:'待办标题',required:true,max:250},
    {key:'project_id',label:'清单',type:'select',catalog:'projects',numeric:true,required:true},
    {key:'priority',label:'优先级',type:'select',numeric:true,required:true,options:[{value:0,label:'普通'},{value:1,label:'低'},{value:2,label:'中'},{value:3,label:'高'},{value:4,label:'紧急'},{value:5,label:'最高'}]},
    {key:'label_ids',label:'标签（可多选）',type:'multiple',catalog:'labels'},
    {key:'due_date',label:'截止时间',type:'datetime-local'},
    {key:'start_date',label:'开始时间',type:'datetime-local'},{key:'end_date',label:'结束时间',type:'datetime-local'},
    {key:'percent_done',label:'完成比例（0–1）',type:'number',min:0,maxNumber:1,step:'0.01',required:true},{key:'is_favorite',label:'收藏',type:'checkbox'},
    {key:'reminder',label:'提醒时间（留空时按截止时间）',type:'datetime-local'},
    {key:'repeat',label:'重复',type:'select',required:true,options:[{value:'none',label:'不重复'},{value:'daily',label:'每天'},{value:'weekly',label:'每周'},{value:'monthly',label:'每月'},{value:'yearly',label:'每年'}]},
    {key:'reminder_enabled',label:'启用提醒',type:'checkbox'},
    ...(!editing ? [{key:'parent_id',label:'父任务编号（可选）',type:'number',min:1}] : []),
    {key:'description',label:'备注',type:'textarea'},
  ]
}

function localDateTime(value) {
  if(!value || value.startsWith('0001-')) return ''
  return new Date(value).toLocaleString('sv-SE',{timeZone:'Asia/Shanghai'}).replace(' ','T').slice(0,16)
}

export function normalizeForm(kind, row, catalog) {
  const today = new Date().toLocaleDateString('sv-SE',{timeZone:'Asia/Shanghai'})
  const result = Object.fromEntries(formFields(kind,Boolean(row)).map(f=>[f.key,f.type==='checkbox'?false:f.type==='multiple'?[]:'']))
  result.reminder_enabled=true
  if(kind === 'subscriptions') Object.assign(result,{price:'0',frequency:1,cycle:3,next_payment:today,auto_renew:true,currency_id:catalog.currencies?.find(x=>x.code==='CNY')?.id || catalog.currencies?.[0]?.id || ''})
  if(kind === 'home-inventory') Object.assign(result,{quantity:1,entityTypeId:catalog.types?.find(x=>!x.isLocation)?.id || '',tagIds:[]})
  if(kind === 'life-reminders') Object.assign(result,{project_id:catalog.projects?.[0]?.id || '',priority:0,percent_done:0,repeat:'none'})
  if(row) for(const key of Object.keys(result)) if(row[key] !== undefined && row[key] !== null) result[key]=row[key]
  if(kind === 'subscriptions') { result.auto_renew=Boolean(Number(result.auto_renew));result.inactive=Boolean(Number(result.inactive));if(row)result.expected_revision=row.workbench_revision || '' }
  if(kind === 'home-inventory' && row) {
    result.entityTypeId=row.entityType?.id || row.entityTypeId || result.entityTypeId
    result.parentId=row.parent?.id || row.parentId || ''
    result.soldDate=String(row.soldDate || '').slice(0,10);if(result.soldDate.startsWith('0001-'))result.soldDate=''
    result.purchaseDate=String(row.purchaseDate || '').slice(0,10)
    result.warrantyExpires=String(row.warrantyExpires || '').slice(0,10)
    result.tagIds=(row.tags || []).map(x=>x.id)
    result.expected_updated=row.updatedAt || ''
  }
  if(kind === 'life-reminders') {
    result.due_date=localDateTime(row?.due_date)
    result.start_date=localDateTime(row?.start_date);result.end_date=localDateTime(row?.end_date)
    result.reminder=localDateTime(row?.reminders?.[0]?.reminder)
    result.repeat=row?.workbench_repeat || 'none'
    result.expected_updated=row?.updated || ''
    result.label_ids=(row?.labels || []).map(x=>x.id)
  }
  return result
}
