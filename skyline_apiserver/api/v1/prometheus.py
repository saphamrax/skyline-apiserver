# Copyright 2022 99cloud
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import annotations

import re
import time
from typing import Any

from fastapi import status
from fastapi.exceptions import HTTPException
from fastapi.param_functions import Depends, Query
from fastapi.routing import APIRouter
from httpx import codes

from skyline_apiserver import schemas
from skyline_apiserver.api import deps
from skyline_apiserver.config import CONF
from skyline_apiserver.types import constants
from skyline_apiserver.utils.httpclient import _http_request
from skyline_apiserver.utils.roles import is_system_admin_or_reader

router = APIRouter()

MAX_MONITORING_WINDOW_SECONDS = 7 * 24 * 60 * 60


_PROMETHEUS_RE2_SPECIAL = re.compile(r'([{}()\[\]^$.|*+?\\])')


def _escape_regex_value(value: str) -> str:
    return _PROMETHEUS_RE2_SPECIAL.sub(r'\\\1', value)


def _prometheus_auth() -> Any:
    if CONF.default.prometheus_enable_basic_auth:
        return (
            CONF.default.prometheus_basic_auth_user,
            CONF.default.prometheus_basic_auth_password,
        )
    return None


def _prometheus_get(url: str, params: dict[str, Any]) -> Any:
    resp = _http_request(url=url, params=params, auth=_prometheus_auth())
    if resp.status_code != codes.OK:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    return resp.json()


def _normalize_window(start: int | None, end: int | None, step: int | None) -> tuple[int, int, int]:
    now = int(time.time())
    end_ts = end if end is not None else now
    start_ts = start if start is not None else end_ts - 3600
    if start_ts >= end_ts:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid time range: start must be less than end.",
        )

    window = end_ts - start_ts
    if window > MAX_MONITORING_WINDOW_SECONDS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid time range: query window exceeds 7 days.",
        )

    min_step = 10
    if window > 24 * 60 * 60:
        min_step = 300
    elif window > 60 * 60:
        min_step = 60

    normalized_step = max(step or min_step, min_step)
    return start_ts, end_ts, normalized_step


def _get_effective_project_id(
    profile: schemas.Profile,
    project_id: str | None,
) -> str:
    if is_system_admin_or_reader(profile):
        return project_id or profile.project.id
    if project_id is not None and project_id != profile.project.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: cannot query metrics for another project.",
        )
    return profile.project.id


def _build_info_matchers(project_id: str, instance_ids: list[str] | None = None) -> str:
    matchers = [f'project_id="{project_id}"']
    if instance_ids:
        sanitized = [iid.strip() for iid in instance_ids if iid.strip()]
        if sanitized:
            ids = "|".join(_escape_regex_value(iid) for iid in sanitized)
            matchers.append(f'instance_id=~"{ids}"')
    return "{" + ",".join(matchers) + "}"


def _convert_range_result_to_series(resp: dict[str, Any]) -> list[schemas.MonitoringMetricSeries]:
    data = resp.get("data", {})
    result = data.get("result", [])
    return [
        schemas.MonitoringMetricSeries(metric=item.get("metric", {}), values=item.get("values", []))
        for item in result
    ]


_JOIN_LABELS = "instance_id,instance_name,project_id,project_name"


