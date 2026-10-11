const $ = (selector) => document.querySelector(selector);
const esc = (value = '') => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const statuses = {queued:'排队中',running:'正在工作',awaiting_approval:'等待审批',waiting_bot:'等待协作',completed:'已完成',failed:'未完成',cancelled:'已取消',interrupted:'已中断'};
const eventNames = {queued:'任务入队',routine:'定时触发',message:'工作进展',tool_start:'调用工具',tool_result:'工具结果',approval:'请求审批',decision:'审批结果',delegation:'移交工作',completed:'完成',error:'错误',interrupted:'任务中断',resumed:'继续执行',cancelled:'取消'};
const terminal = ['completed','failed','cancelled'];
let state = {bots:[],tasks:[],approvals:[],skills:[],routines:[],model:{}};
let selected = localStorage.getItem('neobots.bot');
let view = 'chat', messages = [], signature = '', loading = false, loggedIn = false, panel = false;
let filePath = '.', filesData = null, fileError = '', toastTimer;

async function api(path, options = {}) {
  const response = await fetch('/api' + path, {
    ...options, headers:{'Content-Type':'application/json',...options.headers},
    body: options.body === undefined ? undefined : JSON.stringify(options.body)
  });
  const data = await response.json().catch(() => ({}));
  if (response.status === 401 && path !== '/login') { showLogin(); throw new Error('请先登录'); }
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '请求失败，请检查输入后重试');
  return data;
}
function post(path, body) { return api(path, {method:'POST',body}); }
function toast(message) { clearTimeout(toastTimer); $('#toast').textContent=message; $('#toast').classList.remove('hidden'); toastTimer=setTimeout(()=>$('#toast').classList.add('hidden'),5500); }
function date(value) { return new Date(value*1000).toLocaleString('zh-CN',{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}); }
function botById(id) { return state.bots.find(b=>b.id===id); }
function avatar(bot, cls='') { const i = Math.max(0,state.bots.findIndex(b=>b.id===bot?.id));return `<span class="avatar color-${i%4} ${cls}">${esc(bot?.name?.slice(0,1)||'N')}</span>`; }
function badge(status) { return `<span class="badge ${esc(status)}">${esc(statuses[status]||status)}</span>`; }
function showLogin() { loggedIn=false; $('#app').classList.add('hidden'); $('#login').classList.remove('hidden'); }
function closeDialog() { $('#dialog').close(); }
function openDialog(title, html) { $('#dialog').innerHTML=`<div class="dialog-header"><h2>${esc(title)}</h2><button class="icon-button" data-action="close-dialog" aria-label="关闭">×</button></div>${html}`;if(!$('#dialog').open) $('#dialog').showModal(); }
const dialogFooter = `<div class="dialog-footer"><button type="button" class="secondary" data-action="close-dialog">取消</button><button class="primary" type="submit">保存</button></div>`;

async function refresh(force=false) {
  if (loading) return;
  loading=true;
  try {
    state=await api('/state');
    loggedIn=true; $('#login').classList.add('hidden'); $('#app').classList.remove('hidden');
    if (!botById(selected)) selected=state.bots[0]?.id;
    if(selected) localStorage.setItem('neobots.bot',selected);
    if(view==='chat' && selected) messages=await api(`/bots/${selected}/messages`);
    $('#configuration-banner').classList.toggle('hidden',!!state.model.configured);
    $('#model-name').textContent=state.model.name || '尚未配置模型';
    renderNavigation();
    const next=JSON.stringify([view,selected,state,messages,filePath,filesData,fileError]);
    if(force || next!==signature) { signature=next; renderContent(); }
  } catch(error) { if(loggedIn) toast(error.message); }
  finally { loading=false; }
}

