"""Owned web mapping sessions; stage, validate, review, then activate offline."""
import copy
import fcntl
import hashlib
import json
import math
import os
import re
import shutil
import shlex
import signal
import subprocess
import threading
import time
import uuid
from pathlib import Path

from guide_points import PROJECT
from guide_map import GuideMap

FILES = ('map/map.pgm', 'map/map.yaml',
         'G1Nav2D/src/fastlio2/PCD/map.pcd',
         'G1Nav2D/src/fastlio2/PCD/ground_map.pcd',
         'G1Nav2D/src/fastlio2/path/key_poses.txt')
BUSY = ('starting', 'mapping', 'saving', 'activating', 'stopping')


def session_running(project):
    """A worker holds this lock until its ROS launch has actually exited."""
    path = Path(project)/'run/mapping/session.lock'
    if not path.exists():
        return False
    with path.open('a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
    return False


def atomic_json(path, data):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, allow_nan=False), encoding='utf-8')
    os.replace(str(temporary), str(path))


def validate_session(directory):
    """Bounded structural verification, not a claim of geometric map accuracy."""
    directory = Path(directory).resolve()
    hashes = {}
    for name in FILES:
        path = directory / name
        if path.is_symlink() or path.resolve().parent != path.parent.resolve():
            raise ValueError('地图文件链接需人工处理')
        path.resolve().relative_to(directory)
        if not path.is_file() or not 0 < path.stat().st_size <= 512 * 1024 * 1024:
            raise ValueError('地图产物缺失、为空或过大：'+name)
        if name.endswith('.pcd'):
            with path.open('rb') as handle:
                header = handle.read(4096)
            match = re.search(rb'(?m)^POINTS\s+(\d+)\s*$', header)
            if not match or int(match.group(1)) <= 0 or b'\nDATA ' not in header:
                raise ValueError('PCD 头无效：'+name)
        if name.endswith('key_poses.txt'):
            with path.open() as handle:
                first = handle.readline(4096).split()
            if len(first) != 8:
                raise ValueError('关键位姿格式无效')
            if not all(math.isfinite(float(value)) for value in first):
                raise ValueError('关键位姿数值无效')
        digest = hashlib.sha256()
        with path.open('rb') as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b''):
                digest.update(block)
        hashes[name] = digest.hexdigest()
    metadata = GuideMap(project=directory).metadata()
    return {'files': hashes, 'map_fingerprint': metadata['map_fingerprint'],
            'metadata': metadata, 'geometric_review_required': True}