def _instance_metric_queries(info_matchers: str) -> dict[str, str]:
    join = (
        f" * on(domain) group_left({_JOIN_LABELS}) "
        f"libvirt_domain_openstack_info{info_matchers}"
    )
    by = f"by({_JOIN_LABELS})"
    net_by = f"by({_JOIN_LABELS},target_device)"
    iops_by = f"by({_JOIN_LABELS},target_device)"
    return {
        "cpu": (
            f"sum {by} ("
            f"rate(libvirt_domain_info_cpu_time_seconds_total[5m])"
            f"{join}) * 100"
        ),
        "memory": (
            f"max {by} ("
            f"libvirt_domain_memory_stats_used_percent"
            f"{join})"
        ),
        "network_rx": (
            f"sum {net_by} ("
            f"rate(libvirt_domain_interface_stats_receive_bytes_total[5m])"
            f"{join})"
        ),
        "network_tx": (
            f"sum {net_by} ("
            f"rate(libvirt_domain_interface_stats_transmit_bytes_total[5m])"
            f"{join})"
        ),
        "disk_read": (
            f"sum {by} ("
            f"rate(libvirt_domain_block_stats_read_bytes_total[5m])"
            f"{join})"
        ),
        "disk_write": (
            f"sum {by} ("
            f"rate(libvirt_domain_block_stats_write_bytes_total[5m])"
            f"{join})"
        ),
        "disk_read_iops": (
            f"sum {iops_by} ("
            f"rate(libvirt_domain_block_stats_read_requests_total[5m])"
            f"{join})"
        ),
        "disk_write_iops": (
            f"sum {iops_by} ("
            f"rate(libvirt_domain_block_stats_write_requests_total[5m])"
            f"{join})"
        ),
    }


def get_prometheus_query_response(
    resp: dict,
    profile: schemas.Profile,
) -> schemas.PrometheusQueryResponse:
    ret = schemas.PrometheusQueryResponse(status=resp["status"])
    if "warnings" in resp:
        ret.warnings = resp["warnings"]
    if "errorType" in resp:
        ret.errorType = resp["errorType"]
    if "error" in resp:
        ret.error = resp["error"]
    if "data" in resp:
        result = [
            schemas.PrometheusQueryResult(metric=i["metric"], value=i["value"])
            for i in resp["data"]["result"]
        ]

        if not is_system_admin_or_reader(profile):
            result = [
                i
                for i in result
                if "project_id" in i.metric and i.metric["project_id"] == profile.project.id
            ]

        data = schemas.PrometheusQueryData(
            resultType=resp["data"]["resultType"],
            result=result,
        )
        ret.data = data

    return ret


def get_prometheus_query_range_response(
    resp: dict,
    profile: schemas.Profile,
) -> schemas.PrometheusQueryRangeResponse:
    ret = schemas.PrometheusQueryRangeResponse(status=resp["status"])
    if "warnings" in resp:
        ret.warnings = resp["warnings"]
    if "errorType" in resp:
        ret.errorType = resp["errorType"]
    if "error" in resp:
        ret.error = resp["error"]
    if "data" in resp:
        result = [
            schemas.PrometheusQueryRangeResult(metric=i["metric"], value=i["values"])
            for i in resp["data"]["result"]
        ]

        if not is_system_admin_or_reader(profile):
            result = [
                i
                for i in result
                if "project_id" in i.metric and i.metric["project_id"] == profile.project.id
            ]

        data = schemas.PrometheusQueryRangeData(
            resultType=resp["data"]["resultType"],
            result=result,
        )
        ret.data = data

    return ret


@router.get(
    "/query",
    description="Prometheus query API.",
    responses={
        200: {"model": schemas.PrometheusQueryResponse},
        401: {"model": schemas.UnauthorizedMessage},
        500: {"model": schemas.InternalServerErrorMessage},
    },
    response_model=schemas.PrometheusQueryResponse,
    status_code=status.HTTP_200_OK,
    response_description="OK",
    response_model_exclude_none=True,
)
def prometheus_query(
    query: str = Query(None, description="The query expression of prometheus to filter."),
    time: str = Query(None, description="The time to filter."),
    timeout: str = Query(None, description="The timeout to filter."),
    profile: schemas.Profile = Depends(deps.get_profile_update_jwt),
) -> schemas.PrometheusQueryResponse:
    kwargs = {}
    if query is not None:
        kwargs["query"] = query
    if time is not None:
        kwargs["time"] = time
    if timeout is not None:
        kwargs["timeout"] = timeout

    auth = None
    if CONF.default.prometheus_enable_basic_auth:
        auth = (
            CONF.default.prometheus_basic_auth_user,
            CONF.default.prometheus_basic_auth_password,
        )
    resp = _http_request(
        url=CONF.default.prometheus_endpoint + constants.PROMETHEUS_QUERY_API,
        params=kwargs,
        auth=auth,
    )

    if resp.status_code != codes.OK:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)

    return get_prometheus_query_response(resp.json(), profile)


