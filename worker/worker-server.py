#!/usr/bin/env python3
#
# Waveform-separation worker for the Music-Separation-as-a-Service lab.
#
# Waits for jobs on the Redis "toWorker" list, fetches the song from Min.io,
# runs DEMUCS on it, uploads the four separated tracks to the output bucket
# and (optionally) calls the requested webhook.
#
import glob
import json
import os
import platform
import shutil
import subprocess
import sys

import redis
import requests
from minio import Minio

##
## Configuration (environment variables override the local-dev defaults)
##
redisHost = os.getenv("REDIS_HOST") or "localhost"
redisPort = int(os.getenv("REDIS_PORT") or 6379)
minioHost = os.getenv("MINIO_HOST") or "localhost:9000"
minioUser = os.getenv("MINIO_USER") or "rootuser"
minioPasswd = os.getenv("MINIO_PASSWD") or "rootpass123"

QUEUE_BUCKET = os.getenv("QUEUE_BUCKET") or "queue"
OUTPUT_BUCKET = os.getenv("OUTPUT_BUCKET") or "output"
WORK_DIR = os.getenv("WORK_DIR") or "/tmp/demucs-work"
DEMUCS_CMD = os.getenv("DEMUCS_CMD") or f"{sys.executable} -m demucs.separate"
WORK_KEY = "toWorker"
TRACKS = ("bass", "drums", "vocals", "other")

redisClient = redis.StrictRedis(host=redisHost, port=redisPort, db=0)
minioClient = Minio(minioHost, access_key=minioUser, secret_key=minioPasswd, secure=False)

##
## Logging via the shared Redis "logging" list (see logs/README.md)
##
infoKey = "{}.worker.info".format(platform.node())
debugKey = "{}.worker.debug".format(platform.node())


def log_debug(message, key=debugKey):
    print("DEBUG:", message, file=sys.stdout, flush=True)
    try:
        redisClient.lpush("logging", f"{key}:{message}")
    except redis.RedisError:
        pass


def log_info(message, key=infoKey):
    print("INFO:", message, file=sys.stdout, flush=True)
    try:
        redisClient.lpush("logging", f"{key}:{message}")
    except redis.RedisError:
        pass


def ensure_bucket(bucket):
    if not minioClient.bucket_exists(bucket):
        minioClient.make_bucket(bucket)


def run_demucs(input_path, out_dir, model=None):
    cmd = DEMUCS_CMD.split() + ["--mp3", "--out", out_dir]
    if model:
        cmd += ["-n", model]
    cmd.append(input_path)
    log_debug(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        tail = (result.stderr or result.stdout)[-2000:]
        raise RuntimeError(f"demucs exited with {result.returncode}: {tail}")


def find_tracks(out_dir, songhash):
    """DEMUCS writes <out>/<model name>/<input stem>/<track>.mp3; the model dir varies."""
    found = {}
    for path in glob.glob(os.path.join(out_dir, "*", songhash, "*.mp3")):
        name = os.path.splitext(os.path.basename(path))[0]
        if name in TRACKS:
            found[name] = path
    return found


def do_callback(callback, songhash, ok):
    if not callback or not callback.get("url"):
        return
    payload = callback.get("data")
    try:
        requests.post(callback["url"], json=payload, timeout=10)
        log_debug(f"Callback to {callback['url']} for {songhash} sent (ok={ok})")
    except requests.RequestException as exp:
        # The spec says the callback may fail and that's fine.
        log_debug(f"Callback to {callback['url']} failed: {exp}")


def process(job):
    songhash = job["hash"]
    job_dir = os.path.join(WORK_DIR, songhash)
    input_path = os.path.join(job_dir, f"{songhash}.mp3")
    out_dir = os.path.join(job_dir, "output")
    os.makedirs(job_dir, exist_ok=True)
    try:
        log_info(f"Fetching {QUEUE_BUCKET}/{songhash}.mp3")
        minioClient.fget_object(QUEUE_BUCKET, f"{songhash}.mp3", input_path)

        log_info(f"Separating {songhash}")
        run_demucs(input_path, out_dir, job.get("model"))

        tracks = find_tracks(out_dir, songhash)
        missing = set(TRACKS) - set(tracks)
        if missing:
            raise RuntimeError(f"demucs output missing tracks: {sorted(missing)}")

        ensure_bucket(OUTPUT_BUCKET)
        for name, path in tracks.items():
            obj = f"{songhash}-{name}.mp3"
            minioClient.fput_object(OUTPUT_BUCKET, obj, path, content_type="audio/mpeg")
            log_debug(f"Uploaded {OUTPUT_BUCKET}/{obj}")

        minioClient.remove_object(QUEUE_BUCKET, f"{songhash}.mp3")
        log_info(f"Finished {songhash}")
        return True
    finally:
        shutil.rmtree(job_dir, ignore_errors=True)


def main():
    log_info(f"Worker starting (redis={redisHost}:{redisPort}, minio={minioHost})")
    while True:
        try:
            # REST lpush'es onto the left; pop from the right so jobs run FIFO.
            _, raw = redisClient.brpop(WORK_KEY, timeout=0)
        except redis.RedisError as exp:
            print(f"Redis error waiting for work: {exp}", file=sys.stderr, flush=True)
            continue
        try:
            job = json.loads(raw)
            songhash = job["hash"]
        except (ValueError, KeyError, TypeError) as exp:
            log_info(f"Dropping malformed job {raw!r}: {exp}")
            continue
        ok = False
        try:
            ok = process(job)
        except Exception as exp:
            log_info(f"Job {songhash} failed: {exp}")
        do_callback(job.get("callback"), songhash, ok)


if __name__ == "__main__":
    main()
