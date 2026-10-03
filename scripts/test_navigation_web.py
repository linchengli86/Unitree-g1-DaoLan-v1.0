#!/usr/bin/env python3
"""Isolated HTTP/browser coverage for motion-only named-point scheduling."""

import hashlib
import json
import os
import shutil
import subprocess
import time
import unittest
from unittest.mock import Mock, patch

import test_guide_points as point_tests
from guide_web import SCRIPT, render_page


class NavigationWebTests(unittest.TestCase):
    request = point_tests.WebPointTests.request

    def setUp(self):
        self.environment = patch.dict(os.environ, {
            "GUIDE_NAMED_NAV_VERIFIED": "0", "GUIDE_MOTION_ENABLED": "0", "GUIDE_ARM_ENABLED": "0"})
        self.environment.start()
        try:
            point_tests.WebPointTests.setUp(self)
        except Exception:
            self.environment.stop()
            raise
        self.pose = Mock(side_effect=point_tests.capture)
        self.server.points.pose_provider = self.pose
        self.point = self.server.points.add({"name": "音圈展点", "speech": ""})
        self.pose.reset_mock()
        self.original = self.digest()
        self.tasks.snapshot.return_value = None
        self.tasks.start.return_value = {"id": "1" * 32, "state": "running", "dry_run": False}
        self.server.omni_ready = Mock(return_value=False)
        self.server.query_board = Mock(side_effect=AssertionError("motion-only called Omni"))

    def tearDown(self):
        try:
            point_tests.WebPointTests.tearDown(self)
        finally:
            self.environment.stop()

    def digest(self):
        return hashlib.sha256(self.server.points.path.read_bytes()).hexdigest()

    def assert_no_robot_or_speech(self):
        self.assertEqual(self.digest(), self.original)
        self.pose.assert_not_called()
        self.speech.enqueue.assert_not_called()
        self.speech.speak_and_wait.assert_not_called()
        self.server.query_board.assert_not_called()

    def prepare(self):
        status, result = self.request("/api/navigation/prepare", {"point_id": self.point["id"]})
        self.assertEqual(status, 200, result)
        return result

    def test_prepare_is_registry_anchored_single_step_without_omni_or_speech(self):
        result = self.prepare()
        self.assertIs(result["navigation_only"], True)
        self.assertTrue(result["requires_pin"])
        self.assertEqual(result["plan"]["steps"], [{"action": "navigate_to_point",
            "parameters": {"point_id": self.point["id"]}, "timeout": 180}])
        self.assertIn(self.point["name"], result["plan"]["title"])
        self.tasks.start.assert_not_called()
        self.assertTrue(result["warnings"])
        self.assert_no_robot_or_speech()

    def test_rejects_unknown_ids_coordinates_speed_modes_and_speech(self):
        invalid = [{}, {"point_id": None}, {"point_id": True}, {"point_id": "c" * 32},
                   {"point_id": "x; command"}, {"point_id": [self.point["id"]]}]
        invalid += [{"point_id": self.point["id"], key: value} for key, value in (
            ("x", 1), ("y", 2), ("yaw", 0), ("speed", 2), ("max_vx", 1.5),
            ("timeout", 900), ("commissioning_confirmed", True), ("speech", "say this"),
            ("present_after_arrival", True), ("plan", {}))]
        for payload in invalid:
            with self.subTest(payload=payload):
                self.assertEqual(self.request("/api/navigation/prepare", payload)[0], 400)
        self.assertFalse(self.server.pending)
        self.tasks.start.assert_not_called()
        self.assert_no_robot_or_speech()

    def test_active_tasks_initialization_or_relocation_block_preparation(self):
        for manager in (self.tasks, self.server.initialization, self.server.relocation):
            with patch.object(manager, "active", return_value=True):
                self.assertEqual(self.request("/api/navigation/prepare", {"point_id": self.point["id"]})[0], 503)
        self.assertFalse(self.server.pending)
        self.tasks.start.assert_not_called()
        self.assert_no_robot_or_speech()

    def test_confirmation_requires_motion_gate_pin_and_named_gate(self):
        result = self.prepare()
        token = result["confirmation"]
        payload = {"token": token, "pin": "246810"}
        self.assertEqual(self.request("/api/plans/confirm", payload)[0], 503)
        self.server.motion_enabled = True
        self.assertEqual(self.request("/api/plans/confirm", {**payload, "pin": "bad"})[0], 400)
        self.assertEqual(self.request("/api/plans/confirm", payload)[0], 503)
        self.tasks.start.assert_not_called()
        self.assert_no_robot_or_speech()

    def test_explicit_pin_confirmation_only_submits_named_navigation_once(self):
        with patch.dict(os.environ, {"GUIDE_NAMED_NAV_VERIFIED": "1", "GUIDE_MOTION_ENABLED": "1"}):
            self.server.motion_enabled = True
            result = self.prepare()
            payload = {"token": result["confirmation"], "pin": "246810"}
            status, response = self.request("/api/plans/confirm", payload)
            self.assertEqual(status, 200, response)
            self.tasks.start.assert_called_once_with(result["plan"], dry_run=False)
            self.assertEqual(self.request("/api/plans/confirm", payload)[0], 400)
        self.assert_no_robot_or_speech()

    def test_gate_is_rechecked_after_preview(self):
        self.server.motion_enabled = True
        with patch.dict(os.environ, {"GUIDE_NAMED_NAV_VERIFIED": "1"}):
            result = self.prepare()
        self.assertEqual(self.request("/api/plans/confirm", {
            "token": result["confirmation"], "pin": "246810"})[0], 503)
        self.tasks.start.assert_not_called()
        self.assert_no_robot_or_speech()

    def test_simulation_remains_available_without_motion_authorization(self):
        result = self.prepare()
        status, response = self.request("/api/plans/dry-run", {"token": result["confirmation"]})
        self.assertEqual(status, 200, response)
        self.tasks.start.assert_called_once_with(result["plan"], dry_run=True)
        self.assert_no_robot_or_speech()

    def test_expired_and_superseded_confirmation_never_start(self):
        first = self.prepare()
        second = self.prepare()
        self.assertEqual(self.request("/api/plans/confirm", {"token": first["confirmation"], "pin": "246810"})[0], 400)
        plan, _ = self.server.pending[second["confirmation"]]
        self.server.pending[second["confirmation"]] = (plan, time.monotonic() - 1)
        self.assertEqual(self.request("/api/plans/confirm", {"token": second["confirmation"], "pin": "246810"})[0], 400)
        self.tasks.start.assert_not_called()
        self.assert_no_robot_or_speech()


