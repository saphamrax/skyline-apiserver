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

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class PrometheusQueryResultBase(BaseModel):
    metric: Dict[str, str] = Field(..., description="Prometheus metric")
    value: List[Any] = Field(..., description="Prometheus metric value")


class PrometheusQueryDataBase(BaseModel):
    resultType: str = Field(..., description="Prometheus result type")


class PrometheusResponseBase(BaseModel):
    status: str = Field(..., description="Prometheus status")
    errorType: Optional[str] = Field(default=None, description="Prometheus error type")
    error: Optional[str] = Field(default=None, description="Prometheus error")
    warnings: Optional[str] = Field(default=None, description="Prometheus warnings")


class PrometheusQueryResult(PrometheusQueryResultBase):
    """"""


class PrometheusQueryData(PrometheusQueryDataBase):
    result: List[PrometheusQueryResult] = Field(..., description="Prometheus query result")


class PrometheusQueryResponse(PrometheusResponseBase):
    data: Optional[PrometheusQueryData] = Field(default=None, description="Prometheus query data")


class PrometheusQueryRangeResult(PrometheusQueryResultBase):
    """"""


class PrometheusQueryRangeData(PrometheusQueryDataBase):
    result: List[PrometheusQueryRangeResult] = Field(
        ..., description="Prometheus query range result"
    )


class PrometheusQueryRangeResponse(PrometheusResponseBase):
    data: Optional[PrometheusQueryRangeData] = Field(
        default=None, description="Prometheus query range data"
    )


class MonitoringInstance(BaseModel):
    instance_id: str = Field(..., description="Instance ID")
    instance_name: Optional[str] = Field(default=None, description="Instance name")
    project_id: Optional[str] = Field(default=None, description="Project ID")
    project_name: Optional[str] = Field(default=None, description="Project name")
    host: Optional[str] = Field(default=None, description="Compute host")


class MonitoringInstancesResponse(BaseModel):
    instances: List[MonitoringInstance] = Field(default_factory=list)


class MonitoringMetricSeries(BaseModel):
    metric: Dict[str, str] = Field(..., description="Prometheus metric labels")
    values: List[List[Any]] = Field(default_factory=list, description="Time series data")


class MonitoringMetricsData(BaseModel):
    start: int = Field(..., description="Start timestamp in seconds")
    end: int = Field(..., description="End timestamp in seconds")
    step: int = Field(..., description="Query step in seconds")
    cpu: List[MonitoringMetricSeries] = Field(default_factory=list)
    memory: List[MonitoringMetricSeries] = Field(default_factory=list)
    network_rx: List[MonitoringMetricSeries] = Field(default_factory=list)
    network_tx: List[MonitoringMetricSeries] = Field(default_factory=list)
    disk_read: List[MonitoringMetricSeries] = Field(default_factory=list)
    disk_write: List[MonitoringMetricSeries] = Field(default_factory=list)
    disk_read_iops: List[MonitoringMetricSeries] = Field(default_factory=list)
    disk_write_iops: List[MonitoringMetricSeries] = Field(default_factory=list)


class MonitoringMetricsResponse(BaseModel):
    data: MonitoringMetricsData = Field(..., description="Monitoring metrics data")