function renderNavigation() {
  const tabs=[['tasks','▤','任务记录'],['files','▧','共享文件'],['routines','◷','定时任务'],['skills','⌘','技能库'],['approvals','◇','待审批']];
  $('#navigation').innerHTML=tabs.map(([key,icon,label])=>`<button data-view="${key}" class="${view===key?'active':''}"><span class="nav-icon">${icon}</span>${label}${key==='approvals'&&state.approvals.length?`<span class="count">${state.approvals.length}</span>`:''}</button>`).join('');
  $('#bot-count').textContent=state.bots.length;
  $('#bot-list').innerHTML=state.bots.map(bot=>{
    const active=state.tasks.find(t=>t.bot_id===bot.id&&!terminal.includes(t.status));
    const dot=active?.status==='running'?'busy':active?'waiting':'online';
    return `<button class="bot-item ${view==='chat'&&selected===bot.id?'selected':''}" data-bot="${bot.id}">${avatar(bot)}<span class="bot-label"><strong>${esc(bot.name)}</strong><small>${active?statuses[active.status]:esc(bot.role.slice(0,28)||'准备好接收任务')}</small></span><span class="status-dot ${dot}"></span></button>`;
  }).join('');
}

function taskCard(task) {
  if(!task) return '';
  return `<div class="task-card"><div class="task-bar">${badge(task.status)}<span>${date(task.created)}</span><button class="quiet" data-task="${task.id}">查看过程 ↗</button></div>${task.status==='failed'?`<div class="task-error">${esc(task.result)}</div>`:''}${!terminal.includes(task.status)?`<div class="card-actions">${task.status==='awaiting_approval'?'<button class="secondary compact" data-view="approvals">查看待审批操作</button>':''}${task.status==='interrupted'?`<button class="secondary compact" data-resume="${task.id}">继续任务</button>`:''}<button class="quiet" data-cancel="${task.id}">停止任务</button></div>`:''}</div>`;
}

function page(title, subtitle, action, body) {
  return `<section class="page"><div class="page-heading"><div><p class="eyebrow">YOUR WORKSPACE</p><h2>${title}</h2><p>${subtitle}</p></div>${action}</div>${body}</section>`;
}
function empty(text) { return `<div class="empty">${text}</div>`; }

