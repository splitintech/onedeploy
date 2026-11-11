"""Strict, secret-free inputs for the opt-in AutoDeploy engine."""
from dataclasses import asdict, dataclass
import hashlib
import json
import re

from onedeploy.errors import OnedeployError


def invalid(message):
    raise OnedeployError("invalid_config", message)


def strict(data, required, optional=()):
    if not isinstance(data, dict) or set(data) - set(required) - set(optional) or set(required) - set(data):
        invalid("Missing or unknown AutoDeploy fields.")


def positive(value, name):
    if type(value) is not int or value < 1:
        invalid(f"{name} must be a positive integer.")


def digest(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class DeploymentConfig:
    deployment_id: str
    repository: str
    branch: str
    target: str
    resources: dict
    max_workers: int
    budget_usd: float
    schema_version: int = 1
    idle_timeout_seconds: int = 60
    candidate_timeout_seconds: int = 300

    @classmethod
    def from_dict(cls, data):
        strict(data, ("deployment_id", "repository", "branch", "target", "resources", "max_workers", "budget_usd"),
               ("schema_version", "idle_timeout_seconds", "candidate_timeout_seconds"))
        if type(data.get("schema_version", 1)) is not int or data.get("schema_version", 1) != 1:
            invalid("Unsupported AutoDeploy schema version.")
        for field in ("deployment_id", "target"):
            if not isinstance(data[field], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", data[field]):
                invalid(f"Invalid {field}.")
        if not isinstance(data["repository"], str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", data["repository"]):
            invalid("Invalid repository.")
        branch = data["branch"]
        if not isinstance(branch, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./-]{0,199}", branch) or ".." in branch or "//" in branch or branch.endswith(("/", ".", ".lock")):
            invalid("Invalid branch.")
        resources = data["resources"]
        strict(resources, ("cpu", "memory_gib", "disk_gib", "gpu_count", "gpu_type"))
        for field in ("cpu", "memory_gib", "disk_gib", "gpu_count"):
            positive(resources[field], field)
        if not isinstance(resources["gpu_type"], str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", resources["gpu_type"]):
            invalid("Invalid GPU type.")
        positive(data["max_workers"], "max_workers")
        budget = data["budget_usd"]
        if type(budget) not in (int, float) or not 0 < budget <= 100000:
            invalid("A finite positive budget_usd up to 100000 is required.")
        for field, default in (("idle_timeout_seconds", 60), ("candidate_timeout_seconds", 300)):
            positive(data.get(field, default), field)
        return cls(**{**data, "resources": dict(resources)})

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class ReleaseSpec:
    source_commit: str
    image_digest: str
    model_digest: str
    evidence_digest: str

    @classmethod
    def from_dict(cls, data):
        strict(data, ("source_commit", "image_digest", "model_digest", "evidence_digest"))
        if not isinstance(data["source_commit"], str) or not re.fullmatch(r"[0-9a-f]{40}", data["source_commit"]):
            invalid("source_commit must be a full lowercase Git SHA.")
        image = data["image_digest"]
        if not isinstance(image, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]*@sha256:[0-9a-f]{64}", image) or "://" in image:
            invalid("image_digest must be an immutable registry image digest.")
        for field in ("model_digest", "evidence_digest"):
            if not isinstance(data[field], str) or not re.fullmatch(r"[0-9a-f]{64}", data[field]):
                invalid(f"{field} must be a SHA-256 digest.")
        return cls(**data)

    def to_dict(self):
        return asdict(self)
