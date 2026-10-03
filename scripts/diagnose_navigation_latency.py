#!/usr/bin/env python3
"""Bounded, passive ROS navigation tracing; never requests motion or planning.

Source/header age, this process's independent TF cache and move_base's reported
pose age are separate observations. ACTIVE and velocity messages do not prove
that the robot physically moved. Output is JSON on stdout; no files are created.
"""

import argparse
from collections import deque
import json
import math
import re
import struct
import threading
import time

from navigation_thread_budget import apply_current_process


ACTIVE = {0, 1, 6, 7}
TERMINAL = {2, 3, 4, 5, 8, 9}
POSE_WARNING = re.compile(
    r"Current time:\s*([\d.eE+-]+).*?global_pose stamp:\s*([\d.eE+-]+).*?tolerance:\s*([\d.eE+-]+)",
    re.IGNORECASE)


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


class BoundedStats:
    def __init__(self, limit=4096):
        if type(limit) is not int or limit < 1:
            raise ValueError("sample limit must be positive")
        self.values = deque(maxlen=limit)
        self.count = self.rejected = 0
        self.minimum = self.maximum = None

    def add(self, value):
        if not finite(value):
            self.rejected += 1
            return False
        self.values.append(float(value))
        self.count += 1
        self.minimum = value if self.minimum is None else min(self.minimum, value)
        self.maximum = value if self.maximum is None else max(self.maximum, value)
        return True

    def summary(self):
        ordered = sorted(self.values)

        def percentile(fraction):
            if not ordered:
                return None
            position = fraction * (len(ordered) - 1)
            low = int(position)
            weight = position - low
            return ordered[low] * (1 - weight) + ordered[min(low + 1, len(ordered) - 1)] * weight

        return {"count": self.count, "retained": len(ordered), "rejected": self.rejected,
                "p50": percentile(.50), "p95": percentile(.95),
                "min": self.minimum, "max": self.maximum,
                "abs_max": None if self.minimum is None else max(abs(self.minimum), abs(self.maximum)),
                "percentiles_use": "all_samples" if self.count == len(ordered) else "recent_retained_samples"}


class TopicSeries:
    def __init__(self, limit=4096):
        self.count = self.rejected = 0
        self.last = None
        self.age, self.gap = BoundedStats(limit), BoundedStats(limit)
        self.vx, self.wz = BoundedStats(limit), BoundedStats(limit)
        self.callers = set()

    def add(self, received, age=None, vx=None, wz=None, caller=""):
        if not finite(received) or any(value is not None and not finite(value) for value in (age, vx, wz)):
            self.rejected += 1
            return False
        if self.last is not None and received < self.last:
            self.rejected += 1
            return False
        if self.last is not None:
            self.gap.add(received - self.last)
        self.last = received
        self.count += 1
        for stats, value in ((self.age, age), (self.vx, vx), (self.wz, wz)):
            if value is not None:
                stats.add(value)
        if caller and len(self.callers) < 16:
            self.callers.add(str(caller)[:160])
        return True

    def summary(self):
        return {"received": self.count, "rejected": self.rejected, "callerids": sorted(self.callers),
                "header_age_seconds": self.age.summary(), "receipt_gap_seconds": self.gap.summary(),
                "vx_m_per_s": self.vx.summary(), "wz_rad_per_s": self.wz.summary()}


class SampleTrace:
    """Finite receipt/header metadata only; no ROS messages or cloud payloads."""

    def __init__(self, limit):
        self.samples = deque(maxlen=limit)
        self.preflight_snapshot = deque(maxlen=limit)
        self.total_received = self.rejected = self.capacity_dropped = self.window_evicted = 0
        self.last_received = None

    def freeze_preflight(self, cutoff):
        self.preflight_snapshot.extend(
            sample for sample in self.samples if sample["receipt_monotonic_seconds"] >= cutoff)
        self.samples.clear()

    def add(self, sample, cutoff):
        received = sample["receipt_monotonic_seconds"]
        if self.last_received is not None and received < self.last_received:
            self.rejected += 1
            return False
        while self.samples and self.samples[0]["receipt_monotonic_seconds"] < cutoff:
            self.samples.popleft()
            self.window_evicted += 1
        if len(self.samples) == self.samples.maxlen:
            self.capacity_dropped += 1
        self.samples.append(sample)
        self.total_received += 1
        self.last_received = received
        return True