class MappingManager:
    def __init__(self, project=PROJECT, idle=lambda: True, launcher=None, validator=validate_session,
                 offline=None):
        self.project = Path(project).resolve()
        self.idle = idle
        self.launcher = launcher or self._launch
        self.validator = validator
        self.offline = offline or self._offline
        self.lock = threading.RLock()
        self.state = {'state': 'idle', 'message': '新场地：先建图；已有地图可直接初始化',
                      'automatic_motion_enabled': False}
        self.process = None
        self.thread = None
        self.directory = None
        self.cancelled = threading.Event()
        if session_running(self.project):
            try:
                session = json.loads((self.project/'run/mapping/current.json').read_text())['session_id']
                if not re.fullmatch('[0-9a-f]{32}', session):
                    raise ValueError('invalid session')
                self.directory = self.project/'run/mapping'/session
                self.state.update(state='stopping', session_id=session,
                    message='上次建图仍在运行；请取消并等待退出，不自动恢复任务')
                self.thread = threading.Thread(target=self._recover, daemon=True)
                self.thread.start()
            except (OSError, ValueError, KeyError):
                self.state.update(state='stopping', message='存在未识别的建图会话，请检查服务；不启动新任务')

    def _recover(self):
        while session_running(self.project):
            time.sleep(.2)
        with self.lock:
            self.state.update(state='cancelled', message='上次会话已退出；隔离产物保留，不自动激活')

    def active(self):
        with self.lock:
            return self.state['state'] in BUSY or session_running(self.project)

    def snapshot(self):
        with self.lock:
            if self.directory and (self.directory/'status.json').is_file() and self.state['state'] in ('starting', 'mapping', 'saving'):
                try:
                    event = json.loads((self.directory/'status.json').read_text())
                    order = {'starting': 0, 'mapping': 1, 'saving': 2}
                    if event.get('state') in order and order[event['state']] >= order[self.state['state']]:
                        self.state.update(state=event['state'], message=event['message'])
                except (OSError, ValueError, KeyError):
                    pass
            return copy.deepcopy(self.state)

    def start(self, payload):
        if set(payload) != {'robot_ready', 'manual_only'} or any(value is not True for value in payload.values()):
            raise ValueError('请确认落地、官方运动模式、拆绳、停稳与遥控监护；建图仅人工遥控')
        with self.lock:
            if self.active() or self.state['state'] == 'review' or not self.idle():
                raise RuntimeError('已有任务、初始化、重定位或待审核地图；不能重复建图')
            self.cancelled.clear()
            session = uuid.uuid4().hex
            directory = self.project/'run/mapping'/session
            directory.mkdir(parents=True, mode=0o700)
            self.directory = directory
            atomic_json(self.project/'run/mapping/current.json', {'session_id': session})
            self.state = {'state': 'starting', 'session_id': session, 'message': '禁用自动运动并准备独立建图会话',
                          'automatic_motion_enabled': False, 'started_at': time.time()}
            self.thread = threading.Thread(target=self._worker, daemon=True, name='web-mapping')
            self.thread.start()
            return self.snapshot()

    def finish(self, payload):
        with self.lock:
            if (set(payload) != {'session_id', 'stationary'} or payload.get('session_id') != self.state.get('session_id') or
                    payload.get('stationary') is not True or self.state['state'] != 'mapping'):
                raise ValueError('请确认本次建图已走完且停稳，再保存')
            atomic_json(self.directory/'finish.request', {'stationary': True})
            self.state.update(state='saving', message='保存到隔离目录，尚未替换当前地图')
            return self.snapshot()

    def cancel(self):
        with self.lock:
            if self.state['state'] not in ('starting', 'mapping', 'saving', 'review', 'stopping'):
                return False
            self.cancelled.set()
            if self.directory:
                atomic_json(self.directory/'cancel.request', {'cancel': True})
            running = self.process is not None or session_running(self.project) or (self.thread and self.thread.is_alive())
            self.state.update(state='stopping' if running else 'cancelled',
                              message='取消建图，保留当前地图和隔离产物')
            return True

    def preview(self):
        with self.lock:
            if self.state['state'] != 'review':
                raise ValueError('暂无待审核的新地图')
            return GuideMap(project=self.directory).image_png()

    def activate(self, payload):
        with self.lock:
            if (self.state['state'] != 'review' or set(payload) != {'session_id', 'map_reviewed'} or
                    payload.get('session_id') != self.state.get('session_id') or payload.get('map_reviewed') is not True):
                raise ValueError('请先预览并人工核对本次新地图')
            if not self.idle():
                raise RuntimeError('存在活动任务，不能切换地图')
            self.offline()
            checked = self.validator(self.directory)
            if checked['files'] != self.state['validation']['files']:
                raise ValueError('待审核文件发生变化，拒绝激活')
            backup = self.project/'run/backups'/('mapping_'+self.state['session_id']+'_'+uuid.uuid4().hex[:8])
            backup.mkdir(parents=True, mode=0o700)
            self.state.update(state='activating', message='离线备份与切换地图')
            changed = []
            try:
                # Preserve editing metadata tied to the old map, not just PGM/PCD.
                targets = list(FILES)+['map/map.json', 'map/map_region.json', 'config/guide_points.json']
                for name in targets:
                    target = self.project/name
                    if target.is_symlink():
                        raise ValueError('当前地图文件是链接，不自动替换')
                    target.resolve().relative_to(self.project)
                    if target.exists():
                        stored = backup/name
                        stored.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(str(target), str(stored))
                for name in FILES:
                    target = self.project/name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    temporary = target.with_name(target.name+'.mapping-'+self.state['session_id'])
                    shutil.copy2(str(self.directory/name), str(temporary))
                    os.replace(str(temporary), str(target))
                    changed.append(name)
                # A new map has a new coordinate system. Keep the old registry
                # in the backup and its photos/documents in place, but do not
                # make those points navigable on the new map.
                for name in ('map/map.json', 'map/map_region.json', 'config/guide_points.json'):
                    target = self.project/name
                    if target.exists():
                        target.unlink()  # Old editing metadata is preserved in the exact backup above.
                        changed.append(name)
                atomic_json(self.project/'map/active_version.json', {
                    'session_id': self.state['session_id'], **checked, 'activated_at': time.time()})
            except Exception:
                for name in reversed(changed):
                    target, original = self.project/name, backup/name
                    if original.exists():
                        shutil.copy2(str(original), str(target))
                    elif target.exists():
                        target.unlink()
                self.state.update(state='review', message='切换失败，旧文件已回滚；请查看错误')
                raise
            self.state.update(state='succeeded', message='新地图已激活；旧图和点位登记已备份，照片/文献保留。请初始化/重新定位并登记新图点位',
                              backup=str(backup.relative_to(self.project)), validation=checked)
            return self.snapshot()

    def _launch(self, directory):
        import shlex
        command = 'source '+shlex.quote(str(self.project/'scripts/env.sh'))
        command += ' && set +u && source "$ROS_SETUP" && source "$NAV_WS/devel/setup.bash"'
        command += ' && exec /usr/bin/python3 -u '+shlex.quote(str(self.project/'scripts/web_mapping_worker.py'))
        command += ' --directory '+shlex.quote(str(directory))
        log = (directory/'worker.log').open('ab')
        try:
            return subprocess.Popen(['bash', '-lc', command], cwd=str(self.project), stdin=subprocess.DEVNULL,
                                    stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        finally:
            log.close()

    def _worker(self):
        try:
            process = self.launcher(self.directory)
            with self.lock:
                self.process = process
            deadline = time.monotonic()+3600
            while process.poll() is None:
                if time.monotonic() > deadline:
                    self.cancel()
                    deadline = time.monotonic()+60
                if self.cancelled.wait(.15) and (self.directory/'cancel.request').exists():
                    # The owned worker observes this request and reaps its launch.
                    time.sleep(.15)
            with self.lock:
                if self.cancelled.is_set():
                    self.state.update(state='cancelled', message='建图已取消，当前地图未改变')
                elif process.returncode != 0:
                    self.state.update(state='failed', message='建图或保存失败；当前地图未改变，查看本会话 worker.log')
                else:
                    checked = self.validator(self.directory)
                    self.state.update(state='review', message='产物结构检查通过；请预览核对后激活，不代表几何质量验收', validation=checked)
        except Exception as exc:
            with self.lock:
                self.state.update(state='failed', message=str(exc))
        finally:
            with self.lock:
                self.process = None

    def _offline(self):
        from initialize_robot_services import find_services, validate_inventory
        services = find_services(self.project, os.environ.get('CONTROL_IFACE', 'eth0'))
        validate_inventory(services)
        if services['navigation'] or services['safe_controller']:
            raise RuntimeError('导航或控制器仍在运行，不能激活地图')
        # Separate ROS helper rejects foreign ROS publishers as well.
        result = subprocess.run(['bash', '-lc', 'source /opt/ros/noetic/setup.bash && /usr/bin/python3 '+
            shlex.quote(str(self.project/'scripts/web_mapping_worker.py'))+' --offline-check'],
            capture_output=True, timeout=8)
        if result.returncode:
            raise RuntimeError('ROS 导航/定位/建图节点未完全退出，不能切换地图')