function renderContent() {
  const bot=botById(selected);
  $('#composer-area').classList.toggle('hidden',view!=='chat');
  $('#prompt').placeholder=`告诉 ${bot?.name||'Bot'} 你想完成什么…`;
  const titles={tasks:'任务记录',files:'共享文件',routines:'定时任务',skills:'技能库',approvals:'待审批'};
  $('#page-title').innerHTML=view==='chat'?`<h1>${esc(bot?.name||'工作室')} <span class="status-dot online"></span><button class="quiet" data-action="edit-bot" title="编辑 Bot">⌄</button></h1><small>你的个人 AI 同事</small>`:`<h1>${titles[view]}</h1><small>NeoBots / 个人工作室</small>`;
  const content=$('#content'), nearBottom=content.scrollHeight-content.scrollTop-content.clientHeight<120;
  let html='';
  if(view==='chat') {
    if(!messages.length) {
      const starters=[['⌕','研究一个话题','查阅来源，整理成有依据的报告','请研究一个我感兴趣的话题：先问我具体主题，再查阅公开网页，给出有来源链接的结论，并保存到 report.md。'],['▧','整理工作文件','浏览共享目录，归纳信息与待办','请查看共享工作目录，整理文件概览和建议的下一步；先不要修改现有文件。'],['◷','建立日常工作流','将重复的工作交给你的 Bot','帮我设计一个每天执行的信息整理任务。先问清楚信息来源和交付格式，再把流程保存成技能。'],['◈','组建你的 Bot 团队','让各有所长的伙伴一起完成工作','列出当前的 Bot 和它们的职责，帮我设计适合个人使用的分工，并说明可以如何协作。']];
      html=`<section class="welcome"><span class="spark">✳</span><p class="eyebrow">A LITTLE TEAM. A LOT MORE POSSIBLE.</p><h2>把下一件事，<br>交给 <span>${esc(bot?.name||'Neo')}</span>。</h2><p class="muted">给一个目标，让你的 Bot 从查找资料到实际执行，<br>带着记忆和工具，把工作向前推进。</p><div class="starter-grid">${starters.map(([icon,title,sub,prompt])=>`<button class="starter" data-prompt="${esc(prompt)}"><span class="starter-icon">${icon}</span><strong>${title}<span>↗</span></strong><small>${sub}</small></button>`).join('')}</div><div class="welcome-bottom"><span>◈ 独立执行环境</span><span>◇ 持久化记忆</span><span>◷ 后台任务</span></div></section>`;
    } else {
      html=`<div class="messages">${messages.map(m=>{
        const task=state.tasks.find(t=>t.id===m.task_id);
        return `<article class="message ${m.role}">${m.role==='assistant'?avatar(bot):''}<div class="message-body">${m.role==='assistant'?`<span class="message-name">${esc(bot?.name)}</span>`:''}${esc(m.content)}</div></article>${m.role==='user'?taskCard(task):''}`;
      }).join('')}</div>`;
    }
  } else if(view==='tasks') {
    html=page('每一步，都有记录。','查看后台任务、工具调用和 Bot 间的移交。','',state.tasks.length?`<div class="cards">${state.tasks.map(t=>`<article class="card"><h3>${esc(botById(t.bot_id)?.name)} ${badge(t.status)}</h3><p>${esc(t.prompt.slice(0,220))}</p><div class="card-footer"><span>${date(t.created)}${t.parent_id?' · 协作任务':''}</span><button class="secondary compact" data-task="${t.id}">查看过程 ↗</button></div></article>`).join('')}</div>`:empty('还没有任务。给你的第一个 Bot 发一条消息吧。'));
  } else if(view==='approvals') {
    html=page('需要你的决定。','审批只授权卡片中的这一次操作。批准或拒绝后，Bot 会继续处理。','',state.approvals.length?`<div class="cards">${state.approvals.map(a=>`<article class="card"><h3>◇ ${esc(a.tool)} <span class="badge awaiting_approval">等待决定</span></h3><p>${esc(botById(state.tasks.find(t=>t.id===a.task_id)?.bot_id)?.name)} · ${date(a.created)}</p><pre>${esc(JSON.stringify(JSON.parse(a.arguments),null,2))}</pre><div class="card-footer"><button class="quiet" data-task="${a.task_id}">查看任务上下文 ↗</button><div class="card-actions"><button class="danger" data-approval="${a.id}" data-decision="denied">拒绝</button><button class="primary" data-approval="${a.id}" data-decision="approved">批准这次操作</button></div></div></article>`).join('')}</div>`:empty('目前没有待审批操作。'));
  } else if(view==='routines') {
    html=page('让好流程，自动重复。','按固定间隔派发任务；离线期间错过的执行不会集中补跑。','<button class="primary" data-action="new-routine">＋ 新建任务</button>',state.routines.length?`<div class="cards">${state.routines.map(r=>`<article class="card"><h3>${esc(r.name)} <span class="badge">${r.enabled?'已启用':'已暂停'}</span></h3><p>${esc(r.prompt)}</p><div class="card-footer"><span>${esc(botById(r.bot_id)?.name)} · 每 ${r.interval_minutes} 分钟<br>${r.enabled?'下次执行：'+date(r.next_run):'暂停期间不派发新任务'}</span><div class="card-actions">${r.last_task?`<button class="quiet" data-task="${r.last_task}">最近执行</button>`:''}<button class="secondary compact" data-routine="${r.id}">${r.enabled?'暂停':'启用'}</button></div></div></article>`).join('')}</div>`:empty('先完成一次真实任务，再把稳定的流程设为定时任务。'));
  } else if(view==='skills') {
    html=page('把经验，变成技能。','所有 Bot 共享的工作方法。你可以直接告诉 Bot 技能名称，让它读取并执行。','<button class="primary" data-action="new-skill">＋ 新建技能</button>',state.skills.length?`<div class="cards">${state.skills.map(s=>`<article class="card"><h3>⌘ ${esc(s.name)}</h3><p>${esc(s.instructions.slice(0,220))}${s.instructions.length>220?'…':''}</p><div class="card-footer"><span>共享技能 · ${date(s.created)}</span><button class="secondary compact" data-skill="${s.id}">查看 / 编辑</button></div></article>`).join('')}</div>`:empty('还没有保存的技能。完成一个任务后，可以让 Bot 将方法保存下来。'));
  } else if(view==='files') {
    html=page('成果，有处可寻。','Bots 在同一个工作目录协作，这里的文件会随容器重启保留。','<button class="secondary compact" data-action="refresh-files">↻ 刷新</button>',`<p class="help">/workspace/${esc(filePath==='.'?'':filePath)}</p>${fileError?empty(esc(fileError)):!filesData?empty('正在连接工作目录…'):`<div class="file-table">${filePath!=='.'?'<button class="file-row" data-action="parent-folder">↰ 返回上一级</button>':''}${filesData.files.map(f=>{const path=(filePath==='.'?'':filePath+'/')+f.name;return f.directory?`<a href="#" class="file-row" data-folder="${esc(path)}">▧ ${esc(f.name)}<span>目录 →</span></a>`:`<a class="file-row" href="/api/files?path=${encodeURIComponent(path)}" download>▤ ${esc(f.name)}<span>${Math.ceil(f.size/1024)} KB ↓</span></a>`;}).join('')||'<div class="file-row muted">共享目录还是空的</div>'}</div>`}`);
  }
  content.innerHTML=html;
  if(view==='chat'&&nearBottom) content.scrollTop=content.scrollHeight;
}