def decode_header_stamp(payload):
    """Read only the leading std_msgs/Header from an AnyMsg byte buffer."""
    if not isinstance(payload, (bytes, bytearray, memoryview)) or len(payload) < 16:
        raise ValueError("truncated ROS header")
    _, seconds, nanoseconds, frame_length = struct.unpack_from("<IIII", payload, 0)
    if nanoseconds >= 1000000000 or frame_length > 4096 or len(payload) < 16 + frame_length:
        raise ValueError("invalid ROS header")
    return seconds + nanoseconds / 1000000000.0


def decode_livox_timing(payload):
    """CustomMsg header + timebase + first/last offsets, O(1), no point decoding.

    ROS1 CustomPoint is packed <IfffBBB (19 bytes). The raw last point may
    differ from the LIO-filtered last point used to timestamp its TF/odometry.
    """
    begin = decode_header_stamp(payload)
    frame_length = struct.unpack_from("<I", payload, 12)[0]
    fixed = 16 + frame_length
    if len(payload) < fixed + 20:
        raise ValueError("truncated Livox CustomMsg metadata")
    timebase, point_num = struct.unpack_from("<QI", payload, fixed)
    array_length = struct.unpack_from("<I", payload, fixed + 16)[0]
    points = fixed + 20
    if point_num != array_length or len(payload) != points + array_length * 19:
        raise ValueError("Livox point count/payload length mismatch")
    if array_length == 0:
        return begin, None, 0
    first_offset = struct.unpack_from("<I", payload, points)[0]
    last_offset = struct.unpack_from("<I", payload, points + (array_length - 1) * 19)[0]
    if last_offset < first_offset:
        raise ValueError("Livox final point precedes first point")
    return begin, (timebase + last_offset) / 1000000000.0, array_length


