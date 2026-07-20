"""Decode EcoFlow Power Ocean MQTT protobuf payloads."""

from __future__ import annotations

import logging
from typing import Any

from google.protobuf.message import DecodeError

from . import powerocean_pb2 as pb

_LOGGER = logging.getLogger(__name__)

_SENT_TYPES: tuple[type, ...] = (
    pb.sentJTS1_ENERGY_STREAM_REPORT,
    pb.sentJTS1_EMS_HEARTBEAT,
    pb.sentJTS1_EMS_CHANGE_REPORT,
    pb.sentParallelEnergyStreamReport,
)


def _phase_to_dict(phase: pb.pcsPhase) -> dict[str, float]:
    data: dict[str, float] = {}
    if phase.HasField("vol"):
        data["vol"] = phase.vol
    if phase.HasField("amp"):
        data["amp"] = phase.amp
    power = phase.actPwr if phase.HasField("actPwr") else phase.pwr if phase.HasField("pwr") else None
    if power is not None:
        data["actPwr"] = power
    return data


def _from_energy_stream(report: pb.JTS1_ENERGY_STREAM_REPORT) -> dict[str, Any]:
    flat: dict[str, Any] = {}
    if report.HasField("sysLoadPwr"):
        flat["sysLoadPwr"] = report.sysLoadPwr
    if report.HasField("sysGridPwr"):
        flat["sysGridPwr"] = report.sysGridPwr
    if report.HasField("mpptPwr"):
        flat["mpptPwr"] = report.mpptPwr
    if report.HasField("bpPwr"):
        flat["bpPwr"] = report.bpPwr
    if report.HasField("bpSoc"):
        flat["bpSoc"] = report.bpSoc
    return flat


def _from_heartbeat(report: pb.JTS1_EMS_HEARTBEAT) -> dict[str, Any]:
    flat: dict[str, Any] = {}
    if report.HasField("emsBpPower"):
        flat["bpPwr"] = report.emsBpPower
    for name in ("pcsAPhase", "pcsBPhase", "pcsCPhase"):
        phase = getattr(report, name)
        if phase.ByteSize() > 0:
            flat[name] = _phase_to_dict(phase)
            for key, value in _phase_to_dict(phase).items():
                flat[f"{name}.{key}"] = value
    return flat


def _from_change_report(report: pb.JTS1_EMS_CHANGE_REPORT) -> dict[str, Any]:
    flat: dict[str, Any] = {}
    if report.HasField("bpSoc"):
        flat["bpSoc"] = report.bpSoc
    if report.HasField("sys_bat_chg_up_limit"):
        flat["sysBatChgUpLimit"] = report.sys_bat_chg_up_limit
    if report.HasField("sys_bat_dsg_down_limit"):
        flat["sysBatDsgDownLimit"] = report.sys_bat_dsg_down_limit
    if report.HasField("emsFeedRatio"):
        flat["emsFeedRatio"] = report.emsFeedRatio
    if report.HasField("emsFeedPwr"):
        flat["emsFeedPwr"] = report.emsFeedPwr
    return flat


def _from_parallel(report: pb.ParallelEnergyStreamReport, serial: str | None = None) -> dict[str, Any]:
    flat: dict[str, Any] = {}
    for stream in report.para_energy_stream:
        prefix = ""
        if serial and stream.HasField("dev_sn") and stream.dev_sn != serial:
            if stream.dev_sn.startswith("HR51"):
                prefix = "ocean_"
            else:
                prefix = "system1_"
        mapping = {
            "sysLoadPwr": stream.sys_load_pwr if stream.HasField("sys_load_pwr") else None,
            "sysGridPwr": stream.sys_grid_pwr if stream.HasField("sys_grid_pwr") else None,
            "mpptPwr": stream.mppt_pwr if stream.HasField("mppt_pwr") else None,
            "bpPwr": stream.bp_pwr if stream.HasField("bp_pwr") else None,
            "bpSoc": stream.bp_soc if stream.HasField("bp_soc") else None,
        }
        for key, value in mapping.items():
            if value is None:
                continue
            flat[f"{prefix}{key}" if prefix else key] = value
    return flat


def _extract_from_message(message: Any, serial: str | None = None) -> dict[str, Any]:
    if not message.HasField("header"):
        return {}
    header = message.header
    if not header.HasField("pdata"):
        return {}

    if isinstance(message, pb.sentJTS1_ENERGY_STREAM_REPORT):
        return _from_energy_stream(header.pdata)
    if isinstance(message, pb.sentJTS1_EMS_HEARTBEAT):
        return _from_heartbeat(header.pdata)
    if isinstance(message, pb.sentJTS1_EMS_CHANGE_REPORT):
        return _from_change_report(header.pdata)
    if isinstance(message, pb.sentParallelEnergyStreamReport):
        return _from_parallel(header.pdata, serial)
    return {}


def parse_protobuf_payload(payload: bytes, serial: str | None = None) -> dict[str, Any] | None:
    """Decode a binary EcoFlow MQTT payload into flat telemetry fields."""
    for message_type in _SENT_TYPES:
        message = message_type()
        try:
            message.ParseFromString(payload)
        except DecodeError:
            continue
        flat = _extract_from_message(message, serial)
        if flat:
            _LOGGER.debug(
                "Decoded %s: bpSoc=%s sysLoadPwr=%s",
                message_type.__name__,
                flat.get("bpSoc"),
                flat.get("sysLoadPwr"),
            )
            return flat
    return None