@router.get(
    "/query_range",
    description="Prometheus query_range API.",
    responses={
        200: {"model": schemas.PrometheusQueryRangeResponse},
        401: {"model": schemas.UnauthorizedMessage},
        500: {"model": schemas.InternalServerErrorMessage},
    },
    response_model=schemas.PrometheusQueryRangeResponse,
    status_code=status.HTTP_200_OK,
    response_description="OK",
    response_model_exclude_none=True,
)
def prometheus_query_range(
    query: str = Query(None, description="The query expression of prometheus to filter."),
    start: str = Query(None, description="The start time to filter."),
    end: str = Query(None, description="The end time to filter."),
    step: str = Query(None, description="The step to filter."),
    timeout: str = Query(None, description="The timeout to filter."),
    profile: schemas.Profile = Depends(deps.get_profile_update_jwt),
) -> schemas.PrometheusQueryRangeResponse:
    kwargs = {}
    if query is not None:
        kwargs["query"] = query
    if start is not None:
        kwargs["start"] = start
    if end is not None:
        kwargs["end"] = end
    if step is not None:
        kwargs["step"] = step
    if timeout is not None:
        kwargs["timeout"] = timeout

    auth = None
    if CONF.default.prometheus_enable_basic_auth:
        auth = (
            CONF.default.prometheus_basic_auth_user,
            CONF.default.prometheus_basic_auth_password,
        )
    resp = _http_request(
        url=CONF.default.prometheus_endpoint + constants.PROMETHEUS_QUERY_RANGE_API,
        params=kwargs,
        auth=auth,
    )

    if resp.status_code != codes.OK:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)

    return get_prometheus_query_range_response(resp.json(), profile)


@router.get(
    "/monitoring/instances",
    description="List instances available in monitoring metrics.",
    responses={
        200: {"model": schemas.MonitoringInstancesResponse},
        401: {"model": schemas.UnauthorizedMessage},
        403: {"model": schemas.ForbiddenMessage},
        500: {"model": schemas.InternalServerErrorMessage},
    },
    response_model=schemas.MonitoringInstancesResponse,
    status_code=status.HTTP_200_OK,
    response_description="OK",
)
def list_monitoring_instances(
    project_id: str | None = Query(None, description="Project ID to filter."),
    profile: schemas.Profile = Depends(deps.get_profile_update_jwt),
) -> schemas.MonitoringInstancesResponse:
    effective_project_id = _get_effective_project_id(profile, project_id)
    info_matchers = _build_info_matchers(effective_project_id)
    query = f"libvirt_domain_openstack_info{info_matchers}"
    resp = _prometheus_get(
        CONF.default.prometheus_endpoint + constants.PROMETHEUS_QUERY_API,
        {"query": query},
    )
    instances = []
    for item in resp.get("data", {}).get("result", []):
        metric = item.get("metric", {})
        instance_id = metric.get("instance_id")
        if not instance_id:
            continue
        instances.append(
            schemas.MonitoringInstance(
                instance_id=instance_id,
                instance_name=metric.get("instance_name"),
                project_id=metric.get("project_id"),
                project_name=metric.get("project_name"),
                host=metric.get("instance"),
            )
        )
    return schemas.MonitoringInstancesResponse(instances=instances)


