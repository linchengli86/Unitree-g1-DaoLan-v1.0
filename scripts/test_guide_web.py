#!/usr/bin/env python3
"""Hardware-free regressions for the guide website's routed presentation."""

import json
import shutil
import subprocess
import unittest
from html.parser import HTMLParser
from unittest.mock import patch

from guide_web import CSS, SCRIPT, WEB_ROUTES, render_page


EXPECTED_ROUTES = {
    "/setup": "setup",
    "/": "home",
    "/initialize": "initialize",
    "/map": "map",
    "/navigation": "navigation",
    "/assistant": "assistant",
    "/agent": "agent",
    "/knowledge": "knowledge",
    "/tasks": "tasks",
    "/points": "points",
    "/points/new": "point_new",
    "/localization": "localization",
    "/system": "system",
}


class PageParser(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.elements = []
        self.ancestors = {}
        self._stack = []
        self.title = ""
        self._in_title = False
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.elements.append((tag, attrs))
        if attrs.get("id"):
            self.ancestors[attrs["id"]] = list(self._stack)
        if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}:
            self._stack.append((tag, attrs))
        if tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        for i in range(len(self._stack) - 1, -1, -1):
            if self._stack[i][0] == tag:
                del self._stack[i:]
                break
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data

    def attrs(self, tag):
        return [attrs for name, attrs in self.elements if name == tag]