class NavigationBrowserTests(unittest.TestCase):
    def test_navigation_view_explains_limits_and_independent_motion(self):
        page = render_page("/navigation")
        self.assertIn('data-view="navigation" aria-label="点位导航"', page)
        self.assertIn('id="navigation-ready"', page)
        self.assertIn('id="navigation-prepare" class="primary" disabled', page)
        self.assertIn('id="plan-panel" aria-label=', page)
        self.assertIn("0.60 m/s", page)
        self.assertIn("0.70 rad/s", page)
        self.assertIn("不依赖展板整理、Omni 或语音服务", page)

    @unittest.skipUnless(shutil.which("node"), "Node optional for isolated browser interaction")
    def test_read_only_selection_then_explicit_motion_plan_and_pin_confirmation(self):
        harness = r'''
const vm=require('vm'),elements=new Map(),requests=[],listeners={},timers=[];
class Element{
 constructor(id=''){this.id=id;this.children=[];this.dataset={};this.value='';this.files=[];this.checked=false;this.hidden=false;this.disabled=false;this.style={};this.attrs={};this._text='';}
 set textContent(v){this._text=String(v);this.children=[];}get textContent(){return this._text;}
 set innerHTML(v){throw new Error('untrusted HTML');}
 append(...nodes){this.children.push(...nodes);}appendChild(n){this.append(n);return n;}setAttribute(k,v){this.attrs[k]=String(v);}getAttribute(k){return this.attrs[k]||null;}removeAttribute(k){delete this.attrs[k];}addEventListener(){}focus(){}querySelectorAll(){return [];}
}
const keys=['home','initialize','map','navigation','assistant','agent','knowledge','tasks','points','point_new','localization','system'];
const views=keys.map(k=>{const e=new Element();e.dataset.view=k;return e;});
const location={pathname:'/',href:'http://localhost/'};
const document={body:new Element(),title:'',getElementById(id){if(!elements.has(id))elements.set(id,new Element(id));return elements.get(id);},createElement(){return new Element();},querySelectorAll(s){return s.includes('data-view')?views:[];},addEventListener(k,v){listeners[k]=v;}};document.body.dataset.page='home';
const point={id:'1'.repeat(32),name:'<script>展点</script>',speech:'',pose:{x:1,y:2,yaw:0}};
const plan={title:'前往：'+point.name,steps:[{action:'navigate_to_point',parameters:{point_id:point.id},timeout:180}]};
let pin='246810',promptCount=0,holds=false,release=null,currentTask=null;
const response=body=>({ok:true,json:async()=>body});
const task={id:'a'.repeat(32),state:'running',plan,steps:[{state:'running'}],message:'导航中'};
const sandbox={document,location,window:{addEventListener(){},scrollTo(){}},history:{pushState(_,__,p){location.pathname=p;}},URL,console,
 setTimeout(f,d){timers.push({f,d});return timers.length;},clearTimeout(){},setInterval(){return 1;},clearInterval(){},alert(){throw new Error('unexpected alert');},confirm(){throw new Error('unexpected confirm');},prompt(){promptCount++;return pin;},
 fetch:async(path,options={})=>{const method=options.method||'GET',payload=options.body?JSON.parse(options.body):null;requests.push({path,method,payload});
 if(method==='GET')return response({status:'success',message:'ready',motion_enabled:true,named_navigation_verified:true,task:currentTask,points:[point],requires_pin:true,seeds:[],relocation:{state:'idle',message:'idle'},arm_gestures:{}});
 if(path==='/api/navigation/prepare'){if(holds)return new Promise(resolve=>{release=()=>resolve(response({status:'success',confirmation:'b'.repeat(32),plan,requires_pin:true,navigation_only:true,message:'待确认'}));});return response({status:'success',confirmation:'b'.repeat(32),plan,requires_pin:true,navigation_only:true,message:'待确认'});}
 if(path==='/api/plans/confirm'){currentTask=task;return response({status:'success',task,message:'已提交'});}
 throw new Error('unexpected API '+path);}
};
vm.runInNewContext(SCRIPT_VALUE,sandbox,{timeout:1000});
const tick=()=>new Promise(r=>setImmediate(r)),assert=(a,m)=>{if(!a)throw new Error(m);},posts=()=>requests.filter(r=>r.method==='POST');
setImmediate(async()=>{try{
 await tick();sandbox.selectNavigationPoint(point.id);await tick();
 assert(document.body.dataset.page==='navigation'&&!document.getElementById('plan-panel').hidden,'navigation view missing');
 assert(posts().length===0&&promptCount===0,'selection performed mutation');
 assert(document.getElementById('navigation-prepare').disabled,'missing readiness gate');
 await sandbox.preparePointNavigation();assert(posts().length===0,'unchecked readiness allowed plan');
 document.getElementById('navigation-ready').checked=true;sandbox.syncNavigationButtons();
 holds=true;const pending=sandbox.preparePointNavigation();await tick();await sandbox.preparePointNavigation();
 assert(posts().length===1,'duplicate preparation');release();await pending;holds=false;
 assert(posts()[0].path==='/api/navigation/prepare'&&Object.keys(posts()[0].payload).join()==='point_id','wrong motion-only API');
 assert(posts().length===1&&promptCount===0,'preview auto-executed');
 const box=document.getElementById('confirm'),controls=box.children[box.children.length-1],execute=controls.children.find(e=>e.textContent==='确认前往（仅移动）');
 assert(execute,'explicit confirmation missing');pin=null;await execute.onclick();assert(posts().length===1,'cancelled PIN submitted');
 pin='246810';await execute.onclick();await tick();assert(posts().length===2&&posts()[1].path==='/api/plans/confirm','not confirmed via scheduler');
 assert(posts()[1].payload.pin==='246810','PIN not passed');
 assert(document.getElementById('navigation-prepare').disabled,'active task did not lock button');
 currentTask={...task,state:'succeeded',steps:[{state:'succeeded',result:{verified:true,motion_disabled:true,position_error_m:.077,yaw_error_rad:.034}}]};sandbox.renderTask(currentTask);
 const rendered=document.getElementById('task').children[1].children[0].children[0].textContent;
 assert(rendered.includes('7.7 cm')&&rendered.includes('运动已禁用'),'arrival result missing');
 assert(!requests.some(r=>/omni|speak|agent\/(prepare|prefetch)/.test(r.path)),'motion invoked speech or Omni');
 document.getElementById('navigation-ready').checked=true;await sandbox.preparePointNavigation();
 sandbox.navigationPointChanged();assert(!box.children.length&&!document.getElementById('navigation-ready').checked,'changed selection retained confirmation');
 console.log(JSON.stringify({explicit_posts:posts().length,selection_read_only:true,motion_only:true}));
}catch(e){console.error(e);process.exitCode=1;}});
'''.replace("SCRIPT_VALUE", json.dumps(SCRIPT, ensure_ascii=True))
        completed = subprocess.run([shutil.which("node"), "-e", harness], capture_output=True, text=True, timeout=5)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue(json.loads(completed.stdout.strip().splitlines()[-1])["motion_only"])


if __name__ == "__main__":
    unittest.main()
