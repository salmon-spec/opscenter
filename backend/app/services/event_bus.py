"""Kafka 事件总线客户端（v5.0.0）。

用途：
- 审计/告警/任务事件流：`publish(event_type, payload)` → KAFKA_TOPIC（key=event_type）；
- 只读浏览：topics / consumer groups。

环境变量：KAFKA_BOOTSTRAP、KAFKA_TOPIC、KAFKA_ENABLED（publish 需要 true；
只读浏览列表不受开关限制，只要 BOOTSTRAP 配置）。

实现：kafka-python 2.2.x，KafkaProducer（惰性创建，value_serializer=JSON utf-8，
acks=1，retries=1，max_block_ms=3000，linger_ms=50），KafkaAdminClient 用于列表。
全部 fail-open，publish 返回 bool。
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from app import config

logger = logging.getLogger("opscenter.event_bus")

try:  # v5.0.0 依赖已安装；导入失败时全部方法 fail-open
    from kafka import KafkaProducer
    from kafka import KafkaConsumer, TopicPartition
    from kafka.admin import KafkaAdminClient
except Exception:  # pragma: no cover - 依赖缺失兜底
    KafkaProducer = None  # type: ignore[assignment]
    KafkaConsumer = None  # type: ignore[assignment]
    TopicPartition = None  # type: ignore[assignment]
    KafkaAdminClient = None  # type: ignore[assignment]

_CACHE_TTL = 10.0
_CACHE: dict[str, Any] = {"at": 0.0, "ok": False, "latency": None, "error": ""}

_producer = None
_admin = None


def _bootstrap_servers() -> list[str]:
    return [item.strip() for item in (config.KAFKA_BOOTSTRAP or "").split(",") if item.strip()]


def _sanitize(message: Any) -> str:
    """错误脱敏：任何 user:pass@ 片段一律打码。"""
    text = str(message or "")[:300]
    return re.sub(r"://[^/@\s]+@", "://***@", text)


def _call(fn, *args, timeout: float | None = None):
    """调用 kafka 客户端方法：优先带 timeout=；旧签名不支持时退回无参调用。"""
    if timeout is not None:
        try:
            return fn(*args, timeout=timeout)
        except TypeError:
            pass
    return fn(*args)


def _get_producer():
    """惰性单例 KafkaProducer；未配置/依赖缺失返回 None。"""
    global _producer
    if _producer is not None:
        return _producer
    servers = _bootstrap_servers()
    if not servers or KafkaProducer is None:
        return None
    _producer = KafkaProducer(
        bootstrap_servers=servers,
        value_serializer=lambda value: json.dumps(value, ensure_ascii=False).encode("utf-8"),
        acks=1,
        retries=1,
        max_block_ms=3000,
        linger_ms=50,
        request_timeout_ms=5000,
    )
    return _producer


def _get_admin():
    """惰性单例 KafkaAdminClient；未配置/依赖缺失返回 None。"""
    global _admin
    if _admin is not None:
        return _admin
    servers = _bootstrap_servers()
    if not servers or KafkaAdminClient is None:
        return None
    request_timeout_ms = max(1000, int(float(config.MW_PROBE_TIMEOUT or 4.0) * 1000))
    _admin = KafkaAdminClient(
        bootstrap_servers=servers,
        request_timeout_ms=request_timeout_ms,
    )
    return _admin


def _probe(force: bool = False) -> tuple[bool, float | None, str]:
    """admin.list_topics 探测；available() 复用 10s 缓存，status() force=True 实时探测。"""
    now = time.monotonic()
    if not force and _CACHE["at"] > 0 and (now - _CACHE["at"]) < _CACHE_TTL:
        return bool(_CACHE["ok"]), _CACHE["latency"], _CACHE["error"]
    ok, latency, error = False, None, ""
    started = time.monotonic()
    try:
        admin = _get_admin()
        if admin is not None:
            _call(admin.list_topics, timeout=config.MW_PROBE_TIMEOUT)
            ok = True
    except Exception as exc:
        error = _sanitize(exc)
    latency = round((time.monotonic() - started) * 1000, 1)
    _CACHE.update(at=time.monotonic(), ok=ok, latency=latency, error=error)
    return ok, latency, error


def _partition_count(partitions: Any) -> int:
    if partitions is None:
        return 0
    try:
        return len(partitions)
    except TypeError:
        return 0


def _replicas_of(partitions: Any) -> int:
    """第 0 分区（缺失时取第一个分区）的副本数。"""
    first = None
    if isinstance(partitions, dict):
        if 0 in partitions:
            first = partitions[0]
        elif partitions:
            try:
                first = partitions[min(partitions)]
            except Exception:
                first = next(iter(partitions.values()))
    elif isinstance(partitions, (list, tuple)) and partitions:
        first = partitions[0]
    if first is None:
        return 0
    replicas = getattr(first, "replicas", None)
    if replicas is None and isinstance(first, dict):
        replicas = first.get("replicas")
    try:
        return len(replicas or [])
    except TypeError:
        return 0


def _entry(name: str, partitions: Any) -> dict:
    return {
        "name": name,
        "partitions": _partition_count(partitions),
        "replicas": _replicas_of(partitions),
    }


def _topic_entries(raw: Any) -> list[dict] | None:
    """归一化各种元数据形态：
    - ClusterMetadata（.topics dict）→ {name: TopicMetadata}
    - list[dict]（admin.describe_topics 真实返回）→ partition dict 列表
    - list[str]（admin.list_topics 真实返回）/ list[TopicMetadata]
    """
    if raw is None:
        return None
    topics = getattr(raw, "topics", None)
    if topics is not None and not isinstance(raw, (dict, list, tuple)):
        raw = topics
    if isinstance(raw, dict):
        entries = []
        for name, meta in raw.items():
            if isinstance(meta, dict):
                partitions = meta.get("partitions")
            else:
                partitions = getattr(meta, "partitions", None)
            entries.append(_entry(str(name), partitions))
        return entries
    if isinstance(raw, (list, tuple)):
        entries = []
        for item in raw:
            if isinstance(item, str):
                entries.append({"name": item, "partitions": 0, "replicas": 0})
            elif isinstance(item, dict):
                name = item.get("topic") or item.get("name") or ""
                entries.append(_entry(str(name), item.get("partitions")))
            else:
                name = getattr(item, "topic", None) or getattr(item, "name", None) or ""
                partitions = getattr(item, "partitions", None)
                entries.append(_entry(str(name), partitions))
        return entries
    return None


def _describe_topics(admin) -> list[dict] | None:
    describe = getattr(admin, "describe_topics", None)
    if describe is None:
        return None
    try:
        raw = _call(describe, timeout=config.MW_PROBE_TIMEOUT)
    except Exception:
        return None
    return _topic_entries(raw)


def _clamp(value: Any, default: int, maximum: int) -> int:
    try:
        number = int(value)
    except Exception:
        number = default
    return max(1, min(number, maximum))


class EventBus:
    def available(self) -> bool:
        try:
            if not _bootstrap_servers():
                return False
            return _probe()[0]
        except Exception:
            return False

    def status(self) -> dict:
        """{"kind":"kafka","configured","available","latency_ms","error",
        "detail":{"bootstrap":"host:port","topic":...}}"""
        result = {
            "kind": "kafka",
            "configured": bool(_bootstrap_servers()),
            "available": False,
            "latency_ms": None,
            "error": "",
            "detail": {"bootstrap": config.KAFKA_BOOTSTRAP or "", "topic": config.KAFKA_TOPIC or ""},
        }
        if not result["configured"]:
            return result
        try:
            ok, latency, error = _probe(force=True)
            result.update(available=ok, latency_ms=latency, error=error)
        except Exception as exc:
            result["error"] = _sanitize(exc)
        return result

    def publish(self, event_type: str, payload: dict) -> bool:
        """发一条事件；不可用返回 False。payload 会补 event_type/ts(epoch 秒)。"""
        if not config.KAFKA_ENABLED or not _bootstrap_servers():
            return False
        try:
            producer = _get_producer()
            if producer is None:
                return False
            event = dict(payload or {})
            event["event_type"] = event_type
            event.setdefault("ts", int(time.time()))
            future = producer.send(
                config.KAFKA_TOPIC,
                key=event_type.encode("utf-8"),
                value=event,
            )
            future.get(timeout=config.MW_PROBE_TIMEOUT)
            return True
        except Exception as exc:
            logger.warning("kafka publish failed: %s", _sanitize(exc))
            return False

    def topics(self, limit: int = 200) -> list[dict]:
        """[{"name","partitions","replicas"}]"""
        try:
            admin = _get_admin()
            if admin is None:
                return []
            raw = _call(admin.list_topics, timeout=config.MW_PROBE_TIMEOUT)
            entries = _topic_entries(raw)
            if entries is None:
                return []
            # 仅返回主题名（无分区信息）时，尝试用 describe_topics 补全
            if not any(entry["partitions"] for entry in entries):
                described = _describe_topics(admin)
                if described:
                    entries = described
            entries.sort(key=lambda entry: entry["name"])
            return entries[:_clamp(limit, 200, 1000)]
        except Exception as exc:
            logger.warning("kafka topics failed: %s", _sanitize(exc))
            return []

    def consumer_groups(self, limit: int = 100) -> list[dict]:
        """[{"group","state","members","topics"}]；lag 获取昂贵可省略。"""
        try:
            admin = _get_admin()
            if admin is None:
                return []
            raw = _call(admin.list_consumer_groups, timeout=config.MW_PROBE_TIMEOUT)
        except Exception as exc:
            logger.warning("kafka consumer groups failed: %s", _sanitize(exc))
            return []

        entries: list[dict] = []
        for item in raw or []:
            if isinstance(item, (tuple, list)):
                group_id = item[0] if item else ""
            elif isinstance(item, dict):
                group_id = item.get("group") or item.get("group_id") or ""
            else:
                group_id = getattr(item, "group_id", None) or getattr(item, "group", None) or ""
            if not group_id:
                continue
            entries.append({"group": str(group_id), "state": "", "members": 0, "topics": []})
        entries = entries[:_clamp(limit, 100, 1000)]
        if entries:
            self._merge_group_details(admin, entries)
        entries.sort(key=lambda entry: entry["group"])
        return entries

    @staticmethod
    def _merge_group_details(admin, entries: list[dict]) -> None:
        """describe_consumer_groups 合并 state/members/topics；失败保留基础字段。"""
        index = {entry["group"]: entry for entry in entries}
        try:
            described = _call(
                admin.describe_consumer_groups,
                list(index),
                timeout=config.MW_PROBE_TIMEOUT,
            )
        except Exception:
            return
        for description in described or []:
            if isinstance(description, dict):
                group_id = str(description.get("group_id") or description.get("group") or "")
                state = description.get("state") or ""
                members = description.get("members") or []
            else:
                group_id = str(getattr(description, "group_id", None)
                                or getattr(description, "group", "") or "")
                state = getattr(description, "state", "") or ""
                members = getattr(description, "members", None) or []
            entry = index.get(group_id)
            if entry is None:
                continue
            entry["state"] = str(state)
            entry["members"] = len(members)
            topics = set()
            for member in members:
                assignment = (member.get("member_assignment") if isinstance(member, dict)
                              else getattr(member, "member_assignment", None))
                member_topics = (assignment.get("topics") if isinstance(assignment, dict)
                                 else getattr(assignment, "topics", None)) or []
                for topic in member_topics:
                    if isinstance(topic, (tuple, list)):
                        topic = topic[0] if topic else ""
                    topic = str(topic)
                    if topic:
                        topics.add(topic)
            entry["topics"] = sorted(topics)

    def consumer_group_lags(self, limit: int = 100) -> list[dict]:
        """Best-effort committed/end offset lag for consumer groups."""
        admin = _get_admin()
        if admin is None or KafkaConsumer is None or TopicPartition is None:
            return []
        try:
            groups = self.consumer_groups(limit=limit)
            committed: dict = {}
            partitions: set = set()
            for group in groups:
                group_id = str(group.get("group") or "")
                if not group_id:
                    continue
                try:
                    result = _call(admin.list_consumer_group_offsets, group_id,
                                   timeout=config.MW_PROBE_TIMEOUT)
                except Exception:
                    continue
                items = result.items() if isinstance(result, dict) else (result or [])
                for partition, offset_meta in items:
                    offset = (offset_meta.get("offset") if isinstance(offset_meta, dict)
                              else getattr(offset_meta, "offset", None))
                    if offset is None or not isinstance(partition, TopicPartition):
                        continue
                    committed[(group_id, partition.topic, int(partition.partition))] = int(offset)
                    partitions.add(partition)
            if not partitions:
                return []
            consumer = KafkaConsumer(
                bootstrap_servers=_bootstrap_servers(),
                request_timeout_ms=max(1000, int(float(config.MW_PROBE_TIMEOUT or 4.0) * 1000)),
                consumer_timeout_ms=max(1000, int(float(config.MW_PROBE_TIMEOUT or 4.0) * 1000)),
                enable_auto_commit=False,
            )
            try:
                ends = consumer.end_offsets(list(partitions))
            finally:
                consumer.close()
            rows: list[dict] = []
            for (group_id, topic, partition), committed_offset in committed.items():
                key = next((item for item in partitions
                            if item.topic == topic and int(item.partition) == partition), None)
                end_offset = ends.get(key) if key is not None else None
                if end_offset is None:
                    continue
                rows.append({
                    "group": group_id, "topic": topic, "partition": partition,
                    "current_offset": committed_offset, "log_end_offset": int(end_offset),
                    "lag": max(0, int(end_offset) - committed_offset),
                })
            return rows
        except Exception:
            return []

    def monitor_metrics(self) -> list[dict]:
        admin = _get_admin()
        if admin is None:
            return []
        rows: list[dict] = []

        def add(key: str, label: str, value: Any, unit: str = "count",
                dimensions: dict | None = None, source: str = "admin") -> None:
            if value is None:
                return
            rows.append({"key": key, "label": label, "value": value, "unit": unit,
                         "dimensions": dimensions or {}, "source": source})

        try:
            cluster = _call(admin.describe_cluster, timeout=config.MW_PROBE_TIMEOUT) or {}
            brokers = cluster.get("brokers") if isinstance(cluster, dict) else getattr(cluster, "brokers", None)
            add("kafka_brokers_total", "Broker 数", len(brokers or []))
            controller = cluster.get("controller") if isinstance(cluster, dict) else getattr(cluster, "controller", None)
            if controller is not None:
                add("kafka_controller_id", "Controller ID",
                    controller.get("id") if isinstance(controller, dict) else getattr(controller, "id", None))
        except Exception:
            pass

        topics = self.topics(limit=1000)
        add("kafka_topics_total", "Topic 数", len(topics))
        partition_total = sum(int(item.get("partitions") or 0) for item in topics)
        add("kafka_partitions_total", "分区总数", partition_total)
        for topic in topics[:200]:
            dims = {"topic": str(topic.get("name") or "")}
            add("kafka_topic_partitions", "Topic 分区数", topic.get("partitions"), "count", dims)
            add("kafka_topic_replicas", "Topic 副本数", topic.get("replicas"), "count", dims)

        try:
            described = _call(admin.describe_topics, timeout=config.MW_PROBE_TIMEOUT) or []
            if isinstance(described, dict):
                if isinstance(described.get("topics"), dict):
                    described = described["topics"]
                if isinstance(described, dict):
                    if {"topic", "name", "partitions"} & set(described):
                        described = [described]
                    else:
                        described = [
                            dict(meta, topic=name) if isinstance(meta, dict)
                            else {"topic": name, "partitions": getattr(meta, "partitions", None)}
                            for name, meta in described.items()
                        ]
            elif not isinstance(described, (list, tuple)):
                described = [described]
            under = offline = 0
            for item in described:
                parts = item.get("partitions") if isinstance(item, dict) else getattr(item, "partitions", None)
                if isinstance(parts, dict):
                    parts = parts.values()
                for partition in parts or []:
                    replicas = partition.get("replicas") if isinstance(partition, dict) else getattr(partition, "replicas", None)
                    isr = partition.get("isr") if isinstance(partition, dict) else getattr(partition, "isr", None)
                    leader = partition.get("leader") if isinstance(partition, dict) else getattr(partition, "leader", None)
                    if len(isr or []) < len(replicas or []):
                        under += 1
                    if leader is None or int(leader) < 0:
                        offline += 1
            add("kafka_under_replicated_partitions", "UnderReplicated 分区", under)
            add("kafka_offline_partitions", "离线分区", offline)
        except Exception:
            pass

        groups = self.consumer_groups(limit=1000)
        add("kafka_consumer_groups_total", "消费组数", len(groups))
        add("kafka_consumer_members_total", "消费者成员数",
            sum(int(group.get("members") or 0) for group in groups))
        lags = self.consumer_group_lags(limit=100)
        add("kafka_consumer_lag_total", "消费 Lag 合计", sum(int(item.get("lag") or 0) for item in lags))
        for item in sorted(lags, key=lambda row: int(row.get("lag") or 0), reverse=True)[:200]:
            dims = {
                "group": str(item.get("group") or ""),
                "topic": str(item.get("topic") or ""),
                "partition": str(item.get("partition") or ""),
            }
            add("kafka_partition_consumer_lag", "分区消费 Lag", item.get("lag"), "count", dims, "offsets")
        return rows

    def close(self) -> None:
        global _producer, _admin
        producer, _producer = _producer, None
        admin, _admin = _admin, None
        _CACHE.update(at=0.0, ok=False, latency=None, error="")
        for client in (producer, admin):
            if client is None:
                continue
            try:
                client.close()
            except Exception:
                pass


event_bus = EventBus()