class PassiveRecorder:
    def __init__(self, started, max_streams=80, sample_limit=4096):
        if not finite(started) or started < 0:
            raise ValueError("invalid monotonic start")
        self.lock = threading.RLock()
        self.started, self.origin, self.duration = started, None, None
        self.max_streams, self.sample_limit = max_streams, sample_limit
        self.preflight_window = 20.0
        self.topics = {}
        self.source_notes = {}
        self.logs, self.status_events = deque(maxlen=1024), deque(maxlen=512)
        self.controller_milestones = {}
        self.observed_goals = set()
        self.nav_attempt = self.raw_command = self.smooth_command = self.active = False
        self.terminal_at = None
        self.dropped_streams = self.invalid_records = 0

    def begin(self, received, duration):
        if not finite(received) or received < self.started or not finite(duration) or not 0 < duration <= 180:
            raise ValueError("invalid capture interval")
        with self.lock:
            if self.origin is not None:
                raise ValueError("capture already started")
            self.origin, self.duration = received, duration
            cutoff = max(self.started, received - self.preflight_window)
            for trace in self.topics.values():
                trace.freeze_preflight(cutoff)

    def expect_topics(self, topics):
        with self.lock:
            for topic in topics:
                if topic not in self.topics and len(self.topics) < self.max_streams:
                    self.topics[topic] = SampleTrace(self.sample_limit)

    def record(self, topic, received, wall, stamp=None, vx=None, wz=None, caller=""):
        with self.lock:
            if not finite(received) or not finite(wall) or any(
                    item is not None and not finite(item) for item in (stamp, vx, wz)):
                self.invalid_records += 1
                return False
            # Waiting for ACTIVE must not erase evidence of failed preflight.
            if received < self.started or (self.origin is not None and received >= self.origin + self.duration):
                return False
            if not isinstance(topic, str) or not topic or len(topic) > 400:
                self.invalid_records += 1
                return False
            if topic not in self.topics:
                if len(self.topics) >= self.max_streams:
                    self.dropped_streams += 1
                    return False
                self.topics[topic] = SampleTrace(self.sample_limit)
            age = None if stamp is None else wall - stamp
            if age is not None and not finite(age):
                self.invalid_records += 1
                return False
            cutoff = max(self.started, (received if self.origin is None else self.origin) - self.preflight_window)
            sample = {"receipt_monotonic_seconds": received, "receipt_wall_seconds": wall,
                      "header_stamp_seconds": stamp, "header_age_seconds": age,
                      "vx_m_per_s": vx, "wz_rad_per_s": wz, "callerid": str(caller)[:160]}
            if not self.topics[topic].add(sample, cutoff):
                return False
            if topic in {"/cmd_vel", "/cmd_vel_smooth"} and any(
                    value is not None and abs(value) > 1e-6 for value in (vx, wz)):
                if topic == "/cmd_vel":
                    self.raw_command = True
                else:
                    self.smooth_command = True
            return True

    def status(self, states, received):
        with self.lock:
            if not finite(received) or received < self.started:
                self.invalid_records += 1
                return
            valid = [(str(goal)[:160], state) for goal, state in states
                     if isinstance(goal, str) and type(state) is int and 0 <= state <= 9]
            self.active = any(state in ACTIVE for _, state in valid)
            for goal, state in valid[:64]:
                if state in ACTIVE:
                    self.nav_attempt = True
                    if goal and len(self.observed_goals) < 64:
                        self.observed_goals.add(goal)
                elif goal in self.observed_goals and state in TERMINAL and self.terminal_at is None:
                    self.terminal_at = received
            if self.active:
                self.terminal_at = None
            self.status_events.append({"elapsed_seconds": received - self.started,
                                       "active": self.active, "states": valid[:64]})

    def warning(self, name, level, message, received, stamp=None, wall=None):
        if not finite(received) or received < self.started or type(level) is not int:
            return
        basename = str(name).strip("/").split("/")[-1]
        if not ((basename == "move_base" and level >= 4) or basename.startswith("unitree_safe_controller")
                or basename == "diagnostic_tf_listener"):
            return
        text = str(message)[:1000]
        item = {"elapsed_seconds": received - self.started, "node": str(name)[:160],
                "level": level, "message": text}
        if finite(wall) and finite(stamp) and finite(wall - stamp):
            item["log_header_age_seconds"] = wall - stamp
        match = POSE_WARNING.search(text)
        if match:
            current, pose, tolerance = map(float, match.groups())
            if all(finite(value) for value in (current, pose, tolerance)) and finite(current - pose):
                item["move_base_reported_pose_age_seconds"] = current - pose
                item["move_base_transform_tolerance_seconds"] = tolerance
        with self.lock:
            self.logs.append(item)
            if basename.startswith("unitree_safe_controller"):
                lower = text.lower()
                if "motion enabled" in lower:
                    self.controller_milestones.setdefault("first_enable", item)
                if "initializing" in lower or "ready but disarmed" in lower:
                    self.controller_milestones.setdefault("first_start", item)
                if "stopmove" in lower or "motion disabled" in lower:
                    self.controller_milestones.setdefault("first_stop", item)
                    self.controller_milestones["latest_stop"] = item

    def observation(self):
        with self.lock:
            return self.nav_attempt, self.terminal_at

    def stop_reason(self, received, waiting_deadline):
        with self.lock:
            if self.origin is None:
                return "wait_for_goal_timeout" if received >= waiting_deadline else None
            if received >= self.origin + self.duration:
                return "duration_elapsed"
            if self.terminal_at is not None and received >= self.terminal_at + 8:
                return "goal_terminal_plus_8_seconds"
            return None

    def summary(self, ended, reason):
        with self.lock:
            elapsed = 0 if self.origin is None else max(0, min(self.duration, ended - self.origin))
            cutoff = max(self.started, (ended if self.origin is None else self.origin) - self.preflight_window)
            until = ended if self.origin is None else min(ended, self.origin + self.duration)
            topics, buckets = {}, {}
            retained_raw = retained_smooth = False
            for topic, trace in sorted(self.topics.items()):
                selected = [sample for sample in (*trace.preflight_snapshot, *trace.samples)
                            if cutoff <= sample["receipt_monotonic_seconds"] <= until]
                series = TopicSeries(self.sample_limit * 2)
                preflight, captured = TopicSeries(self.sample_limit), TopicSeries(self.sample_limit)
                for sample in selected:
                    received, age = sample["receipt_monotonic_seconds"], sample["header_age_seconds"]
                    vx, wz, owner = sample["vx_m_per_s"], sample["wz_rad_per_s"], sample["callerid"]
                    series.add(received, age, vx, wz, owner)
                    phase = preflight if self.origin is None or received < self.origin else captured
                    phase.add(received, age, vx, wz, owner)
                    index = int((received - self.started) / 3.0)
                    bucket = buckets.setdefault(index, {})
                    bucket.setdefault(topic, TopicSeries(512)).add(received, age, vx, wz, owner)
                    nonzero = any(value is not None and abs(value) > 1e-6 for value in (vx, wz))
                    retained_raw |= topic == "/cmd_vel" and nonzero
                    retained_smooth |= topic == "/cmd_vel_smooth" and nonzero
                report = series.summary()

                def receipt_rows(rows):
                    return [{"elapsed_seconds": sample["receipt_monotonic_seconds"] - self.started,
                             **{key: value for key, value in sample.items() if key != "receipt_monotonic_seconds"}}
                            for sample in rows[-64:]]

                report.update(process_received_total=trace.total_received,
                              process_rejected_total=trace.rejected,
                              retained_window_received=len(selected),
                              samples_dropped_by_capacity_total=trace.capacity_dropped,
                              samples_evicted_by_preflight_window_total=trace.window_evicted,
                              ever_received=trace.last_received is not None,
                              tail_silence_seconds=max(0, ended - (
                                  self.started if trace.last_received is None else trace.last_received)),
                              statistics_scope="retained_trace_samples_in_selected_window",
                              preflight_statistics=preflight.summary(),
                              capture_statistics=captured.summary(),
                              preflight_header_receipt_samples=receipt_rows([
                                  sample for sample in selected if self.origin is None
                                  or sample["receipt_monotonic_seconds"] < self.origin]),
                              capture_header_receipt_samples=receipt_rows([
                                  sample for sample in selected if self.origin is not None
                                  and sample["receipt_monotonic_seconds"] >= self.origin]),
                              recent_header_receipt_samples=receipt_rows(selected))
                topics[topic] = report
            return {"status": "complete", "reason": reason, "capture_seconds": elapsed,
                    "observational_only": True, "navigation_attempt_observed": self.nav_attempt,
                    "motion_command_observed": self.raw_command or self.smooth_command,
                    "raw_nonzero_command_observed": self.raw_command,
                    "smooth_nonzero_command_observed": self.smooth_command,
                    "physical_motion_verified": False, "navigation_active_at_end": self.active,
                    "motion_command_observed_in_retained_window": retained_raw or retained_smooth,
                    "preflight_window_seconds": self.preflight_window,
                    "trace_origin_monotonic_seconds": self.started,
                    "capture_start_offset_seconds": None if self.origin is None else self.origin - self.started,
                    "retained_window_start_offset_seconds": cutoff - self.started,
                    "retained_window_end_offset_seconds": until - self.started,
                    "elapsed_since_start_seconds": ended - self.started,
                    "source_notes": self.source_notes,
                    "motion_flags_scope": "any_observed_since_process_start; retained_window_flag_is_separate",
                    "event_scope": "bounded_recent_process_events_plus_first_controller_milestones",
                    "age_basis": "system_wall_time_minus_ROS_header_stamp; negative age can indicate clock skew",
                    "tf_common_scope": "independent_diagnostic_listener_not_move_base_internal_cache",
                    "invalid_records": self.invalid_records, "dropped_new_streams": self.dropped_streams,
                    "topics": topics,
                    "three_second_buckets": [
                        {"start_seconds": index * 3, "end_seconds": min((index + 1) * 3, until - self.started),
                         "retained_from_seconds": max(index * 3, cutoff - self.started),
                         "topics": {key: value.summary() for key, value in sorted(bucket.items())}}
                        for index, bucket in sorted(buckets.items())],
                    "status_events": list(self.status_events), "relevant_logs": list(self.logs),
                    "controller_milestones": self.controller_milestones}


