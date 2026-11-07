"""Build pinned main-site images from approved Git bytes, independent of Windows EOL."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import subprocess
import tarfile
from pathlib import Path

APPROVED_REVISION = "5e174e0cc38a8ff26e66406423b646c74e3c6b4f"
REPOSITORY = Path(__file__).resolve().parents[1]


def constant(source: Path, name: str):
    for node in ast.parse(source.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name for target in node.targets
        ):
            return ast.literal_eval(node.value)
    raise ValueError("approved integrity anchor missing")


def materialize_fixture_bytes(context: Path) -> list[dict[str, str]]:
    """Honor frozen digests using only LF/CRLF serialization in the build copy.

    Historical pins use mixed newline encodings even inside one fixture.
    Never change a pin, raster, JSON value, or the repository checkout.
    """
    fixture_root = context / "data/geochange-fixtures"
    validator = context / "scripts/validate_ndbi_science.py"
    pins = {
        "real-sentinel2-ndbi-v1": constant(validator, "TRUSTED_SHA256"),
        "real-sentinel2-ndwi-v5": constant(validator, "TRUSTED_NDWI_SHA256"),
        "real-sentinel2-v1": {
            "manifest.json": constant(context / "src/geochange/fixture.py", "MANIFEST_SHA256")
        },
    }
    evidence = []
    for folder, files in pins.items():
        for filename, expected in files.items():
            path = fixture_root / folder / filename
            original = path.read_bytes()
            candidates = [original]
            if path.suffix == ".json":
                lf = original.replace(b"\r\n", b"\n")
                candidates.extend([lf, lf.replace(b"\n", b"\r\n")])
            matched = next(
                (b for b in candidates if hashlib.sha256(b).hexdigest() == expected), None
            )
            if matched is None:
                raise ValueError("approved fixture cannot satisfy unchanged integrity anchor")
            if matched != original:
                path.write_bytes(matched)
            evidence.append(
                {
                    "path": path.relative_to(context).as_posix(),
                    "git_bytes_sha256": hashlib.sha256(original).hexdigest(),
                    "packaged_sha256": expected,
                }
            )
    return evidence


def export_source(destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    archive = destination.parent / "approved-source.tar"
    with archive.open("xb") as stream:
        subprocess.run(
            [
                "git",
                "-C",
                str(REPOSITORY),
                "-c",
                "core.autocrlf=false",
                "archive",
                APPROVED_REVISION,
            ],
            stdout=stream,
            check=True,
        )
    with tarfile.open(archive) as source:
        source.extractall(destination, filter="data")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, required=True, help="New directory outside repositories"
    )
    args = parser.parse_args()
    output = args.output.resolve()
    if any((parent / ".git").exists() for parent in [output, *output.parents]):
        parser.error("build evidence must be outside Git repositories")
    output.mkdir(parents=True, exist_ok=False)
    context = output / "context"
    export_source(context)
    fixtures = materialize_fixture_bytes(context)
    preflight = (REPOSITORY / "scripts/main_site_preflight.py").read_bytes()
    (context / "scripts/main_site_preflight.py").write_bytes(preflight)
    identity = {
        "approved_revision": APPROVED_REVISION,
        "preflight_sha256": hashlib.sha256(preflight).hexdigest(),
        "compose_sha256": hashlib.sha256(
            (REPOSITORY / "compose.main-site.yaml").read_bytes()
        ).hexdigest(),
        "source_archive_sha256": hashlib.sha256(
            (output / "approved-source.tar").read_bytes()
        ).hexdigest(),
    }
    (output / "fixture-packaging.json").write_text(json.dumps(fixtures, indent=2), encoding="utf-8")
    for kind, dockerfile in [
        ("api", "docker/Dockerfile.service"),
        ("web", "docker/Dockerfile.web"),
    ]:
        tag = f"taskpilot-main-{kind}:{APPROVED_REVISION[:12]}"
        command = [
            "docker",
            "build",
            "--label",
            "org.opencontainers.image.revision=" + APPROVED_REVISION,
            "--label",
            "taskpilot.main.preflight.sha256=" + identity["preflight_sha256"],
            "--label",
            "taskpilot.main.compose.sha256=" + identity["compose_sha256"],
            "-f",
            str(context / dockerfile),
            "-t",
            tag,
            str(context),
        ]
        with (output / f"{kind}-build.log").open("wb") as log:
            result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT)
        if result.returncode:
            print(f"{kind} build failed; inspect restricted build log")
            return result.returncode
        image = json.loads(subprocess.check_output(["docker", "image", "inspect", tag]))[0]
        identity[kind + "_image"] = image["Id"]
        print(f"{kind} image built: {image['Id']}", flush=True)
    (output / "candidate-source.json").write_text(json.dumps(identity, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