@router.get(
    "/monitoring/instances/{instance_id}/metrics",
    description="Get basic monitoring metrics for one instance.",
    responses={
        200: {"model": schemas.MonitoringMetricsResponse},
        401: {"model": schemas.UnauthorizedMessage},
        403: {"model": schemas.ForbiddenMessage},
        500: {"model": schemas.InternalServerErrorMessage},
    },
    response_model=schemas.MonitoringMetricsResponse,
    status_code=status.HTTP_200_OK,
    response_description="OK",
)
def get_instance_monitoring_metrics(
    instance_id: str,
    start: int | None = Query(None, description="Range start timestamp (seconds)."),
    end: int | None = Query(None, description="Range end timestamp (seconds)."),
    step: int | None = Query(None, description="Range step in seconds."),
    project_id: str | None = Query(None, description="Project ID to filter."),
    profile: schemas.Profile = Depends(deps.get_profile_update_jwt),
) -> schemas.MonitoringMetricsResponse:
    return get_instances_monitoring_metrics(
        instance_ids=instance_id,
        start=start,
        end=end,
        step=step,
        project_id=project_id,
        profile=profile,
    )


@router.get(
    "/monitoring/instances/metrics",
    description="Get basic monitoring metrics for one or more instances.",
    responses={
        200: {"model": schemas.MonitoringMetricsResponse},
        401: {"model": schemas.UnauthorizedMessage},
        403: {"model": schemas.ForbiddenMessage},
        500: {"model": schemas.InternalServerErrorMessage},
    },
    response_model=schemas.MonitoringMetricsResponse,
    status_code=status.HTTP_200_OK,
    response_description="OK",
)
def get_instances_monitoring_metrics(
    instance_ids: str = Query(..., description="Comma-separated instance IDs."),
    start: int | None = Query(None, description="Range start timestamp (seconds)."),
    end: int | None = Query(None, description="Range end timestamp (seconds)."),
    step: int | None = Query(None, description="Range step in seconds."),
    project_id: str | None = Query(None, description="Project ID to filter."),
    profile: schemas.Profile = Depends(deps.get_profile_update_jwt),
) -> schemas.MonitoringMetricsResponse:
    effective_project_id = _get_effective_project_id(profile, project_id)
    start_ts, end_ts, normalized_step = _normalize_window(start, end, step)
    ids = [iid.strip() for iid in instance_ids.split(",") if iid.strip()]
    if not ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="instance_ids must include at least one instance id.",
        )

    info_matchers = _build_info_matchers(effective_project_id, ids)
    queries = _instance_metric_queries(info_matchers)

    range_params = {"start": start_ts, "end": end_ts, "step": normalized_step}
    range_api = CONF.default.prometheus_endpoint + constants.PROMETHEUS_QUERY_RANGE_API

    cpu = _convert_range_result_to_series(
        _prometheus_get(range_api, {"query": queries["cpu"], **range_params})
    )
    memory = _convert_range_result_to_series(
        _prometheus_get(range_api, {"query": queries["memory"], **range_params})
    )
    network_rx = _convert_range_result_to_series(
        _prometheus_get(range_api, {"query": queries["network_rx"], **range_params})
    )
    network_tx = _convert_range_result_to_series(
        _prometheus_get(range_api, {"query": queries["network_tx"], **range_params})
    )
    disk_read = _convert_range_result_to_series(
        _prometheus_get(range_api, {"query": queries["disk_read"], **range_params})
    )
    disk_write = _convert_range_result_to_series(
        _prometheus_get(range_api, {"query": queries["disk_write"], **range_params})
    )
    disk_read_iops = _convert_range_result_to_series(
        _prometheus_get(range_api, {"query": queries["disk_read_iops"], **range_params})
    )
    disk_write_iops = _convert_range_result_to_series(
        _prometheus_get(range_api, {"query": queries["disk_write_iops"], **range_params})
    )

    return schemas.MonitoringMetricsResponse(
        data=schemas.MonitoringMetricsData(
            start=start_ts,
            end=end_ts,
            step=normalized_step,
            cpu=cpu,
            memory=memory,
            network_rx=network_rx,
            network_tx=network_tx,
            disk_read=disk_read,
            disk_write=disk_write,
            disk_read_iops=disk_read_iops,
            disk_write_iops=disk_write_iops,
        )
    )