async function changeView(next) {
  view=next; signature=''; $('#sidebar').classList.remove('open');
  if(next==='files') await loadFiles();
  await refresh(true);
}
async function loadFiles() {
  fileError=''; filesData=null;
  try { filesData=await api('/files?path='+encodeURIComponent(filePath)); } catch(e) { fileError=e.message; }
  if(view==='files') renderContent();
}
async function health() {
  try { const result=await api('/health'); $('#computer-dot').className='status-dot '+(result.computer?'online':'waiting'); $('#computer-status').textContent=result.computer?'共享电脑已连接':'共享电脑未连接'; }
  catch(e) { $('#computer-status').textContent='工作室连接中断'; }
}

function botForm(edit=false) {
  const bot=edit?botById(selected):null;
  if(edit&&!bot) return;
  openDialog(edit?'编辑 '+bot.name:'认识一位新伙伴',`<form id="bot-form" data-id="${bot?.id||''}"><label>名字<input name="name" required maxlength="80" value="${esc(bot?.name||'')}" placeholder="例如：研究员"></label><label>职责与工作方式<textarea name="role" rows="4" maxlength="12000" placeholder="告诉 Bot 它负责什么，交付什么，以及你的偏好。">${esc(bot?.role||'')}</textarea></label><label>长期记忆<textarea name="memory" rows="3" maxlength="12000" placeholder="稳定的偏好与背景，可随时查看和修改。">${esc(bot?.memory||'')}</textarea></label><p class="help">每位 Bot 保留独立的对话和记忆，共享电脑上的文件、浏览器登录态和技能。</p>${dialogFooter}</form>`);
}
function skillForm(id) {
  const skill=state.skills.find(s=>s.id===id);
  openDialog(skill?'编辑技能':'保存一个好方法',`<form id="skill-form"><label>技能名称<input name="name" required maxlength="100" value="${esc(skill?.name||'')}"></label><label>执行方法<textarea name="instructions" rows="10" required maxlength="20000" placeholder="适用场景、输入、操作步骤、检查方式、输出，以及需要你决定的地方。">${esc(skill?.instructions||'')}</textarea></label>${dialogFooter}</form>`);
}
function routineForm() {
  openDialog('创建定时任务',`<form id="routine-form"><label>名称<input name="name" required maxlength="100" placeholder="例如：每日研究简报"></label><label>交给哪位 Bot<select name="bot_id">${state.bots.map(b=>`<option value="${b.id}" ${b.id===selected?'selected':''}>${esc(b.name)}</option>`).join('')}</select></label><label>每隔多少分钟<input name="interval_minutes" type="number" min="5" max="525600" value="1440" required></label><label>任务描述<textarea name="prompt" rows="5" required maxlength="12000" placeholder="描述输入来源、工作内容和预期结果。"></textarea></label><p class="help">保存后经过一个间隔开始首次执行。Windows 休眠或 WSL 停止期间不运行，恢复后合并漏掉的触发。</p>${dialogFooter}</form>`);
}
function settings() {
  openDialog('工作室设置',`<p class="help">部署方式：WSL2 + Docker · 个人工作室</p><p class="help">当前模型：${esc(state.model.name||'尚未设置')}<br>模型连接：${state.model.configured?'已填写配置，实际可用性由任务验证':'等待配置'}</p><pre>MODEL_BASE_URL=https://你的服务地址/v1\nMODEL_NAME=支持工具调用的模型名\nMODEL_API_KEY=你的密钥</pre><p class="help">在项目根目录的 .env 中修改这些值，然后运行 docker compose up -d。密钥仅由后端读取。容器内访问宿主机服务时，用 host.docker.internal 替代 localhost。</p><p class="help">任务、聊天和记忆保存在 app-data 卷；文件保存在 workspace 卷；登录态保存在 browser-profile 卷。</p><div class="dialog-footer"><button class="secondary" data-action="logout">退出登录</button><button class="primary" data-action="close-dialog">知道了</button></div>`);
}