def emit(packet):
    print(json.dumps(packet, ensure_ascii=False, allow_nan=False), flush=True)


def run_ros(duration, wait_for_goal):
    # Lazy imports keep statistics/tests usable without ROS or robot access.
    apply_current_process()  # An observer must not oversubscribe BLAS workers.
    import rospy
    import tf
    from geometry_msgs.msg import Twist
    from nav_msgs.msg import Odometry
    from sensor_msgs.msg import LaserScan, Imu
    from actionlib_msgs.msg import GoalStatusArray
    from rosgraph_msgs.msg import Log
    from tf2_msgs.msg import TFMessage

    rospy.init_node("passive_navigation_latency", anonymous=True, disable_signals=True)
    listener = tf.TransformListener()
    recorder = PassiveRecorder(time.monotonic())
    recorder.expect_topics(["/tf", "/livox/imu", "/livox/lidar", "/livox/lidar_raw_scan_end", "/slam_odom", "/navigation_odom",
                            "/scan", "/cmd_vel", "/cmd_vel_smooth", "/move_base/status",
                            "tf_common:map->base_link"])
    recorder.source_notes["/livox/lidar"] = {
        "probe": "AnyMsg header/timebase/first-last offsets only; point payload is not deserialized, copied or serialized",
        "header_time_semantics": "Livox scan BEGIN, often one scan interval older than scan-END TF; not pure transport delay"}
    recorder.source_notes["/livox/lidar_raw_scan_end"] = {
        "stamp_kind": "derived_timebase_plus_RAW_last_offset_not_actual_header",
        "timing_semantics": "Raw last serialized point; LIO-filtered last point and its TF scan-END can differ"}

    def caller(message):
        header = getattr(message, "_connection_header", None)
        return header.get("callerid", "") if isinstance(header, dict) else ""

    def stamped(topic, message, velocity=False):
        now, wall = time.monotonic(), time.time()
        twist = message.twist.twist if velocity else None
        recorder.record(topic, now, wall, message.header.stamp.to_sec(),
                        None if twist is None else twist.linear.x,
                        None if twist is None else twist.angular.z, caller(message))

    def command(topic, message):
        recorder.record(topic, time.monotonic(), time.time(), vx=message.linear.x,
                        wz=message.angular.z, caller=caller(message))

    def lidar_header(message):
        now, wall = time.monotonic(), time.time()
        connection = getattr(message, "_connection_header", None)
        declared_type = connection.get("type", "") if isinstance(connection, dict) else ""
        if declared_type not in {"livox_ros_driver2/CustomMsg", "livox_ros_driver/CustomMsg"}:
            with recorder.lock:
                recorder.source_notes["/livox/lidar"]["unsupported_connection_type"] = str(declared_type)[:160]
            return
        try:
            stamp, raw_end, point_count = decode_livox_timing(message._buff)
        except (ValueError, TypeError, struct.error):
            with recorder.lock:
                recorder.invalid_records += 1
            return
        recorder.record("/livox/lidar", now, wall, stamp, caller=caller(message))
        if raw_end is not None:
            recorder.record("/livox/lidar_raw_scan_end", now, wall, raw_end, caller=caller(message))
        with recorder.lock:
            recorder.source_notes["/livox/lidar"]["last_raw_point_count"] = point_count

    def transforms(message):
        now, wall, owner = time.monotonic(), time.time(), str(caller(message))[:160]
        recorder.record("/tf", now, wall, caller=owner)
        for transform in message.transforms[:64]:
            parent = str(transform.header.frame_id).strip("/")[:96]
            child = str(transform.child_frame_id).strip("/")[:96]
            recorder.record("tf_edge:{}->{}@{}".format(parent, child, owner), now, wall,
                            transform.header.stamp.to_sec(), caller=owner)

    def status(message):
        now = time.monotonic()
        recorder.record("/move_base/status", now, time.time(), message.header.stamp.to_sec(), caller=caller(message))
        recorder.status([(item.goal_id.id, item.status) for item in message.status_list[:64]], now)

    def log(message):
        recorder.warning(message.name, message.level, message.msg, time.monotonic(),
                         message.header.stamp.to_sec(), time.time())

    subscribers = [
        rospy.Subscriber("/tf", TFMessage, transforms, queue_size=1),
        rospy.Subscriber("/livox/imu", Imu, lambda msg: stamped("/livox/imu", msg), queue_size=1),
        rospy.Subscriber("/livox/lidar", rospy.AnyMsg, lidar_header, queue_size=1, buff_size=1048576),
        rospy.Subscriber("/slam_odom", Odometry, lambda msg: stamped("/slam_odom", msg, True), queue_size=1),
        rospy.Subscriber("/navigation_odom", Odometry, lambda msg: stamped("/navigation_odom", msg, True), queue_size=1),
        rospy.Subscriber("/scan", LaserScan, lambda msg: stamped("/scan", msg), queue_size=1),
        rospy.Subscriber("/cmd_vel", Twist, lambda msg: command("/cmd_vel", msg), queue_size=1),
        rospy.Subscriber("/cmd_vel_smooth", Twist, lambda msg: command("/cmd_vel_smooth", msg), queue_size=1),
        rospy.Subscriber("/move_base/status", GoalStatusArray, status, queue_size=1),
        rospy.Subscriber("/rosout", Log, log, queue_size=50)]
    emit({"status": "ready", "observational_only": True, "subscriptions_started": True,
          "robot_readiness_verified": False, "capture_duration_seconds": duration,
          "wait_for_goal_seconds": wait_for_goal, "terminal_tail_seconds": 8,
          "preflight_trace_window_seconds": 20,
          "livox_lidar_probe": "AnyMsg_O1_header_and_raw_last_offset_only; raw_last_can_differ_from_LIO_last",
          "note": "ACTIVE is an attempt; nonzero velocity is a command, not proof of physical movement"})
    reason = "duration_elapsed"
    next_tf = 0.0
    try:
        waiting_deadline = time.monotonic() + wait_for_goal
        if wait_for_goal == 0:
            recorder.begin(time.monotonic(), duration)
        while not rospy.is_shutdown():
            now = time.monotonic()
            observed, _ = recorder.observation()
            if recorder.origin is None:
                if observed:
                    recorder.begin(now, duration)
            stopped = recorder.stop_reason(now, waiting_deadline)
            if stopped is not None:
                reason = stopped
                break
            if now >= next_tf:
                next_tf = now + .5
                try:
                    stamp = listener.getLatestCommonTime("map", "base_link").to_sec()
                    recorder.record("tf_common:map->base_link", now, time.time(), stamp)
                except (tf.LookupException, tf.ConnectivityException, tf.ExtrapolationException) as exc:
                    recorder.warning("diagnostic_tf_listener", 4, str(exc), now)
            threading.Event().wait(.1)
        else:
            reason = "ROS_shutdown"
    except KeyboardInterrupt:
        reason = "interrupted"
    finally:
        for subscriber in subscribers:
            subscriber.unregister()
        emit(recorder.summary(time.monotonic(), reason))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration", type=float, default=30, help="capture seconds, >0 and <=180")
    parser.add_argument("--wait-for-goal", type=float, default=0, help="passively wait up to 600 seconds for ACTIVE")
    args = parser.parse_args(argv)
    if not finite(args.duration) or not 0 < args.duration <= 180:
        parser.error("--duration must be >0 and <=180")
    if not finite(args.wait_for_goal) or not 0 <= args.wait_for_goal <= 600:
        parser.error("--wait-for-goal must be 0..600")
    try:
        return run_ros(args.duration, args.wait_for_goal)
    except Exception as exc:
        emit({"status": "error", "observational_only": True, "message": str(exc)[:1000]})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
