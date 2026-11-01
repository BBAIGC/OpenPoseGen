"""Import the public RuiC reference catalog and download local assets on demand.

Images stay outside the plugin source tree. This module uses Python's standard
library only; it does not install the upstream application or download models.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

DEFAULT_ROOT = Path(r"F:\AIGC\AssetLibrary\RuiC-PoseAtlas")
COMMIT = "0e7851b05c2ab95c85e5d9d22c2c53130f3c4ba3"
REPOSITORY = "https://github.com/HRuiCcc/RuiC-PoseAtlas"
RAW = f"https://raw.githubusercontent.com/HRuiCcc/RuiC-PoseAtlas/{COMMIT}/"
UA = "Local-AssetLibrary/1.0 (+https://github.com/HRuiCcc/RuiC-PoseAtlas)"
SOURCE_FILES = ["data/poses.json", "data/costumes.json", "data/faces.json",
                "assets/js/costume-tags.js", "LICENSE", "README.md"]


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(temporary, path)


@contextmanager
def download_lock(root):
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".download.lock").open("a+b") as stream:
        stream.seek(0)
        if not os.fstat(stream.fileno()).st_size:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise RuntimeError("A download is already running for this library") from error
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def inside(root, relative):
    path = Path(relative)
    if path.anchor or ".." in path.parts or not path.parts or ":" in str(path) or "\x00" in str(path):
        raise ValueError(f"Unsafe asset path: {relative}")
    target = root / path
    current = root
    for part in path.parts:
        current = current / part
        if current.is_symlink() or (hasattr(current, "is_junction") and current.is_junction()):
            raise ValueError(f"Asset path contains a link: {relative}")
    return target


def names(records):
    return {row["key"]: row["names"] for row in records}


def local_originals(root):
    path = inside(root, ".source/local-originals.json")
    if not path.is_file():
        return None
    overlay = json.loads(path.read_text(encoding="utf-8"))
    if overlay.get("version") != 1 or not isinstance(overlay.get("items"), dict):
        raise ValueError("Unsupported local-originals overlay; original PNGs were not replaced")
    return overlay


def validate_local_original(root, relative, source):
    target = inside(root, relative)
    expected_sha, expected_size = source.get("sha256"), source.get("bytes")
    if (not isinstance(expected_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_sha)
            or type(expected_size) is not int or expected_size <= 0 or target.suffix.lower() != ".png"):
        raise ValueError(f"Invalid local-PNG verification record: {relative}")
    if not target.is_file() or target.stat().st_size != expected_size or not image_valid(target) or sha256(target) != expected_sha:
        raise ValueError(f"Local original is missing or changed; re-import its retained ZIP: {relative}")
    return target


def prepare_catalog(root=DEFAULT_ROOT, refresh=False):
    root = Path(root)
    source_dir = root / ".source"
    for relative in SOURCE_FILES:
        target = inside(source_dir, relative)
        if refresh or not target.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            with urlopen(Request(RAW + relative, headers={"User-Agent": UA}), timeout=45) as response:
                data = response.read()
            temporary = target.with_name(target.name + ".tmp")
            temporary.write_bytes(data)
            os.replace(temporary, target)
    for filename in ("pose-zh.json", "costume-zh.json", "portrait-zh.json"):
        target = source_dir / "anhao/data" / filename
        if refresh or not target.is_file():
            try:
                request = Request("https://ai-pose-gallery.anhao.app/data/" + filename,
                                  headers={"User-Agent": UA})
                with urlopen(request, timeout=30) as response:
                    data = response.read()
                if not isinstance(json.loads(data), list):
                    raise ValueError("Localized source is not an item list")
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary = target.with_name(target.name + ".tmp")
                temporary.write_bytes(data)
                os.replace(temporary, target)
            except (OSError, ValueError) as error:
                print(f"Localized metadata unavailable ({filename}): {error}", flush=True)
    poses, outfits, identities = [json.loads((source_dir / "data" / file).read_text(encoding="utf-8"))
                                  for file in ("poses.json", "costumes.json", "faces.json")]
    tag_text = (source_dir / "assets/js/costume-tags.js").read_text(encoding="utf-8")
    tag_names = {match[0]: {"zh": match[1], "en": match[2], "ja": match[3]}
                 for match in re.findall(r"\s*'?([\w-]+)'?:\s*\{\s*zh:\s*'([^']*)',\s*en:\s*'([^']*)',\s*ja:\s*'([^']*)'", tag_text)}
    angle_names, series_names = names(poses["angles"]), names(poses["series"])
    outfit_categories, outfit_groups = names(outfits["categories"]), names(outfits["groups"])
    sections, hairs = names(identities["sections"]), names(identities["hairs"])
    localized = {}
    for kind, filename in (("pose", "pose-zh.json"), ("outfit", "costume-zh.json"), ("identity", "portrait-zh.json")):
        path = source_dir / "anhao/data" / filename
        if path.exists():
            localized[kind] = {row["id"]: row for row in json.loads(path.read_text(encoding="utf-8"))}
    items = []
    for pose in poses["poses"]:
        for variant in pose["variants"]:
            angle = angle_names[variant["angle"]]
            items.append({"id": "ruic-pose-" + Path(variant["full"]).stem,
                          "title": f"{pose['names']['zh']} · {angle['zh']}", "kind": "pose",
                          "category": pose["category"],
                          "tags": [pose["category"], series_names[pose["series"]]["zh"], angle["zh"], pose["no"]],
                          "prompt": f"{pose['names']['en']}, {angle['en']} view",
                          "negative_prompt": "", "image": variant["full"], "thumbnail": variant["thumb"],
                          "source_url": f"https://hruiccc.github.io/RuiC-PoseAtlas/#pose={pose['id']}&angle={variant['angle']}",
                          "metadata": {"upstream_id": pose["id"], "series": pose["series"],
                                       "series_names": series_names[pose["series"]], "angle": variant["angle"],
                                       "angle_names": angle, "names": pose["names"], "number": pose["no"],
                                       "width": 900, "height": 1200, "license": "reference-creative-use",
                                       "prompt_source": "descriptive_metadata", "image_url": RAW + variant["full"],
                                       "thumbnail_url": RAW + variant["thumb"]}})
    for outfit in outfits["costumes"]:
        category, group = outfit_categories[outfit["cat"]], outfit_groups[outfit["group"]]
        translated = [tag_names.get(tag, {"zh": tag})["zh"] for tag in outfit["tags"]]
        items.append({"id": "ruic-outfit-" + outfit["id"], "title": f"{outfit['id']} · {category['zh']}季{group['zh']}" if outfit['cat'] in ('spring','summer','autumn','winter') else f"{outfit['id']} · {category['zh']}{group['zh']}",
                      "kind": "outfit", "category": category["zh"],
                      "tags": list(dict.fromkeys(translated + outfit["tags"] + [group["zh"]])),
                      "prompt": ", ".join(outfit["tags"]), "negative_prompt": "",
                      "image": outfit["image"], "thumbnail": outfit["thumb"],
                      "source_url": "https://hruiccc.github.io/RuiC-PoseAtlas/costume.html#costume=" + outfit["id"],
                      "metadata": {**outfit, "category_names": category, "group_names": group,
                                   "tag_names": {tag: tag_names[tag] for tag in outfit["tags"] if tag in tag_names},
                                   "license": "reference-creative-use", "prompt_source": "descriptive_metadata",
                                   "image_url": RAW + outfit["image"], "thumbnail_url": RAW + outfit["thumb"]}})
    for identity in identities["images"]:
        section, hair = sections[identity["sec"]], hairs[identity["hair"]]
        items.append({"id": "ruic-identity-" + identity["id"],
                      "title": f"{section['zh']} · {hair['zh']} · {identity['id']}",
                      "kind": "identity", "category": section["zh"],
                      "tags": [section["zh"], hair["zh"]] + ([identity["style"]] if identity.get("style") else []),
                      "prompt": "，".join([section["zh"], hair["zh"]]),
                      "negative_prompt": "", "image": identity["full"], "thumbnail": identity["thumb"],
                      "source_url": identity["full_remote"],
                      "metadata": {**identity, "section_names": section, "hair_names": hair,
                                   "width": identity["w"], "height": identity["h"],
                                   "license": "CC0-1.0", "prompt_source": "descriptive_metadata",
                                   "image_url": identity["full_remote"], "thumbnail_url": RAW + identity["thumb"]}})
    for item in items:
        raw_id = Path(item["image"]).stem
        row = localized.get(item["kind"], {}).get(raw_id)
        if row:
            item["metadata"]["localized_source"] = "https://ai-pose-gallery.anhao.app/"
            item["metadata"]["localized_record"] = row
            if item["kind"] == "outfit":
                item["title"] = row.get("服装名称", item["title"])
            elif item["kind"] == "identity":
                item["title"] = row.get("标题", item["title"])
                item["prompt"] = "，".join(dict.fromkeys(filter(None, [row.get("分区"), row.get("标题"), row.get("发型名称")])) )
    overlay = local_originals(root)
    if overlay:
        for item in items:
            original = overlay["items"].get(item["id"])
            if original:
                validate_local_original(root, original["image"], original)
                item["image"] = original["image"]
                item["metadata"].update(local_source=original["local_source"], width=original["width"], height=original["height"])
    counts = {kind: sum(item["kind"] == kind for item in items) for kind in ("pose", "outfit", "identity")}
    if counts != {"pose": 1439, "outfit": 1407, "identity": 1499} or len({item['id'] for item in items}) != 4345:
        raise ValueError(f"Unexpected source counts or duplicate IDs: {counts}")
    catalog = {"version": 1, "source": {"name": "RuiC-PoseAtlas", "repository": REPOSITORY,
                "homepage": "https://hruiccc.github.io/RuiC-PoseAtlas/", "commit": COMMIT,
                "total": len(items), "counts": counts, "imported_at": timestamp(),
                "localized_source": "https://ai-pose-gallery.anhao.app/" if localized else None,
                "license": "Code MIT; images retain their upstream licenses; local reference use only",
                "original_generation_parameters_available": False}, "items": items}
    if overlay:
        catalog["source"]["local_originals"] = {"total": len(overlay["items"]), "format": "PNG",
            "manifest": overlay.get("manifest"), "imported_at": overlay.get("imported_at")}
    atomic_json(root / "catalog.json", catalog)
    metadata_files = SOURCE_FILES + [path.relative_to(source_dir).as_posix() for path in (source_dir / "anhao").rglob("*") if path.is_file()]
    if overlay:
        metadata_files.append("local-originals.json")
    atomic_json(source_dir / "metadata-checksums.json", {relative: sha256(source_dir / relative) for relative in metadata_files})
    return catalog


def image_valid(path):
    if not path.is_file() or path.stat().st_size < 1024:
        return False
    with path.open("rb") as stream:
        signature = stream.read(12)
    return ((path.suffix.lower() == ".png" and signature.startswith(b"\x89PNG\r\n\x1a\n"))
            or (path.suffix.lower() in (".jpg", ".jpeg") and signature.startswith(b"\xff\xd8"))
            or (path.suffix.lower() == ".webp" and signature[:4] == b"RIFF" and signature[8:] == b"WEBP"))


def download_one(root, job, previous, stop):
    relative, url, expected_size = job[:3]
    local_source = job[3] if len(job) > 3 else None
    target = inside(root, relative)
    if local_source:
        validate_local_original(root, relative, local_source)
        return relative, {**(previous or {}), "sha256": local_source["sha256"], "bytes": local_source["bytes"],
                          "local_source": local_source}, False
    if image_valid(target) and (not expected_size or target.stat().st_size == expected_size):
        digest = sha256(target)
        if not previous or previous.get("sha256") == digest:
            return relative, {"sha256": digest, "bytes": target.stat().st_size, "url": url}, False
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".part")
    last_error = None
    for attempt in range(4):
        if stop.is_set() or (root / ".pause-download").exists():
            raise InterruptedError("Download paused")
        try:
            offset = part.stat().st_size if part.exists() else 0
            headers = {"User-Agent": UA, "Accept-Encoding": "identity"}
            if offset:
                headers["Range"] = f"bytes={offset}-"
            with urlopen(Request(url, headers=headers), timeout=45) as response:
                append = response.status == 206 and offset > 0
                if append and not response.headers.get("Content-Range", "").startswith(f"bytes {offset}-"):
                    raise ValueError("Unexpected content range")
                if not append:
                    offset = 0
                length = response.headers.get("Content-Length")
                total = offset + int(length) if length else None
                with part.open("ab" if append else "wb") as output:
                    while True:
                        if stop.is_set() or (root / ".pause-download").exists():
                            raise InterruptedError("Download paused")
                        block = response.read(128 * 1024)
                        if not block:
                            break
                        output.write(block)
            size = part.stat().st_size
            if (total and size != total) or (expected_size and size != expected_size):
                raise ValueError(f"Incomplete image: {size}, expected {expected_size or total}")
            # Validate using the intended extension before publishing the file.
            with part.open("rb") as stream:
                signature = stream.read(12)
            if size < 1024 or not (signature.startswith(b"\x89PNG\r\n\x1a\n") or signature.startswith(b"\xff\xd8") or (signature[:4] == b"RIFF" and signature[8:] == b"WEBP")):
                part.unlink(missing_ok=True)
                raise ValueError("Response is not an image")
            digest = sha256(part)
            os.replace(part, target)
            return relative, {"sha256": digest, "bytes": size, "url": url}, True
        except InterruptedError:
            raise
        except (OSError, ValueError) as error:
            last_error = error
            if isinstance(error, HTTPError) and error.code == 416:
                part.unlink(missing_ok=True)
            if attempt < 3:
                stop.wait(min(2 ** attempt, 4))
    raise RuntimeError(f"{relative}: {last_error}")


def download_catalog(root=DEFAULT_ROOT, workers=8, mode="all", limit=None, refresh=False):
    root = Path(root)
    catalog = prepare_catalog(root, refresh)
    manifest_path = root / ".download-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    stop = threading.Event()
    stages = ["thumbnail", "image"] if mode == "all" else ["thumbnail" if mode == "thumbnails" else "image"]
    errors = []
    state = {"status": "running", "started_at": timestamp(), "workers": workers, "asset_total": len(catalog["items"])}
    thumbnails = {item["thumbnail"] for item in catalog["items"]}
    originals = {item["image"] for item in catalog["items"]}
    last_saved = 0
    def save_progress():
        nonlocal last_saved
        state.update(updated_at=timestamp(), errors=errors[-30:])
        atomic_json(manifest_path, manifest)
        atomic_json(root / "download-state.json", state)
        atomic_json(root / "download-progress.json", {
            "total": len(catalog["items"]), "downloaded": len(originals & manifest.keys()),
            "thumbnail_downloaded": len(thumbnails & manifest.keys()), "status": state["status"],
            "phase": state.get("phase"), "failed": len(errors), "updated_at": state["updated_at"],
            "pid": os.getpid(), "bytes": sum(record["bytes"] for record in manifest.values())})
        last_saved = time.monotonic()
    save_progress()
    try:
        for stage in stages:
            jobs = [(item[stage], item["metadata"][stage + "_url"],
                     item["metadata"].get("bytes") if stage == "image" and item["kind"] == "identity" else None,
                     item["metadata"].get("local_source") if stage == "image" else None)
                    for item in catalog["items"]]
            if limit:
                jobs = jobs[:limit]
            state.update(phase=stage, total=len(jobs), completed=0, downloaded=0, reused=0, failed=0, bytes_downloaded=0)
            pending = iter(jobs)
            def submit(executor):
                job = next(pending, None)
                return executor.submit(download_one, root, job, manifest.get(job[0]), stop) if job else None
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="asset-download") as executor:
                active = {future for _ in range(workers * 2) if (future := submit(executor)) is not None}
                while active:
                    completed, active = wait(active, timeout=2, return_when=FIRST_COMPLETED)
                    for future in completed:
                        try:
                            relative, record, downloaded = future.result()
                            manifest[relative] = record
                            state["downloaded" if downloaded else "reused"] += 1
                            if downloaded:
                                state["bytes_downloaded"] += record["bytes"]
                        except InterruptedError:
                            stop.set()
                            state["status"] = "paused"
                        except Exception as error:
                            state["failed"] += 1
                            errors.append(str(error))
                        state["completed"] += 1
                        if not stop.is_set() and (future := submit(executor)) is not None:
                            active.add(future)
                    if time.monotonic() - last_saved >= 10 or not active:
                        save_progress()
                        print(f"{stage}: {state['completed']}/{state['total']} downloaded={state['downloaded']} reused={state['reused']} failed={state['failed']}", flush=True)
            if stop.is_set():
                break
        if not stop.is_set():
            state["status"] = "completed" if not errors else "incomplete"
    except KeyboardInterrupt:
        stop.set()
        state["status"] = "paused"
    finally:
        save_progress()
    return not errors and not stop.is_set()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("catalog", "thumbnails", "full", "all", "verify"), default="all", nargs="?")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--refresh-metadata", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.workers <= 16:
        parser.error("workers must be between 1 and 16")
    if args.limit is not None and args.limit < 1:
        parser.error("limit must be a positive integer")
    if args.mode not in ("catalog", "verify"):
        with download_lock(args.root):
            return int(not download_catalog(args.root, args.workers, args.mode, args.limit, args.refresh_metadata))
    catalog = prepare_catalog(args.root, args.refresh_metadata)
    if args.mode == "catalog":
        print(json.dumps(catalog["source"], ensure_ascii=False))
        return 0
    if args.mode == "verify":
        manifest_path = args.root / ".download-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
        invalid = [relative for relative, record in manifest.items()
                   if not image_valid(inside(args.root, relative)) or sha256(inside(args.root, relative)) != record["sha256"]]
        missing = [item[key] for item in catalog["items"] for key in ("thumbnail", "image") if not inside(args.root, item[key]).is_file()]
        report = {"verified": len(manifest) - len(invalid), "invalid": invalid, "missing": missing, "total": len(catalog["items"]) * 2}
        atomic_json(args.root / "verification.json", report)
        print(json.dumps({key: len(value) if isinstance(value, list) else value for key, value in report.items()}))
        return int(bool(invalid or missing))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