async function taskDetail(id) {
  const task=await api('/tasks/'+id);
  openDialog('任务执行记录',`<p class="help">${esc(botById(task.bot_id)?.name)} · ${date(task.created)} ${badge(task.status)}</p><p class="help">${esc(task.prompt)}</p>${task.status==='failed'?`<p class="help">${esc(task.result)}</p>`:''}<div>${task.events.map(e=>`<div class="event"><div class="event-meta"><span>${eventNames[e.kind]||esc(e.kind)}</span><span>${date(e.created)}</span></div>${['tool_start','tool_result','delegation','approval'].includes(e.kind)?`<details><summary>查看详情</summary><pre>${esc(pretty(e.content))}</pre></details>`:`<p>${esc(e.content)}</p>`}</div>`).join('')}</div><div class="dialog-footer"><button class="secondary" data-task="${id}">↻ 刷新</button><button class="primary" data-action="close-dialog">关闭</button></div>`);
}
function pretty(text) { try {return JSON.stringify(JSON.parse(text),null,2);}catch{return text;} }

function showComputer() {
  panel=!panel; $('#computer-panel').classList.toggle('hidden',!panel);
  if(!panel)return;
  $('#computer-panel').innerHTML=`<div class="panel-header"><h2>共享电脑</h2><button class="icon-button" data-action="computer" aria-label="关闭电脑面板">×</button></div><p class="computer-description">正在查看 <strong>${esc(botById(selected)?.name)}</strong> 的浏览器。各 Bot 使用不同标签页，共享登录态。点击画面可以接管操作。</p><div class="screen-wrap"><img id="screen" alt="Bot 的浏览器画面"><p id="screen-error" class="screen-error hidden"></p></div><div class="computer-controls"><button class="secondary" data-action="refresh-screen">↻ 刷新画面</button><button class="secondary" data-key="Tab">Tab</button><button class="secondary" data-key="Enter">Enter</button><button class="secondary" data-key="Backspace">⌫</button><button class="secondary" data-scroll="-600">↑ 滚动</button><button class="secondary" data-scroll="600">↓ 滚动</button></div><form id="navigate-form" class="inline-form"><input name="url" type="url" required placeholder="https://…" aria-label="浏览器地址"><button class="secondary compact" type="submit">前往</button></form><form id="type-form" class="inline-form"><input name="text" type="password" autocomplete="off" required placeholder="输入到当前焦点（不写入聊天）" aria-label="输入到浏览器"><button class="secondary compact" type="submit">输入</button></form><p class="panel-status">Bot 执行期间不能人工操作。等待审批或任务结束后，可完成登录，再告诉 Bot 继续。此版本提供浏览器操作，完整桌面和实时画面将在后续加入。</p>`;
  refreshScreen();
}
async function refreshScreen() {
  if(!panel||!selected)return;
  const image=$('#screen'), error=$('#screen-error');
  image.onload=()=>{image.classList.remove('hidden');error.classList.add('hidden');};
  image.onerror=()=>{image.classList.add('hidden');error.textContent='暂时无法获取画面，请检查电脑服务后刷新。';error.classList.remove('hidden');};
  image.src=`/api/computer/${selected}/screen?t=${Date.now()}`;
}
async function computerAction(args) {
  const result=await post(`/computer/${selected}/action`,args);
  if(result.error)throw new Error(result.error);
  await refreshScreen();
}

