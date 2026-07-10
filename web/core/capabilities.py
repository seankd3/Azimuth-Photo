"""Dependency-only availability for optional local AI feature packs.

Detection deliberately never imports a package or inspects model files.  It is
safe to call during startup and from status endpoints on a minimal install.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib.util import find_spec
import platform
import sys


@dataclass(frozen=True)
class Capability:
    key: str
    label: str
    requirements_file: str
    imports: tuple[str, ...]
    optional_imports: tuple[str, ...] = ()

    @property
    def install_command(self) -> str:
        return f"python -m pip install -r {self.requirements_file}"


CAPABILITIES = {
    "search": Capability(
        key="search",
        label="AI search",
        requirements_file="requirements-ai-search.txt",
        imports=("torch", "sentence_transformers", "transformers", "huggingface_hub", "accelerate", "qwen_vl_utils"),
        optional_imports=("bitsandbytes",),
    ),
    "people": Capability(
        key="people",
        label="People recognition",
        requirements_file="requirements-ai-people.txt",
        imports=("insightface", "onnxruntime", "cv2"),
    ),
    "captions": Capability(
        key="captions",
        label="AI captions",
        requirements_file="requirements-ai-captions.txt",
        imports=("torch", "transformers", "huggingface_hub", "accelerate", "qwen_vl_utils", "bitsandbytes"),
    ),
    "develop_ai": Capability(
        key="develop_ai",
        label="Develop subject masks",
        requirements_file="requirements-ai-develop.txt",
        imports=("onnxruntime",),
    ),
}


def _linux_x86_64() -> bool:
    return sys.platform.startswith("linux") and platform.machine().lower() in {"x86_64", "amd64"}


def capability_status(key: str) -> dict:
    capability = CAPABILITIES[key]
    missing = [name for name in capability.imports if find_spec(name) is None]
    optional_missing = [name for name in capability.optional_imports if find_spec(name) is None]
    available = not missing
    if available:
        message = f"{capability.label} dependencies are installed."
    else:
        message = (
            f"{capability.label} is not installed. Existing library data remains available; "
            f"install the optional pack to create new results."
        )
    return {
        "key": capability.key,
        "label": capability.label,
        "available": available,
        "missing": missing,
        "optional_missing": optional_missing,
        "requirements_file": capability.requirements_file,
        "install_command": capability.install_command,
        "runtime_install": False,
        "platform": {
            "linux_x86_64": _linux_x86_64(),
            "bitsandbytes_supported": _linux_x86_64(),
        },
        "message": message,
    }


def unavailable_response(key: str) -> dict:
    status = capability_status(key)
    return {
        "error": f"{status['label']} optional pack is not installed",
        "capability": status,
        "install_command": status["install_command"],
    }
