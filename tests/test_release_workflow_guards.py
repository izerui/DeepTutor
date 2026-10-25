"""Independent deployment policy: local images, no upstream publication CI."""

from pathlib import Path

import pytest
import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "filename",
    ["docker-release.yml", "pypi-release.yml", "tests.yml", "repository-hygiene.yml"],
)
def test_upstream_publication_workflows_remain_retired(filename: str) -> None:
    assert not (REPOSITORY_ROOT / ".github" / "workflows" / filename).exists()


def test_local_develop_image_keeps_deployment_contract() -> None:
    path = REPOSITORY_ROOT / ".github" / "workflows" / "develop-image.yml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    # PyYAML's YAML 1.1 loader interprets the GitHub Actions "on" key as True.
    triggers = document[True]
    assert set(triggers) == {"push", "workflow_dispatch"}
    assert triggers["push"]["branches"] == ["develop"]
    job = document["jobs"]["docker"]
    assert job["if"] == "github.ref == 'refs/heads/develop'"
    assert {
        (variant["context_path"], variant["tag_suffix"])
        for variant in job["strategy"]["matrix"]["variant"]
    } == {("", ""), ("/kaoyan", "-kaoyan")}
    build = next(
        step["with"] for step in job["steps"]
        if step.get("uses", "").startswith("docker/build-push-action@")
    )
    assert build["target"] == "production"
    assert build["platforms"] == "linux/amd64"
    assert build["push"] is True
    args = dict(line.split("=", 1) for line in build["build-args"].splitlines())
    assert args["NEXT_PUBLIC_CONTEXT_PATH"] == "${{ matrix.variant.context_path }}"
    assert args["DEEPTUTOR_REQUIREMENTS_FILE"] == "requirements/deployment.txt"
    assert args["DEEPTUTOR_VERIFY_DEPLOYMENT_EXTRAS"] == "1"
    assert "cmake" in args["DEEPTUTOR_BUILD_APT_PACKAGES"].split()
    assert {"ffmpeg", "texlive-latex-base", "dvisvgm"} <= set(
        args["DEEPTUTOR_RUNTIME_APT_PACKAGES"].split()
    )
    assert "fonts-noto-cjk" in args["DEEPTUTOR_RUNTIME_FONT_PACKAGES"].split()
    metadata = next(step["with"] for step in job["steps"] if step.get("id") == "meta")
    assert metadata["tags"].splitlines() == [
        "type=raw,value=develop${{ matrix.variant.tag_suffix }}",
        "type=sha,prefix=develop${{ matrix.variant.tag_suffix }}-",
    ]
