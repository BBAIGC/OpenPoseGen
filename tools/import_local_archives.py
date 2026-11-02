"""Verify and import the three local original-PNG archives, keeping every ZIP.

Images stream directly to the asset drive. Existing identical files are reused;
conflicting originals stop the import and remain untouched. Catalog publication
happens only after every member and the unchanged source archives are verified.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import struct
import tempfile
from uuid import uuid4
import zipfile
import zlib

from downloader_import import DEFAULT_ROOT, download_lock, image_valid

ARCHIVES = {
    "pose": {"name": "AI 作图姿势库.zip", "directory": "img", "count": 1439},
    "identity": {"name": "AI 作图脸库.zip", "directory": "portrait/img", "count": 1499},
    "outfit": {"name": "AI 服装库.zip", "directory": "costume/img", "count": 1407},
}
DEFAULT_ARCHIVES = Path.cwd()
PNG = b"\x89PNG\r\n\x1a\n"
BLOCK = 1024 * 1024


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".import-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, separators=(",", ":"))
            stream.flush()
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def safe_target(root, relative):
    value = Path(relative)
    if value.anchor or ".." in value.parts or not value.parts or ":" in str(value) or "\x00" in str(value):
        raise ValueError(f"Unsafe local asset path: {relative}")
    current = root
    for part in value.parts:
        current = current / part
        if current.is_symlink() or (hasattr(current, "is_junction") and current.is_junction()):
            raise ValueError(f"Links are not imported: {relative}")
    if not current.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"Asset path leaves the library: {relative}")
    return current


def member_path(info):
    name = info.filename.replace("\\", "/")
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or ":" in name or "\x00" in name:
        raise ValueError(f"Unsafe ZIP member: {info.filename}")
    if stat.S_ISLNK(info.external_attr >> 16):
        raise ValueError(f"ZIP links are not imported: {info.filename}")
    return path


def archive_entries(archive, expected):
    selected, ignored = {}, 0
    with zipfile.ZipFile(archive) as package:
        for info in package.infolist():
            path = member_path(info)
            if info.is_dir() or "__MACOSX" in path.parts or path.name.startswith("._") or path.name == ".DS_Store":
                ignored += 1
                continue
            if len(path.parts) != 2 or path.parts[0] != "originals" or path.suffix.lower() != ".png":
                raise ValueError(f"Unexpected non-PNG archive member: {info.filename}")
            if info.flag_bits & 1:
                raise ValueError(f"Encrypted archive member: {info.filename}")
            if not 33 <= info.file_size <= 96 * 1024 * 1024:
                raise ValueError(f"Unexpected image size: {info.filename}")
            identifier = path.stem
            if identifier in selected:
                raise ValueError(f"Duplicate original in ZIP: {identifier}")
            selected[identifier] = info
    missing, extra = expected.keys() - selected.keys(), selected.keys() - expected.keys()
    if missing or extra:
        raise ValueError(f"ZIP/catalog ID mismatch: missing={sorted(missing)[:10]}, extra={sorted(extra)[:10]}")
    return selected, ignored


def png_dimensions(header):
    if len(header) < 33 or header[:8] != PNG or header[8:16] != b"\x00\x00\x00\rIHDR":
        raise ValueError("Invalid PNG header")
    width, height = struct.unpack(">II", header[16:24])
    if not width or not height or width * height > 80_000_000:
        raise ValueError("Unexpected PNG dimensions")
    if zlib.crc32(header[12:29]) & 0xffffffff != struct.unpack(">I", header[29:33])[0]:
        raise ValueError("Invalid PNG IHDR CRC")
    return width, height


def import_member(package, info, root, relative):
    """Read to EOF for ZIP CRC, verify SHA against the published destination."""
    target = safe_target(root, relative)
    existing = target.is_file()
    if target.exists() and not existing:
        raise FileExistsError(f"Destination is not a file: {relative}")
    existing_sha = sha256(target) if existing else None
    temporary = None
    output = None
    try:
        if not existing:
            target.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary = tempfile.mkstemp(prefix=".original-", suffix=".tmp", dir=target.parent)
            output = os.fdopen(descriptor, "wb")
        digest, checksum, size, header = hashlib.sha256(), 0, 0, bytearray()
        with package.open(info) as stream:
            while block := stream.read(BLOCK):
                if len(header) < 33:
                    header.extend(block[:33 - len(header)])
                digest.update(block)
                checksum = zlib.crc32(block, checksum)
                size += len(block)
                if size > info.file_size:
                    raise ValueError(f"ZIP member grew beyond its directory size: {info.filename}")
                if output is not None:
                    output.write(block)
        if output is not None:
            output.close()
            output = None
        width, height = png_dimensions(header)
        checksum &= 0xffffffff
        if size != info.file_size or checksum != info.CRC:
            raise ValueError(f"ZIP CRC/length mismatch: {info.filename}")
        expected_sha = digest.hexdigest()
        if existing:
            if existing_sha != expected_sha or sha256(target) != expected_sha:
                raise FileExistsError(f"Different original already exists; left untouched: {relative}")
            action = "reused"
        else:
            if sha256(Path(temporary)) != expected_sha:
                raise ValueError(f"Temporary-file SHA mismatch: {relative}")
            if target.exists():
                if not target.is_file() or sha256(target) != expected_sha:
                    raise FileExistsError(f"Destination changed during import: {relative}")
                action = "reused"
            else:
                os.replace(temporary, target)
                temporary = None
                action = "extracted"
            if sha256(target) != expected_sha or not image_valid(target):
                raise ValueError(f"Published PNG SHA/signature mismatch: {relative}")
        return {"image": relative, "bytes": size, "sha256": expected_sha,
                "crc32": f"{checksum:08x}", "width": width, "height": height, "action": action}
    finally:
        if output is not None:
            output.close()
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)


def import_archives(root=DEFAULT_ROOT, archives_root=DEFAULT_ARCHIVES):
    root, archives_root = Path(root).resolve(), Path(archives_root).resolve()
    catalog_path = safe_target(root, "catalog.json")
    catalog_bytes = catalog_path.read_bytes()
    catalog_sha = hashlib.sha256(catalog_bytes).hexdigest()
    catalog = json.loads(catalog_bytes)
    items = catalog["items"]
    if len(items) != 4345 or len({item["id"] for item in items}) != 4345:
        raise ValueError("Expected exactly 4345 unique source assets; user assets stay separate")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:8]
    manifest_relative = f".source/local-imports/{run_id}.json"
    manifest_path = safe_target(root, manifest_relative)
    progress_path = safe_target(root, "local-import-progress.json")
    report = {"version": 1, "run_id": run_id, "status": "running", "started_at": timestamp(),
              "root": str(root), "source_archives_kept": True, "archives": {}, "items": [], "counts": {},
              "catalog_before_sha256": catalog_sha, "manifest": manifest_relative}
    current_phase = "archive-check"

    def checkpoint():
        report["updated_at"] = timestamp()
        atomic_json(manifest_path, report)
        atomic_json(progress_path, {"status": report["status"], "phase": current_phase, "run_id": run_id,
                    "total": 4345, "completed": len(report["items"]), "counts": report["counts"],
                    "updated_at": report["updated_at"], "manifest": manifest_relative,
                    **({"error": report["error"]} if report.get("error") else {})})

    checkpoint()
    try:
        plans = []
        for kind, settings in ARCHIVES.items():
            archive = archives_root / settings["name"]
            source_stat = archive.stat()
            expected = {Path(item["image"]).stem: item for item in items if item["kind"] == kind}
            if len(expected) != settings["count"]:
                raise ValueError(f"Unexpected source catalog count: {kind}")
            entries, ignored = archive_entries(archive, expected)
            print(f"[ZIP] {archive.name}: hashing {source_stat.st_size:,} bytes, {len(entries)} originals", flush=True)
            archive_sha = sha256(archive)
            if (archive.stat().st_size, archive.stat().st_mtime_ns) != (source_stat.st_size, source_stat.st_mtime_ns):
                raise RuntimeError(f"Archive changed while hashing: {archive.name}")
            archive_record = {"path": str(archive), "name": archive.name, "bytes": source_stat.st_size,
                              "mtime_ns": source_stat.st_mtime_ns,
                              "mtime_utc": datetime.fromtimestamp(source_stat.st_mtime, timezone.utc).isoformat(),
                              "sha256": archive_sha, "images": len(entries), "ignored_entries": ignored,
                              "uncompressed_png_bytes": sum(info.file_size for info in entries.values())}
            report["archives"][kind] = archive_record
            report["counts"][kind] = {"total": len(entries), "verified": 0, "reused": 0, "extracted": 0, "bytes": 0}
            plans.append((kind, settings, archive, entries, expected))
            checkpoint()

        for kind, settings, archive, entries, expected in plans:
            current_phase = kind
            with zipfile.ZipFile(archive) as package:
                for stem, info in sorted(entries.items()):
                    relative = settings["directory"] + "/" + stem + ".png"
                    record = import_member(package, info, root, relative)
                    record.update(id=expected[stem]["id"], kind=kind, archive=archive.name,
                                  member=info.filename, archive_sha256=report["archives"][kind]["sha256"], format="PNG")
                    report["items"].append(record)
                    count = report["counts"][kind]
                    count["verified"] += 1
                    count[record["action"]] += 1
                    count["bytes"] += record["bytes"]
                    if len(report["items"]) % 100 == 0 or count["verified"] == count["total"]:
                        print(f"[{kind}] {count['verified']}/{count['total']} verified; reused={count['reused']} extracted={count['extracted']}; all={len(report['items'])}/4345", flush=True)
                        checkpoint()

        current_phase = "archive-retention-verification"
        checkpoint()
        for kind, _, archive, _, _ in plans:
            before = report["archives"][kind]
            print(f"[ZIP] Confirming unchanged retained archive: {archive.name}", flush=True)
            after = archive.stat()
            after_sha = sha256(archive)
            if after.st_size != before["bytes"] or after.st_mtime_ns != before["mtime_ns"] or after_sha != before["sha256"]:
                raise RuntimeError(f"Source ZIP changed during import: {archive.name}")
            before["verified_after_sha256"], before["retained"] = after_sha, True

        current_phase = "publish-index"
        checkpoint()
        if hashlib.sha256(catalog_path.read_bytes()).hexdigest() != catalog_sha:
            raise RuntimeError("Catalog changed during import; verified PNGs remain, index left untouched")
        backup_relative = f".source/backups/local-originals-{run_id}"
        backup = safe_target(root, backup_relative)
        backup.mkdir(parents=True, exist_ok=True)
        shutil.copy2(catalog_path, backup / "catalog.json")
        overlay_path = safe_target(root, ".source/local-originals.json")
        manifest_download = safe_target(root, ".download-manifest.json")
        for path in (overlay_path, manifest_download):
            if path.is_file():
                shutil.copy2(path, backup / path.name)
        overlay = json.loads(overlay_path.read_text(encoding="utf-8")) if overlay_path.is_file() else {"version": 1, "items": {}}
        if overlay.get("version") != 1 or not isinstance(overlay.get("items"), dict):
            raise ValueError("Unsupported existing local-originals overlay; original index kept")
        download_manifest = json.loads(manifest_download.read_text(encoding="utf-8")) if manifest_download.is_file() else {}
        by_id = {item["id"]: item for item in items}
        upgraded = 0
        for record in report["items"]:
            item = by_id[record["id"]]
            upgraded += item["image"] != record["image"]
            item["image"] = record["image"]
            local_source = {key: record[key] for key in ("archive", "member", "format", "sha256", "bytes", "archive_sha256", "crc32")}
            item.setdefault("metadata", {}).update(local_source=local_source, width=record["width"], height=record["height"])
            overlay["items"][item["id"]] = {"image": record["image"], "sha256": record["sha256"], "bytes": record["bytes"],
                                             "width": record["width"], "height": record["height"], "local_source": local_source}
            entry = download_manifest.setdefault(record["image"], {})
            entry.update(sha256=record["sha256"], bytes=record["bytes"], local_source=local_source)
        overlay.update(imported_at=timestamp(), manifest=manifest_relative, archives=report["archives"])
        catalog.setdefault("source", {})["local_originals"] = {"total": len(report["items"]), "format": "PNG", "manifest": manifest_relative,
                                                               "upgraded_paths": upgraded, "imported_at": overlay["imported_at"]}
        atomic_json(overlay_path, overlay)
        atomic_json(manifest_download, download_manifest)
        atomic_json(catalog_path, catalog)
        report.update(status="completed", completed_at=timestamp(), catalog_after_sha256=sha256(catalog_path),
                      catalog_backup=backup_relative + "/catalog.json", upgraded_paths=upgraded,
                      zip_crc_verified=len(report["items"]), destination_sha256_verified=len(report["items"]),
                      extracted_bytes=sum(row["bytes"] for row in report["items"] if row["action"] == "extracted"),
                      original_png_bytes=sum(row["bytes"] for row in report["items"]))
        current_phase = "completed"
        checkpoint()
        atomic_json(safe_target(root, "local-import-summary.json"), {key: value for key, value in report.items() if key != "items"})
        print(json.dumps({"status": report["status"], "counts": report["counts"], "upgraded_paths": upgraded,
                          "original_png_bytes": report["original_png_bytes"], "extracted_bytes": report["extracted_bytes"],
                          "manifest": manifest_relative, "ZIPs_retained_unchanged": True}, ensure_ascii=False), flush=True)
        return report
    except BaseException as error:
        report.update(status="failed", error=str(error) or type(error).__name__)
        checkpoint()
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--archives", type=Path, default=DEFAULT_ARCHIVES)
    args = parser.parse_args()
    with download_lock(args.root):
        import_archives(args.root, args.archives)


if __name__ == "__main__":
    main()
