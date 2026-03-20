# Copyright 2026 99cloud
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

from unittest.mock import Mock, patch

import pytest
from fastapi.exceptions import HTTPException

from skyline_apiserver.api.v1 import prometheus


def _profile(project_id: str, role_name: str = "member") -> Mock:
    profile = Mock()
    profile.project.id = project_id
    role = Mock()
    role.name = role_name
    profile.roles = [role]
    return profile


def test_normalize_window_default():
    start, end, step = prometheus._normalize_window(None, None, None)
    assert end > start
    assert end - start == 3600
    assert step >= 10


def test_normalize_window_reject_invalid_range():
    with pytest.raises(HTTPException) as exc:
        prometheus._normalize_window(100, 100, 10)
    assert exc.value.status_code == 400


def test_project_scope_forbidden_for_non_admin():
    profile = _profile("project-a", role_name="member")
    with pytest.raises(HTTPException) as exc:
        prometheus._get_effective_project_id(profile, "project-b")
    assert exc.value.status_code == 403


@patch("skyline_apiserver.api.v1.prometheus._prometheus_get")
def test_list_monitoring_instances(mock_prometheus_get):
    mock_prometheus_get.return_value = {
        "status": "success",
        "data": {
            "result": [
                {
                    "metric": {
                        "instance_id": "vm-1",
                        "instance_name": "instance-1",
                        "project_id": "project-a",
                        "project_name": "demo",
                        "instance": "compute-1:9177",
                    }
                }
            ]
        },
    }
    profile = _profile("project-a")
    result = prometheus.list_monitoring_instances(project_id=None, profile=profile)
    assert len(result.instances) == 1
    assert result.instances[0].instance_id == "vm-1"


@patch("skyline_apiserver.api.v1.prometheus._prometheus_get")
def test_get_instances_monitoring_metrics(mock_prometheus_get):
    mock_prometheus_get.return_value = {
        "status": "success",
        "data": {
            "resultType": "matrix",
            "result": [
                {
                    "metric": {"instanceId": "vm-1", "instanceName": "instance-1"},
                    "values": [[1710000000, "1.23"]],
                }
            ],
        },
    }
    profile = _profile("project-a")
    result = prometheus.get_instances_monitoring_metrics(
        instance_ids="vm-1",
        start=1710000000,
        end=1710003600,
        step=10,
        project_id=None,
        profile=profile,
    )
    assert result.data.start == 1710000000
    assert result.data.end == 1710003600
    assert len(result.data.cpu) == 1
    assert len(result.data.memory) == 1