document.addEventListener('click',async event=>{
  const target=event.target.closest('button,a');
  if(event.target.id==='screen') {
    const bounds=event.target.getBoundingClientRect();
    try {await computerAction({action:'mouse',x:(event.clientX-bounds.left)*1280/bounds.width,y:(event.clientY-bounds.top)*800/bounds.height});}catch(e){toast(e.message);} return;
  }
  if(!target)return;
  try {
    if(target.dataset.view) {await changeView(target.dataset.view);return;}
    if(target.dataset.bot) {selected=target.dataset.bot;messages=[];if(panel){panel=false;showComputer();}await changeView('chat');return;}
    if(target.dataset.prompt) {$('#prompt').value=target.dataset.prompt;$('#prompt').focus();return;}
    if(target.dataset.task) {await taskDetail(target.dataset.task);return;}
    if(target.dataset.skill) {skillForm(target.dataset.skill);return;}
    if(target.dataset.folder) {event.preventDefault();filePath=target.dataset.folder;await loadFiles();return;}
    if(target.dataset.approval) {target.disabled=true;await post(`/approvals/${target.dataset.approval}/${target.dataset.decision}`);await refresh(true);return;}
    if(target.dataset.routine) {await post(`/routines/${target.dataset.routine}/toggle`);await refresh(true);return;}
    if(target.dataset.cancel) {await post(`/tasks/${target.dataset.cancel}/cancel`);await refresh(true);return;}
    if(target.dataset.resume) {await post(`/tasks/${target.dataset.resume}/resume`);await refresh(true);return;}
    if(target.dataset.key) {await computerAction({action:'press',key:target.dataset.key});return;}
    if(target.dataset.scroll) {await computerAction({action:'scroll',delta:Number(target.dataset.scroll)});return;}
    const action=target.dataset.action;
    if(action==='new-bot')botForm();
    if(action==='edit-bot')botForm(true);
    if(action==='new-skill')skillForm();
    if(action==='new-routine')routineForm();
    if(action==='skills')await changeView('skills');
    if(action==='close-dialog')closeDialog();
    if(action==='settings')settings();
    if(action==='menu')$('#sidebar').classList.toggle('open');
    if(action==='computer')showComputer();
    if(action==='refresh-screen')await refreshScreen();
    if(action==='health')await health();
    if(action==='refresh-files')await loadFiles();
    if(action==='parent-folder'){filePath=filePath.includes('/')?filePath.slice(0,filePath.lastIndexOf('/')):'.';await loadFiles();}
    if(action==='logout'){await post('/logout');closeDialog();showLogin();}
  } catch(error){toast(error.message);target.disabled=false;}
});

document.addEventListener('submit',async event=>{
  event.preventDefault();
  const form=event.target, data=Object.fromEntries(new FormData(form)), button=form.querySelector('button[type="submit"]');
  if(button)button.disabled=true;
  try {
    if(form.id==='login-form'){await post('/login',data);form.reset();$('#login-error').textContent='';await refresh(true);await health();}
    if(form.id==='composer') {
      const prompt=$('#prompt').value.trim();if(!prompt)return;
      await post(`/bots/${selected}/tasks`,{prompt});$('#prompt').value='';await refresh(true);$('#content').scrollTop=$('#content').scrollHeight;
    }
    if(form.id==='bot-form') {
      const bot=await api(form.dataset.id?'/bots/'+form.dataset.id:'/bots',{method:form.dataset.id?'PUT':'POST',body:data});
      selected=bot.id;closeDialog();await changeView('chat');toast('Bot 已保存');
    }
    if(form.id==='skill-form'){await post('/skills',data);closeDialog();await refresh(true);toast('技能已保存');}
    if(form.id==='routine-form'){data.interval_minutes=Number(data.interval_minutes);await post('/routines',data);closeDialog();await refresh(true);toast('定时任务已创建');}
    if(form.id==='navigate-form'){await computerAction({action:'navigate',url:data.url});}
    if(form.id==='type-form'){const text=data.text;form.reset();await computerAction({action:'type',text});}
  } catch(error) {if(form.id==='login-form')$('#login-error').textContent=error.message;else toast(error.message);}
  finally {if(button)button.disabled=false;}
});
$('#prompt').addEventListener('keydown',event=>{if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing){event.preventDefault();if(!$('#send').disabled)$('#composer').requestSubmit();}});
$('#dialog').addEventListener('click',event=>{if(event.target===$('#dialog')){const r=event.target.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)closeDialog();}});
await refresh(true);
if(loggedIn)await health();
setInterval(()=>{if(loggedIn&&!document.hidden)refresh();},2000);
setInterval(()=>{if(loggedIn&&!document.hidden)health();},30000);