class GuideWebTests(unittest.TestCase):
    def test_exact_route_contract(self):
        self.assertEqual(set(WEB_ROUTES), set(EXPECTED_ROUTES))
        for path, key in EXPECTED_ROUTES.items():
            with self.subTest(path=path):
                meta = WEB_ROUTES[path]
                self.assertEqual(meta["key"], key)
                self.assertTrue(meta["title"].strip())
                self.assertTrue(meta["description"].strip())

    def test_ids_are_unique_on_every_direct_route(self):
        for path in EXPECTED_ROUTES:
            with self.subTest(path=path):
                page = PageParser(render_page(path))
                ids = [attrs["id"] for _, attrs in page.elements if "id" in attrs]
                self.assertEqual(len(ids), len(set(ids)))
                for required in ("stop", "text", "point-name", "point-photo", "point-list", "reloc-button",
                                 "agent-point", "agent-topic", "knowledge-point", "knowledge-file"):
                    self.assertIn(required, ids)

    def test_direct_route_title_and_page_key(self):
        for path, key in EXPECTED_ROUTES.items():
            with self.subTest(path=path):
                page = PageParser(render_page(path))
                self.assertEqual(page.attrs("body")[0]["data-page"], key)
                self.assertIn(WEB_ROUTES[path]["title"], page.title)

    def test_exactly_one_view_is_initially_visible(self):
        for path, key in EXPECTED_ROUTES.items():
            with self.subTest(path=path):
                page = PageParser(render_page(path))
                views = [attrs for tag, attrs in page.elements if "data-view" in attrs]
                self.assertEqual({view["data-view"] for view in views}, set(EXPECTED_ROUTES.values()))
                self.assertEqual(len(views), len(EXPECTED_ROUTES))
                visible = [view["data-view"] for view in views if "hidden" not in view]
                self.assertEqual(visible, [key])

    def test_all_sections_have_real_links_and_global_stop(self):
        for path in EXPECTED_ROUTES:
            with self.subTest(path=path):
                page = PageParser(render_page(path))
                hrefs = {attrs.get("href") for attrs in page.attrs("a")}
                self.assertTrue(set(EXPECTED_ROUTES).issubset(hrefs))
                stops = [attrs for tag, attrs in page.elements if tag == "button" and attrs.get("id") == "stop"]
                self.assertEqual(len(stops), 1)
                self.assertIn("stopRobot", stops[0].get("onclick", ""))
                self.assertNotIn("hidden", stops[0])
                self.assertFalse(any("data-view" in attrs or "hidden" in attrs
                                     for _, attrs in page.ancestors["stop"]))

    def test_mobile_and_accessible_navigation(self):
        page = PageParser(render_page("/points/new"))
        self.assertEqual(page.attrs("html")[0].get("lang"), "zh-CN")
        viewport = [meta for meta in page.attrs("meta") if meta.get("name") == "viewport"]
        self.assertEqual(len(viewport), 1)
        self.assertIn("width=device-width", viewport[0].get("content", ""))
        self.assertTrue(any(nav.get("aria-label") for nav in page.attrs("nav")))
        self.assertTrue(any(anchor.get("aria-current") == "page" for anchor in page.attrs("a")))
        self.assertIn("@media", CSS)
        self.assertIn("[hidden]", CSS)
        self.assertIn(".live-map-card .buttons{display:grid;grid-template-columns:repeat(3,minmax(0,1fr))}", CSS)
        self.assertIn("word-break:keep-all;overflow-wrap:normal", CSS)

    def test_render_is_static_read_only_and_excludes_private_data(self):
        # GET presentation must not load robot SDK, capture TF, read point files,
        # or inject private runtime configuration into the HTML response.
        with patch("builtins.open", side_effect=AssertionError("render read a file")), \
                patch("subprocess.Popen", side_effect=AssertionError("render spawned a command")):
            html = render_page("/points")
        for private_value in ("DASHSCOPE_API_KEY", "GUIDE_OPERATOR_PIN", "run/config/omni.env"):
            self.assertNotIn(private_value, html)
        self.assertNotRegex(SCRIPT, r"innerHTML\s*=\s*[^;\n]*point\.(?:name|speech)")
        self.assertNotIn("unitree_sdk2py", html)

    def test_agent_plan_visibility_and_explicit_read_only_preview(self):
        page = PageParser(render_page("/agent"))
        panel = next(attrs for _, attrs in page.elements if attrs.get("id") == "plan-panel")
        self.assertNotIn("hidden", panel)
        inputs = {attrs.get("id"): attrs for _, attrs in page.elements}
        self.assertEqual(inputs["agent-topic"]["maxlength"], "200")
        self.assertEqual(inputs["agent-conceive"]["onclick"], "conceivePoint()")
        self.assertEqual(inputs["agent-prepare"]["onclick"], "preparePointTour()")
        self.assertIn("不播报、不移动", render_page("/agent"))
        self.assertIn("不是固定台词", render_page("/points/new"))

    def test_document_upload_limits_and_pdf_is_not_a_fact_source(self):
        html = render_page("/knowledge")
        self.assertIn("PDF 当前仅保存待解析，不作为可播报事实", html)
        self.assertIn("仅在勾选审核确认后进入先验", html)
        self.assertIn("2*1024*1024", SCRIPT)
        self.assertNotIn("innerHTML", SCRIPT)

    def test_initialization_entry_is_navigation_not_automatic_post(self):
        home = PageParser(render_page("/"))
        entry = next(attrs for tag, attrs in home.elements if attrs.get("id") == "home-initialize")
        self.assertEqual(entry["href"], "/initialize")
        self.assertNotIn("onclick", entry)
        page = PageParser(render_page("/initialize"))
        ids = {attrs.get("id"): attrs for _, attrs in page.elements}
        self.assertEqual(ids["initialize-ready"].get("type"), "checkbox")
        self.assertEqual(ids["initialize-pin"].get("type"), "password")
        self.assertIn("disabled", ids["initialize-start"])
        self.assertIn("disabled", ids["manual-reloc-start"])
        self.assertEqual(ids["manual-reloc-confirmed"].get("type"), "checkbox")
        html = render_page("/initialize")
        self.assertIn("StopMove", html)
        self.assertIn("固定半径 1 米", SCRIPT)
        self.assertIn("无需用鼠标点到 20 cm 内", html)
        self.assertIn("约 ±45°", html)
        self.assertIn("在1米范围匹配定位（不行走）", html)
        self.assertIn("首次配准仍需实机核验", html)
        self.assertNotIn("初值应尽量准确到真实站位", html)
        self.assertIn("不要猜测", html)
        self.assertIn("8×", html)
        self.assertIn("不会使能自动行走", html)
        self.assertIn("getBoundingClientRect", SCRIPT)
        self.assertIn("overflow:auto", CSS)

    def test_setup_has_explicit_manual_mapping_and_review_controls(self):
        page = PageParser(render_page('/setup'))
        ids = {attrs.get('id'): attrs for _, attrs in page.elements}
        for name in ('mapping-ready', 'mapping-finish-ready', 'mapping-reviewed', 'setup-reviewed'):
            self.assertEqual(ids[name]['type'], 'checkbox')
        for name in ('mapping-start', 'mapping-finish', 'mapping-activate'):
            self.assertIn('disabled', ids[name])
        self.assertEqual(ids['setup-pin']['type'], 'password')
        self.assertIn('旧点位登记将归档', render_page('/setup'))

    @unittest.skipUnless(shutil.which('node'), 'Node is optional for browser contracts')
    def test_setup_browser_never_autostarts_and_submits_exact_confirmations(self):
        harness = r"""
const vm=require('vm'),els=new Map(),requests=[];let mapping={state:'idle',message:'idle'},promptValue=null;
class Element{constructor(){this.dataset={};this.checked=false;this.value='';this.files=[];this.style={};this.children=[];}append(...x){this.children.push(...x);}appendChild(x){this.append(x);}replaceChildren(...x){this.children=x;}setAttribute(){}removeAttribute(){}addEventListener(){}querySelectorAll(){return [];}focus(){}}
const keys=['setup','home','initialize','map','navigation','assistant','agent','knowledge','tasks','points','point_new','localization','system'];
const views=keys.map(k=>{const e=new Element();e.dataset.view=k;return e;});
const document={body:new Element(),hidden:false,getElementById(k){if(!els.has(k))els.set(k,new Element());return els.get(k);},createElement(){return new Element();},querySelectorAll(s){return s.includes('data-view')?views:[];},addEventListener(){}};document.body.dataset.page='setup';
const generic={status:'success',message:'okay',points:[],seeds:[],arm_gestures:{},task:null,requires_pin:true,relocation:{state:'idle'},initialization:{state:'idle'}};
const sandbox={document,location:{pathname:'/setup',href:'http://localhost/setup'},window:{addEventListener(){},scrollTo(){}},history:{pushState(){}},URL,AbortController,console,performance:{now:()=>100},setTimeout(){return 1;},clearTimeout(){},setInterval(){return 1;},clearInterval(){},prompt(){return promptValue;},alert(){},confirm(){return false;},fetch:async(path,options={})=>{const method=options.method||'GET';let body=options.body?JSON.parse(options.body):null;requests.push({path,method,body});if(method==='POST'){if(path.endsWith('/start'))mapping={state:'starting',message:'start',session_id:'a'.repeat(32)};if(path.endsWith('/finish'))mapping={state:'saving',message:'save',session_id:'a'.repeat(32)};if(path.endsWith('/activate'))mapping={state:'succeeded',message:'activated',session_id:'a'.repeat(32)};if(path.endsWith('/cancel'))mapping={state:'cancelled',message:'cancelled'};}return {ok:true,json:async()=>({...generic,mapping,has_map:false})};}};
vm.runInNewContext(SCRIPT_VALUE,sandbox,{timeout:1000});
const tick=()=>new Promise(resolve=>setImmediate(resolve)),assert=(v,m)=>{if(!v)throw new Error(m);},posts=()=>requests.filter(r=>r.method==='POST');
setImmediate(async()=>{try{
 await tick();assert(posts().length===0,'page load started hardware');
 await sandbox.mappingAction('start');assert(posts().length===0,'unchecked start');
 document.getElementById('mapping-ready').checked=true;await sandbox.mappingAction('start');assert(posts().length===0,'cancelled PIN prompt started');
 promptValue='246810';await sandbox.mappingAction('start');assert(posts().length===1,'explicit start not submitted');
 assert(Object.keys(posts()[0].body).sort().join(',')==='manual_only,pin,robot_ready','start payload injection');
 mapping={state:'mapping',session_id:'a'.repeat(32),message:'collecting'};await sandbox.loadMapping();
 await sandbox.mappingAction('finish');assert(posts().length===1,'unchecked save');
 document.getElementById('mapping-finish-ready').checked=true;await sandbox.mappingAction('finish');assert(posts()[1].body.stationary===true,'save confirmation missing');
 mapping={state:'review',session_id:'a'.repeat(32),message:'review'};await sandbox.loadMapping();
 assert(!document.getElementById('mapping-review-box').hidden,'preview hidden');
 await sandbox.mappingAction('activate');assert(posts().length===2,'unchecked activation');
 document.getElementById('mapping-reviewed').checked=true;await sandbox.mappingAction('activate');assert(posts()[2].body.map_reviewed===true,'map review missing');
 promptValue=null;await sandbox.mappingAction('cancel');assert(posts().length===4,'cancel unnecessarily required PIN');
 assert(Object.keys(posts()[3].body).length===0,'cancel parameters');
 console.log('setup browser contract passed');
 }catch(e){console.error(e.stack);process.exitCode=1;}});
"""
        command = harness.replace('SCRIPT_VALUE', json.dumps(SCRIPT))
        result = subprocess.run(['node', '-e', command], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('setup browser contract passed', result.stdout)

    def test_unknown_routes_are_not_silently_home(self):
        for path in ("/missing", "/points/../../system", "/<script>alert(1)</script>", "https://example.com", ""):
            with self.subTest(path=path), self.assertRaises(ValueError):
                render_page(path)

    def test_live_map_entry_is_read_only_and_has_no_goal_controls(self):
        page = PageParser(render_page("/"))
        entry = next(attrs for _, attrs in page.elements if attrs.get("id") == "home-live-map")
        self.assertEqual(entry["href"], "/map")
        self.assertNotIn("onclick", entry)
        page = PageParser(render_page("/map"))
        map_buttons = [attrs for tag, attrs in page.elements if tag == "button" and
                       any(parent.get("data-view") == "map" for _, parent in page.ancestors.get(attrs.get("id"), []))]
        html = render_page("/map")
        self.assertIn("点击不会发送目标", html)
        self.assertIn("不会永久写入地图", html)
        self.assertIn("16×", html)
        self.assertIn("AbortController", SCRIPT)
        self.assertIn("performance.now()", SCRIPT)
        self.assertNotIn("relocal", " ".join(button.get("onclick", "") for button in map_buttons))

    @unittest.skipUnless(shutil.which("node"), "Node is optional for live map browser tests")
    def test_live_map_polling_ttl_coordinates_and_cancellation_are_read_only(self):
        harness = r"""
const vm=require('vm'),elements=new Map(),requests=[],listeners={},timers=new Map();let nextTimer=0,clock=100000,draws=[],imageRequests=[];
function context(){return {fillStyle:'',strokeStyle:'',clearRect(){draws=[];},drawImage(){},beginPath(){},arc(x,y){draws.push({color:this.fillStyle,x,y});},fill(){},moveTo(){},lineTo(){},stroke(){draws.push({color:this.strokeStyle,arrow:true});},fillText(){},
  getImageData(){return {data:new Uint8ClampedArray([0,0,0,255,255,255,255,255,205,205,205,255])};},putImageData(pixels){this.pixels=Array.from(pixels.data);}};}
class Element{
 constructor(id=''){this.id=id;this.children=[];this.dataset={};this.value='';this.files=[];this.checked=false;this.hidden=false;this.disabled=false;this.style={};this.attrs={};this.textContent='';this.width=100;this.height=80;this.clientWidth=600;this.clientHeight=300;this.scrollLeft=0;this.scrollTop=0;this.context=context();}
 append(...nodes){this.children.push(...nodes);}appendChild(node){this.append(node);return node;}setAttribute(k,v){this.attrs[k]=String(v);}getAttribute(k){return this.attrs[k]||null;}removeAttribute(k){delete this.attrs[k];}addEventListener(){}focus(){}querySelectorAll(){return [];}
 getContext(){return this.context;}getBoundingClientRect(){const zoom=parseFloat(this.style.width||'100')/100;return {left:0,top:0,width:600*zoom,height:480*zoom};}setPointerCapture(){}
 set innerHTML(value){throw new Error('Untrusted HTML insertion');}
}
class ImageDouble{constructor(){this.naturalWidth=100;this.naturalHeight=80;}set src(value){if(value){imageRequests.push(value);setImmediate(()=>this.onload());}}}
const location={pathname:'/',href:'http://localhost/'},keys=['home','initialize','map','navigation','assistant','agent','knowledge','tasks','points','point_new','localization','system'];
const views=keys.map(key=>{const e=new Element();e.dataset.view=key;return e;});
const document={body:new Element('body'),hidden:false,title:'',getElementById(id){if(!elements.has(id))elements.set(id,new Element(id));return elements.get(id);},createElement(){return new Element();},querySelectorAll(selector){return selector.includes('data-view')?views:[];},addEventListener(name,fn){listeners[name]=fn;}};document.body.dataset.page='home';
const fingerprint='f'.repeat(64),map={frame_id:'map',map_fingerprint:fingerprint,resolution:.5,width:100,height:80,origin:{x:10,y:20,yaw:Math.PI/2},image_url:'/api/map/image',negate:0,free_thresh:.196,occupied_thresh:.65};
const point={id:'1'.repeat(32),name:'<script>这是展板</script>',speech:'先验',pose:{x:-10,y:45,yaw:0},map_fingerprint:fingerprint};
const empty={state:'unavailable',points:[],stamp:null,age_seconds:null};
const fresh=()=>({state:'ready',message:'定位有效',frame_id:'map',map_fingerprint:fingerprint,updated_at:100,pose:{x:-10,y:45,yaw:Math.PI/2,stamp:100,age_seconds:.1},scan:{state:'ready',points:[[-10,45]],stamp:100,age_seconds:.1},costmap:{state:'ready',points:[[-10,45]],stamp:100,age_seconds:.1}});
let live=fresh(),fail=false,hold=false,pendingResolve=null,lastSignal=null;
const read={status:'success',message:'okay',task:null,points:[point],requires_pin:false,seeds:[],relocation:{state:'idle',message:'idle'},arm_gestures:{}};
const sandbox={document,location,window:{addEventListener(){},scrollTo(){}},history:{pushState(_,__,path){location.pathname=path;}},Image:ImageDouble,AbortController,URL,console,performance:{now:()=>clock},
 setTimeout(fn,delay){const id=++nextTimer;timers.set(id,{fn,delay});return id;},clearTimeout(id){timers.delete(id);},setInterval(){return 1;},clearInterval(){},alert(){throw new Error('Unexpected alert');},confirm(){throw new Error('Unexpected confirm');},prompt(){throw new Error('Unexpected prompt');},
 fetch:async(path,options={})=>{const method=options.method||'GET';requests.push({path,method});if(method!=='GET')throw new Error('Live view mutation');
 const response=value=>({ok:true,json:async()=>value});if(path==='/api/map')return response({status:'success',map});
 if(path==='/api/map/live'){lastSignal=options.signal;if(hold)return new Promise(resolve=>{pendingResolve=()=>resolve(response({status:'success',live:fresh()}));});if(fail)throw new Error('network lost');return response({status:'success',live});}
 return response(read);}
};
vm.runInNewContext(SCRIPT_VALUE,sandbox,{timeout:1000});
const tick=()=>new Promise(resolve=>setImmediate(resolve)),assert=(condition,message)=>{if(!condition)throw new Error(message);},near=(a,b)=>assert(Math.abs(a-b)<1e-8,'Coordinate mismatch');
setImmediate(async()=>{try{
 assert(!requests.some(r=>r.path==='/api/map/live'),'Polling happened outside map route');
 sandbox.navigateTo('/map');await tick();await tick();await tick();
 assert(requests.filter(r=>r.path==='/api/map/live').length===1,'Map did not perform its initial read');
 assert(document.getElementById('live-map-position').textContent.includes('x=-10.00'),'Live coordinates not shown');
 assert(draws.some(d=>d.color==='#188553')&&draws.some(d=>d.color==='#f69b24')&&draws.some(d=>d.color==='#dc4141'),'Fresh robot / obstacles missing');
 const p=sandbox.canvasFromWorld(-10,45,map,document.getElementById('live-map'));near(p.cx,50);near(p.cy,40);
 assert(draws.filter(d=>['#188553','#f69b24','#dc4141'].includes(d.color)&&!d.arrow).every(d=>Math.abs(d.x-50)<1e-8&&Math.abs(d.y-40)<1e-8),'Live layer world transform differs from map');
 const bg=sandbox.simplifyLiveMap(new ImageDouble(),map).getContext('2d').pixels;
 assert(bg.slice(0,3).join()==='45,55,69'&&bg.slice(4,7).join()==='240,244,247'&&bg.slice(8,11).join()==='160,171,183','Occupied / free / unknown classification changed');
 assert(imageRequests[0].includes('?fingerprint='+fingerprint),'Map image is not fingerprint bound');
 assert(Array.from(timers.values()).some(t=>t.delay===500),'Live polling cadence missing');
 clock+=1600;sandbox.renderLiveMapState();assert(!draws.some(d=>['#188553','#f69b24','#dc4141'].includes(d.color)),'Expired pose kept live markers');
 assert(!document.getElementById('live-map-status').className.includes('ok'),'Expired pose remained green');
 live=fresh();live.scan.age_seconds=1.6;live.costmap.age_seconds=2.0;await sandbox.pollLiveMap();
 assert(!draws.some(d=>d.color==='#f69b24')&&draws.some(d=>d.color==='#dc4141'),'Independent sensor age limits incorrect');
 fail=true;await sandbox.pollLiveMap();assert(!draws.some(d=>['#188553','#f69b24','#dc4141'].includes(d.color)),'Network loss kept live overlay');fail=false;
 live={state:'starting',message:'正在连接 ROS',frame_id:'map',map_fingerprint:null,updated_at:null,pose:null,scan:empty,costmap:empty};await sandbox.pollLiveMap();
 assert(document.getElementById('live-map-status').textContent.includes('正在连接 ROS'),'Starting state with unknown context rejected');
 live={...fresh(),state:'unlocalized',pose:{x:0,y:0,yaw:0,stamp:100,age_seconds:0}};await sandbox.pollLiveMap();assert(!draws.some(d=>d.color==='#188553'),'Unlocalized zero TF shown as robot');
 live=fresh();live.scan.points=[[NaN,2]];await sandbox.pollLiveMap();assert(!draws.some(d=>d.color==='#188553'),'Invalid sensor data retained pose');
 live={...fresh(),map_fingerprint:'a'.repeat(64)};const readsBefore=requests.filter(r=>r.path==='/api/map').length;
 await sandbox.pollLiveMap();await sandbox.pollLiveMap();await sandbox.pollLiveMap();
 assert(requests.filter(r=>r.path==='/api/map').length===readsBefore+2,'Mismatch refresh was not bounded');
 assert(!draws.some(d=>d.color==='#188553'),'Mismatched map displayed pose');
 assert(!Array.from(timers.values()).some(t=>t.delay===500),'Persistent mismatch kept scheduling overlays');
 live=fresh();sandbox.refreshLiveMap();await tick();await tick();await tick();
 assert(draws.some(d=>d.color==='#188553'),'Explicit refresh did not recover');
 for(let i=0;i<8;i++)sandbox.zoomLiveMap(2);assert(document.getElementById('live-map').style.width==='1600%','Zoom is not bounded to 16x');
 const wrap=document.getElementById('live-map-wrap');wrap.scrollLeft=10;wrap.scrollTop=10;
 sandbox.liveMapPointerDown({button:0,pointerId:1,clientX:50,clientY:50,preventDefault(){}});sandbox.liveMapPointerUp({clientX:40,clientY:40,preventDefault(){}});
 assert(wrap.scrollLeft===20&&wrap.scrollTop===20,'Read-only drag did not pan');
 hold=true;const pending=sandbox.pollLiveMap();await tick();const count=requests.filter(r=>r.path==='/api/map/live').length;await sandbox.pollLiveMap();
 assert(requests.filter(r=>r.path==='/api/map/live').length===count,'Overlapping live reads allowed');
 sandbox.navigateTo('/assistant');assert(lastSignal.aborted,'Leaving map did not abort fetch');pendingResolve();await pending;
 assert(!draws.some(d=>d.color==='#188553'),'Late response after navigation restored marker');
 hold=false;sandbox.navigateTo('/map');await tick();await tick();document.hidden=true;listeners.visibilitychange();
 const hiddenCount=requests.filter(r=>r.path==='/api/map/live').length;await sandbox.pollLiveMap();assert(requests.filter(r=>r.path==='/api/map/live').length===hiddenCount,'Hidden tab kept polling');
 document.hidden=false;listeners.visibilitychange();await tick();await tick();assert(requests.filter(r=>r.path==='/api/map/live').length>hiddenCount,'Visible tab did not resume');
 assert(requests.every(r=>r.method==='GET'),'Map controls issued mutation');
 console.log(JSON.stringify({live_reads:requests.filter(r=>r.path==='/api/map/live').length,map_reads:requests.filter(r=>r.path==='/api/map').length,all_get:true}));
}catch(error){console.error(error);process.exitCode=1;}});
""".replace("SCRIPT_VALUE", json.dumps(SCRIPT, ensure_ascii=True))
        result = subprocess.run([shutil.which("node"), "-e", harness], capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout.strip().splitlines()[-1])["all_get"])

    @unittest.skipUnless(shutil.which("node"), "Node is optional for the browser bootstrap smoke test")
    def test_browser_bootstrap_and_navigation_are_read_only(self):
        # Run the real script against a minimal browser double. A network call
        # using POST/PUT/PATCH/DELETE fails before it can leave this process.
        harness = r"""
const vm = require('vm');
const elements = new Map(), requests = [], listeners = {};
class Element {
  constructor(id='') { this.id=id; this.dataset={}; this.children=[]; this.value=''; this.files=[]; this.hidden=false; this.textContent=''; this.attrs={}; this.style={}; this.className=''; this.disabled=false;
    this.classList={add(){},remove(){},toggle(){},contains(){return false;}}; }
  append(...nodes) { this.children.push(...nodes); }
  appendChild(node) { this.append(node); return node; }
  replaceChildren(...nodes) { this.children=nodes; }
  setAttribute(key,value) { this.attrs[key]=String(value); }
  getAttribute(key) { return this.attrs[key] || null; }
  removeAttribute(key) { delete this.attrs[key]; }
  addEventListener() {}
  querySelectorAll() { return []; }
  querySelector() { return null; }
  focus() {}
  closest() { return null; }
}
const keys=['home','setup','initialize','map','navigation','assistant','agent','knowledge','tasks','points','point_new','localization','system'];
const views=keys.map(key=>{const el=new Element(); el.dataset.view=key; return el;});
const body=new Element('body'); body.dataset.page='home';
const location={pathname:'/',search:'',hash:'',href:'http://localhost/'};
const document={body,title:'',getElementById(id){if(!elements.has(id))elements.set(id,new Element(id)); return elements.get(id);},
  createElement(tag){return new Element();},querySelectorAll(selector){return selector.includes('data-view')?views:[];},
  querySelector(){return null;},addEventListener(name,callback){listeners[name]=callback;}};
Object.defineProperty(Element.prototype,'innerHTML',{set(value){if(value)throw new Error('Data must not be inserted as HTML');}});
const points=Array.from({length:8},(_,i)=>({id:String(i),name:'<b>point '+i+'</b>',speech:'<script>untrusted</script>',pose:{x:1,y:2,yaw:0}}));
const readResponse={status:'success',message:'test',task:null,points,requires_pin:false,arm_gestures:{},seeds:[],relocation:{state:'idle',message:'test'},skills:[],actions:[],motion_enabled:false};
const window={location,addEventListener(name,callback){listeners[name]=callback;},scrollTo(){},matchMedia(){return {matches:false,addEventListener(){}};}};
const sandbox={document,window,location,history:{pushState(){},replaceState(){}},URL,console,
  setTimeout(){return 1;},clearTimeout(){},setInterval(){return 1;},clearInterval(){},
  alert(){throw new Error('Unexpected alert during bootstrap');},confirm(){throw new Error('Unexpected confirmation during bootstrap');},prompt(){throw new Error('Unexpected PIN prompt during bootstrap');},
  fetch:async(path,options={})=>{const method=(options.method||'GET').toUpperCase(); requests.push({path,method}); if(method!=='GET')throw new Error('Bootstrap mutation: '+method+' '+path); return {ok:true,status:200,json:async()=>readResponse};}};
process.on('unhandledRejection',error=>{console.error(error);process.exitCode=1;});
vm.runInNewContext(SCRIPT_VALUE,sandbox,{timeout:1000});
if(listeners.DOMContentLoaded)listeners.DOMContentLoaded();
const form=document.getElementById('point-name'),photo=document.getElementById('point-photo'),confirmation=document.getElementById('confirm');
form.value='unsaved point'; const upload={name:'pending-photo.jpg'}; photo.files=[upload]; confirmation.textContent='pending confirmation';
for(const [path,key] of [['/','home'],['/setup','setup'],['/initialize','initialize'],['/navigation','navigation'],['/assistant','assistant'],['/agent','agent'],['/knowledge','knowledge'],['/tasks','tasks'],['/points','points'],['/points/new','point_new'],['/localization','localization'],['/system','system']]){
  sandbox.navigateTo(path);
  if(body.dataset.page!==key||views.filter(view=>!view.hidden).length!==1||!views.some(view=>!view.hidden&&view.dataset.view===key))throw new Error('Navigation did not select '+key);
  if(form.value!=='unsaved point'||photo.files[0]!==upload||confirmation.textContent!=='pending confirmation')throw new Error('Navigation lost unsaved input or plan');
  if(document.getElementById('plan-panel').hidden!==!['assistant','tasks','agent','navigation'].includes(key))throw new Error('Plan panel visibility mismatch');
}
location.pathname='/points/new'; if(listeners.popstate)listeners.popstate();
if(body.dataset.page!=='point_new'||form.value!=='unsaved point'||photo.files[0]!==upload)throw new Error('Browser back lost form state');
setImmediate(()=>{
  if(requests.some(request=>request.method!=='GET'))process.exitCode=1;
  if(document.getElementById('home-point-count').textContent!=='8'||document.getElementById('point-list').children.length!==8)throw new Error('Saved point overview did not retain all 8 points');
  for(let i=0;i<8;i++)if(document.getElementById('point-list').children[i].children[0].textContent!==points[i].name)throw new Error('Point names were not rendered as plain text');
  console.log(JSON.stringify(requests));
});
""".replace("SCRIPT_VALUE", json.dumps(SCRIPT, ensure_ascii=True))
        result = subprocess.run([shutil.which("node"), "-e", harness], capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.strip(), "Browser bootstrap did not finish")
        requests = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertTrue(all(request["method"] == "GET" for request in requests))

    @unittest.skipUnless(shutil.which("node"), "Node is optional for explicit browser interaction tests")
    def test_explicit_agent_preview_and_document_upload_never_auto_execute(self):
        harness = r"""
const vm=require('vm'), elements=new Map(), requests=[];
class Element{
  constructor(){this.children=[];this.dataset={};this.value='';this.files=[];this.checked=false;this.hidden=false;this.disabled=false;this.attrs={};this.style={};this._text='';}
  set textContent(value){this._text=String(value);this.children=[];} get textContent(){return this._text;}
  set innerHTML(value){throw new Error('HTML insertion is forbidden');}
  append(...nodes){this.children.push(...nodes);}appendChild(node){this.append(node);return node;}
  setAttribute(k,v){this.attrs[k]=String(v);}getAttribute(k){return this.attrs[k]||null;}removeAttribute(k){delete this.attrs[k];}
  addEventListener(){}focus(){}querySelectorAll(){return [];}querySelector(){return null;}
}
const keys=['home','initialize','map','navigation','assistant','agent','knowledge','tasks','points','point_new','localization','system'];
const views=keys.map(key=>{const e=new Element();e.dataset.view=key;return e;});
const location={pathname:'/',href:'http://localhost/'};
const document={body:new Element(),title:'',getElementById(id){if(!elements.has(id))elements.set(id,new Element());return elements.get(id);},
createElement(){return new Element();},querySelectorAll(selector){return selector.includes('data-view')?views:[];},addEventListener(){}};
document.body.dataset.page='home';
const point_id='1'.repeat(32),point={id:point_id,name:'<b>展点</b>',speech:'已审核先验',pose:{x:1,y:2,yaw:0}};
const knowledge={sources:[{source_id:'<script>source</script>',type:'literature',reviewed:true}],documents:[{filename:'<script>paper</script>.txt',size_bytes:3,source_id:'document:test',status:'text_available',reviewed:true}]};
const read={status:'success',message:'okay',task:null,points:[point],requires_pin:false,seeds:[],relocation:{state:'idle',message:'idle'},arm_gestures:{},knowledge};
const conception={segments:['<script>不是 HTML</script>'],source_ids:['<b>source</b>'],needs_review:true,uncertainties:['需要人工核对']};
const plan={title:'动态导览',steps:[{action:'navigate_to_point',parameters:{point_id}},{action:'verify_arrival',parameters:{point_id}},{action:'present_point',parameters:{point_id,topic:'儿童'}}]};
const sandbox={document,location,window:{addEventListener(){},scrollTo(){}},history:{pushState(_,__,path){location.pathname=path;}},console,URL,
setTimeout(){return 1;},clearTimeout(){},setInterval(){return 1;},clearInterval(){},btoa(value){return Buffer.from(value,'binary').toString('base64');},
prompt(){throw new Error('Unexpected PIN prompt');},confirm(){throw new Error('Unexpected execution confirmation');},alert(){throw new Error('Unexpected alert');},
fetch:async(path,options={})=>{const method=options.method||'GET',body=options.body?JSON.parse(options.body):null;requests.push({path,method,body});
  if(method==='GET')return {ok:true,json:async()=>read};
  if(path==='/api/agent/conceive')return {ok:true,json:async()=>({status:'success',conception})};
  if(path==='/api/agent/prepare')return {ok:true,json:async()=>({status:'success',plan,confirmation:'preview-token',requires_pin:true})};
  if(path==='/api/points/'+point_id+'/documents')return {ok:true,json:async()=>({status:'success',message:'已保存'})};
  throw new Error('Unexpected mutation '+method+' '+path);
}};
vm.runInNewContext(SCRIPT_VALUE,sandbox,{timeout:1000});
setImmediate(async()=>{try{
  if(requests.some(request=>request.method!=='GET'))throw new Error('Bootstrap mutated state');
  sandbox.selectAgentPoint(point_id);document.getElementById('agent-topic').value='儿童';
  await sandbox.conceivePoint();
  if(document.getElementById('agent-output').children[1].textContent!==conception.segments[0])throw new Error('Model text not preserved as plain text');
  if(document.getElementById('agent-output').children[0].textContent!=='构思预览 · 尚需核对')throw new Error('Uncertainty not shown');
  await sandbox.preparePointTour();
  if(document.body.dataset.page!=='agent'||document.getElementById('plan-panel').hidden)throw new Error('Plan did not remain on Agent page');
  if(!sandbox.stepLabel(plan.steps[0]).includes(point.name)||!sandbox.stepLabel(plan.steps[2]).includes('动态构思'))throw new Error('New skill labels missing');
  const posts=requests.filter(request=>request.method==='POST');
  if(posts.length!==2||posts[0].body.point_id!==point_id||posts[0].body.topic!=='儿童'||posts[1].body.point_ids[0]!==point_id)throw new Error('Agent request contract mismatch');
  sandbox.navigateTo('/knowledge');await sandbox.loadKnowledge();
  if(document.getElementById('knowledge-documents').children[1].textContent.indexOf(knowledge.documents[0].filename)!==0)throw new Error('Filename not rendered as plain text');
  function file(name,size=3){return {name,size,arrayBuffer:async()=>new Uint8Array([65,66,67]).buffer};}
  document.getElementById('knowledge-file').files=[file('reference.txt')];document.getElementById('knowledge-reviewed').checked=true;
  await sandbox.uploadDocument();
  document.getElementById('knowledge-file').files=[file('reference.pdf')];document.getElementById('knowledge-reviewed').checked=true;
  await sandbox.uploadDocument();
  const uploads=requests.filter(request=>request.path.endsWith('/documents')&&request.method==='POST');
  if(uploads.length!==2||uploads[0].body.reviewed!==true||uploads[1].body.reviewed!==false||uploads[0].body.file_base64!=='QUJD')throw new Error('Document review or Base64 contract mismatch');
  document.getElementById('knowledge-file').files=[file('oversize.txt',2*1024*1024+1)];await sandbox.uploadDocument();
  document.getElementById('agent-topic').value='x'.repeat(201);await sandbox.conceivePoint();
  if(requests.filter(request=>request.method==='POST').length!==4)throw new Error('Invalid input was sent');
  if(requests.some(request=>/speak|confirm|motion_enable|move_base/.test(request.path)))throw new Error('Preview auto-executed robot action');
  console.log(JSON.stringify(requests));
}catch(error){console.error(error);process.exitCode=1;}});
""".replace("SCRIPT_VALUE", json.dumps(SCRIPT, ensure_ascii=True))
        result = subprocess.run([shutil.which("node"), "-e", harness], capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        requests = json.loads(result.stdout.strip().splitlines()[-1])
        mutations = [request for request in requests if request["method"] == "POST"]
        self.assertEqual(len(mutations), 4)
        self.assertTrue(all(request["path"] == "/api/agent/conceive" or request["path"] == "/api/agent/prepare"
                            or request["path"].endswith("/documents") for request in mutations))

    @unittest.skipUnless(shutil.which("node"), "Node is optional for map coordinate and initialization interaction tests")
    def test_map_rotation_scaling_and_confirmed_initialization_flow(self):
        harness = r"""
const vm=require('vm'),elements=new Map(),requests=[],imageRequests=[],mapLabels=[],mapEllipses=[];
const drawing={clearRect(){},drawImage(){},beginPath(){},arc(){},ellipse(...args){mapEllipses.push(args);},fill(){},fillText(text){mapLabels.push(text);},moveTo(){},lineTo(){},stroke(){}};
class Element{
  constructor(){this.children=[];this.dataset={};this.value='';this.files=[];this.checked=false;this.hidden=false;this.disabled=false;this.attrs={};this.style={};this._text='';this.width=1;this.height=1;}
  set textContent(value){this._text=String(value);this.children=[];}get textContent(){return this._text;}
  set innerHTML(value){throw new Error('HTML insertion forbidden');}
  append(...nodes){this.children.push(...nodes);}appendChild(node){this.append(node);return node;}
  setAttribute(k,v){this.attrs[k]=String(v);}getAttribute(k){return this.attrs[k]||null;}removeAttribute(k){delete this.attrs[k];}
  addEventListener(){}focus(){}querySelectorAll(){return [];}getContext(){return drawing;}
  getBoundingClientRect(){const width=500*(parseFloat(this.style.width)||100)/100;return {left:100,top:50,width,height:width*this.height/this.width};}
  setPointerCapture(id){this.pointer=id;}hasPointerCapture(id){return this.pointer===id;}releasePointerCapture(){this.pointer=null;}
}
class ImageDouble{constructor(){this.naturalWidth=200;this.naturalHeight=160;}set src(value){imageRequests.push(value);queueMicrotask(()=>this.onload());}}
const keys=['home','initialize','map','assistant','agent','knowledge','tasks','points','point_new','localization','system'];
const views=keys.map(key=>{const e=new Element();e.dataset.view=key;return e;});
const location={pathname:'/',href:'http://localhost/'};
const document={body:new Element(),title:'',getElementById(id){if(!elements.has(id))elements.set(id,new Element());return elements.get(id);},
createElement(){return new Element();},querySelectorAll(selector){return selector.includes('data-view')?views:[];},addEventListener(){}};
document.body.dataset.page='home';
const fingerprint='f'.repeat(64),point={id:'1'.repeat(32),name:'已存点',pose:{x:-10,y:45,yaw:Math.PI/2},map_fingerprint:fingerprint};
const map={frame_id:'map',map_fingerprint:fingerprint,resolution:.5,width:100,height:80,origin:{x:10,y:20,yaw:Math.PI/2},image_url:'/api/map/image'};
let initialization={state:'idle',stages:[],message:'尚未初始化',localized:false,needs_initial_pose:false};
let relocation={state:'idle',message:'尚未重定位'};
const read={status:'success',message:'okay',task:null,points:[point],requires_pin:true,seeds:[],relocation,arm_gestures:{}};
const sandbox={document,location,window:{addEventListener(){},scrollTo(){}},history:{pushState(_,__,path){location.pathname=path;}},Image:ImageDouble,console,URL,
setTimeout(){return 1;},clearTimeout(){},setInterval(){return 1;},clearInterval(){},
prompt(){throw new Error('Unexpected prompt');},confirm(){throw new Error('Unexpected confirm');},alert(){throw new Error('Unexpected alert');},
fetch:async(path,options={})=>{const method=options.method||'GET',body=options.body?JSON.parse(options.body):null;requests.push({path,method,body});
  const response=value=>({ok:true,json:async()=>value});
  if(method==='GET'){
    if(path==='/api/init')return response({status:'success',requires_pin:true,initialization});
    if(path==='/api/map')return response({status:'success',map});
    if(path==='/api/relocation')return response({...read,relocation});
    return response(read);
  }
  if(path==='/api/init/start'){
    const now=Date.now()/1000;
    initialization={state:'succeeded',started_at:now-1,finished_at:now-.5,map_fingerprint:fingerprint,stages:[{id:'navigation',state:'succeeded',message:'导航软件已启动'}],message:'软件就绪，等待初值',localized:false,needs_initial_pose:true,pose:{x:0,y:0,yaw:0}};
    return response({status:'success',initialization:{state:'running',stages:[],message:'启动中'}});
  }
  if(path==='/api/relocation/manual'){
    const prior={x:body.x,y:body.y,yaw:body.yaw},pose={x:body.x+.6,y:body.y-.2,yaw:body.yaw+.3},now=Date.now()/1000;
    relocation={state:'succeeded',message:'定位已核验',pose,started_at:now-.1,finished_at:now,seed:{name:'地图点选当前位置',frame_id:'map',map_fingerprint:fingerprint,pose:prior}};
    // Initialization is a historical snapshot. It deliberately stays false
    // after a later successful manual localization to exercise the real bug.
    return response({status:'success',relocation:{state:'running',message:'定位中'}});
  }
  if(path==='/api/stop'){initialization={state:'cancelled',stages:[],message:'已停止初始化'};relocation={state:'cancelled',message:'已停止定位'};return response({status:'success',message:'已停止'});}
  throw new Error('Unexpected mutation '+path);
}};
vm.runInNewContext(SCRIPT_VALUE,sandbox,{timeout:1000});
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const near=(a,b)=>{if(Math.abs(a-b)>1e-8)throw new Error('Coordinate mismatch '+a+' != '+b);};
setImmediate(async()=>{try{
  if(requests.some(request=>request.method!=='GET'))throw new Error('Bootstrap mutation');
  const canvas=document.getElementById('initial-map'),numericCanvas={width:200,height:160};
  let w=sandbox.worldFromCanvas(0,160,{...map,origin:{x:10,y:20,yaw:0}},numericCanvas);near(w.x,10);near(w.y,20);
  w=sandbox.worldFromCanvas(0,0,{...map,origin:{x:10,y:20,yaw:0}},numericCanvas);near(w.x,10);near(w.y,60);
  w=sandbox.worldFromCanvas(100,80,map,numericCanvas);near(w.x,-10);near(w.y,45);
  const inverse=sandbox.canvasFromWorld(w.x,w.y,map,numericCanvas);near(inverse.cx,100);near(inverse.cy,80);
  // A 1 m world-space prior is a 4 px radius on this doubled native image,
  // independent of map-origin rotation and later CSS zoom. Native canvas
  // dimensions can be anisotropic, so the rendered ring becomes an ellipse.
  const ring=sandbox.initialPriorCircle({x:w.x,y:w.y},map,numericCanvas);near(ring.cx,100);near(ring.cy,80);near(ring.radiusX,4);near(ring.radiusY,4);
  const rotatedEdge=sandbox.canvasFromWorld(w.x+Math.cos(map.origin.yaw),w.y+Math.sin(map.origin.yaw),map,numericCanvas);near(Math.hypot(rotatedEdge.cx-ring.cx,rotatedEdge.cy-ring.cy),ring.radiusX);
  const anisotropic=sandbox.initialPriorCircle({x:w.x,y:w.y},map,{width:400,height:160});near(anisotropic.cx,200);near(anisotropic.cy,80);near(anisotropic.radiusX,8);near(anisotropic.radiusY,4);
  sandbox.navigateTo('/initialize');await sandbox.loadInitialization();
  if(requests.some(request=>request.method!=='GET'))throw new Error('Page navigation auto-started');
  await sandbox.startInitialization();
  if(requests.some(request=>request.method==='POST'))throw new Error('Unchecked physical readiness started initialization');
  document.getElementById('initialize-ready').checked=true;await sandbox.startInitialization();
  if(requests.some(request=>request.method==='POST'))throw new Error('Required PIN was bypassed');
  document.getElementById('initialize-pin').value='test-pin';await sandbox.startInitialization();await tick();
  if(document.getElementById('initial-map-panel').hidden)throw new Error('Manual map hidden after ready software');
  if(document.getElementById('initialize-position').textContent.includes('(0.00'))throw new Error('Unlocalized zero pose shown as actual map position');
  if(!document.getElementById('initialize-stages').children[0].textContent.includes('导航软件已启动'))throw new Error('Stage id/message schema not rendered');
  if(canvas.width!==200||canvas.height!==160||imageRequests.length!==1)throw new Error('Native image canvas not loaded');
  if(!mapLabels.some(label=>label.includes('已存点')))throw new Error('Saved point map markers missing');
  sandbox.zoomInitialMap(2);sandbox.zoomInitialMap(2);sandbox.zoomInitialMap(2);sandbox.zoomInitialMap(2);
  if(canvas.style.width!=='800%')throw new Error('Map zoom exceeds/misses 8x');
  function event(cx,cy){const rect=canvas.getBoundingClientRect();return {button:0,pointerId:1,clientX:rect.left+cx/canvas.width*rect.width,clientY:rect.top+cy/canvas.height*rect.height,preventDefault(){}};}
  const wrap=document.getElementById('initial-map-wrap');wrap.scrollLeft=20;wrap.scrollTop=40;sandbox.toggleInitialMapPan();
  sandbox.initialMapPointerDown(event(100,80));sandbox.initialMapPointerMove(event(90,70));sandbox.initialMapPointerUp(event(90,70));
  if(wrap.scrollLeft<=20||wrap.scrollTop<=40)throw new Error('Pan mode did not scroll zoomed map');
  if(document.getElementById('initial-map-pose').textContent!=='尚未选择位置与朝向。')throw new Error('Panning accidentally selected pose');
  sandbox.toggleInitialMapPan();
  sandbox.initialMapPointerDown(event(100,80));sandbox.initialMapPointerUp(event(100,80));
  document.getElementById('manual-reloc-confirmed').checked=true;sandbox.syncInitializationButtons();await sandbox.submitManualRelocation();
  if(!document.getElementById('manual-reloc-start').disabled)throw new Error('Directionless click enabled manual pose');
  if(requests.filter(request=>request.method==='POST').length!==1)throw new Error('Click without heading submitted pose');
  sandbox.initialMapPointerDown(event(100,80));sandbox.initialMapPointerMove(event(140,80));sandbox.initialMapPointerUp(event(140,80));
  if(!mapEllipses.some(args=>args[0]===100&&args[1]===80&&args[2]===4&&args[3]===4))throw new Error('Fixed 1 m prior ring not rendered');
  if(!document.getElementById('initial-map-pose').textContent.includes('粗定位先验')||!document.getElementById('initial-map-pose').textContent.includes('固定搜索半径 1.0 m'))throw new Error('Selection treated as an exact pose');
  await sandbox.submitManualRelocation();
  if(requests.filter(request=>request.method==='POST').length!==1)throw new Error('Unconfirmed pose submitted');
  document.getElementById('manual-reloc-confirmed').checked=true;sandbox.syncInitializationButtons();await sandbox.submitManualRelocation();await tick();
  const posts=requests.filter(request=>request.method==='POST');
  if(posts.length!==2||posts[0].path!=='/api/init/start'||posts[0].body.robot_ready!==true||posts[0].body.pin!=='test-pin')throw new Error('Initialization request mismatch');
  if(posts[1].path!=='/api/relocation/manual'||posts[1].body.map_fingerprint!==fingerprint||posts[1].body.confirmed_position!==true||posts[1].body.pin!=='test-pin')throw new Error('Manual pose request mismatch');
  if(Object.keys(posts[1].body).sort().join(',')!=='confirmed_position,map_fingerprint,pin,x,y,yaw')throw new Error('Client can override localization search bounds');
  near(posts[1].body.x,-10);near(posts[1].body.y,45);near(posts[1].body.yaw,Math.PI/2);
  if(!document.getElementById('manual-reloc-status').textContent.includes('重定位核验结果'))throw new Error('Final verified pose missing');
  if(!document.getElementById('manual-reloc-status').textContent.includes('位置修正 0.63 m')||!document.getElementById('manual-reloc-status').textContent.includes('朝向修正 17.2°')||!document.getElementById('manual-reloc-status').textContent.includes('不是定位精度')||!document.getElementById('manual-reloc-status').textContent.includes('现场墙体、障碍物'))throw new Error('Final result omitted coarse-prior correction or human verification');
  const wrapDifference=sandbox.manualPriorDifference({pose:{x:0,y:0,yaw:-Math.PI+.01},seed:{pose:{x:0,y:0,yaw:Math.PI-.01}}});
  if(!wrapDifference.includes('朝向修正 1.1°'))throw new Error('Prior heading delta ignored angle wrap');
  if(!document.getElementById('initialize-position').textContent.includes('人工重定位核验成功')||!document.getElementById('initialize-position').textContent.includes('非实时位姿'))throw new Error('Verified manual result/time semantics missing');
  await sandbox.loadInitialization();
  if(!document.getElementById('initialize-position').textContent.includes('人工重定位核验成功'))throw new Error('Stale initialization snapshot overwrote manual success');
  const verifiedSnapshot={...relocation},baseInit={...initialization};
  function restored(){return document.getElementById('initialize-position').textContent.includes('人工重定位核验成功');}
  // A new page's GETs can arrive in either order. The cached relocation result
  // is restored only after the matching initialization/map context is ready.
  sandbox.renderInitialization({state:'idle',stages:[],message:'新会话尚未初始化'});sandbox.renderManualRelocation(verifiedSnapshot);
  if(restored())throw new Error('A previous boot localization was reused before initialization');
  sandbox.renderInitialization(baseInit);if(!restored())throw new Error('Matching recent snapshot was not restored');
  for(const invalid of [
    {...verifiedSnapshot,finished_at:Date.now()/1000-301,started_at:Date.now()/1000-302},
    {...verifiedSnapshot,started_at:baseInit.started_at-2,finished_at:baseInit.started_at-1},
    {...verifiedSnapshot,seed:{...verifiedSnapshot.seed,map_fingerprint:'0'.repeat(64)}},
    {...verifiedSnapshot,finished_at:undefined},
    {...verifiedSnapshot,finished_at:Date.now()/1000+100},
    {...verifiedSnapshot,seed:{...verifiedSnapshot.seed,name:'旧起点'}},
    {...verifiedSnapshot,state:'failed',message:'最近核验失败'}
  ]){sandbox.renderManualRelocation(invalid);if(restored())throw new Error('Old/mismatched/unverified snapshot restored');}
  sandbox.renderInitialization({...baseInit,started_at:verifiedSnapshot.finished_at+1,finished_at:verifiedSnapshot.finished_at+2});sandbox.renderManualRelocation(verifiedSnapshot);
  if(restored())throw new Error('Cross-initialization snapshot was reused');
  sandbox.renderInitialization(baseInit);sandbox.renderManualRelocation(verifiedSnapshot);if(!restored())throw new Error('Recent same-session result did not recover');
  await sandbox.stopRobot();
  if(requests.some(request=>/speak|goal|enable|plans\/confirm/.test(request.path)))throw new Error('Initializer triggered automatic robot action');
  if(requests.filter(request=>request.method==='POST').length!==3)throw new Error('Polling caused mutation');
  console.log(JSON.stringify(requests));
}catch(error){console.error(error);process.exitCode=1;}});
""".replace("SCRIPT_VALUE", json.dumps(SCRIPT, ensure_ascii=True))
        result = subprocess.run([shutil.which("node"), "-e", harness], capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        requests = json.loads(result.stdout.strip().splitlines()[-1])
        mutations = [request for request in requests if request["method"] == "POST"]
        self.assertEqual([request["path"] for request in mutations], ["/api/init/start", "/api/relocation/manual", "/api/stop"])


if __name__ == "__main__":
    unittest.main()
