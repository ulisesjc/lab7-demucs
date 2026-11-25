#!/usr/bin/env python3
#
# REST front-end for the Music-Separation-as-a-Service lab.
#
# Accepts base64-encoded MP3s, stores them in Min.io, and queues a job for
# the worker on a Redis list. Separated tracks are served back out of Min.io.
#
import base64
import hashlib
import io
import json
import os
import platform
import sys

import redis
from flask import Flask, Response, jsonify, request, send_file
from minio import Minio
from minio.error import S3Error

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
WORK_KEY = "toWorker"
TRACKS = ("bass", "drums", "vocals", "other")

redisClient = redis.StrictRedis(host=redisHost, port=redisPort, db=0)
minioClient = Minio(minioHost, access_key=minioUser, secret_key=minioPasswd, secure=False)

##
## Logging via the shared Redis "logging" list (see logs/README.md)
##
infoKey = "{}.rest.info".format(platform.node())
debugKey = "{}.rest.debug".format(platform.node())


def log_debug(message, key=debugKey):
    print("DEBUG:", message, file=sys.stdout)
    try:
        redisClient.lpush("logging", f"{key}:{message}")
    except redis.RedisError:
        pass


def log_info(message, key=infoKey):
    print("INFO:", message, file=sys.stdout)
    try:
        redisClient.lpush("logging", f"{key}:{message}")
    except redis.RedisError:
        pass


def ensure_buckets():
    for bucket in (QUEUE_BUCKET, OUTPUT_BUCKET):
        if not minioClient.bucket_exists(bucket):
            log_info(f"Creating bucket {bucket}")
            minioClient.make_bucket(bucket)


def track_object_name(songhash, track):
    """Map ('<hash>', 'vocals.mp3' | 'vocals') -> '<hash>-vocals.mp3', or None if invalid."""
    name = track[:-4] if track.endswith(".mp3") else track
    if name not in TRACKS:
        return None
    return f"{songhash}-{name}.mp3"


app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024 * 1024


@app.route("/", methods=["GET"])
def hello():
    # Health check for the GKE load balancer.
    return "<h1> Music Separation Server</h1><p> Use a valid endpoint </p>"


@app.route("/apiv1/separate", methods=["POST"])
def separate():
    data = request.get_json(force=True, silent=True)
    if not data or "mp3" not in data:
        return jsonify({"reason": "JSON body with base64 'mp3' field required"}), 400
    try:
        mp3 = base64.b64decode(data["mp3"], validate=True)
    except (ValueError, TypeError):
        return jsonify({"reason": "'mp3' is not valid base64"}), 400

    songhash = hashlib.sha224(mp3).hexdigest()
    try:
        ensure_buckets()
        minioClient.put_object(
            QUEUE_BUCKET, f"{songhash}.mp3", io.BytesIO(mp3), len(mp3),
            content_type="audio/mpeg",
        )
    except S3Error as exp:
        log_info(f"Min.io upload failed for {songhash}: {exp}")
        return jsonify({"reason": f"storage error: {exp}"}), 500

    job = {
        "hash": songhash,
        "model": data.get("model"),
        "callback": data.get("callback"),
    }
    redisClient.lpush(WORK_KEY, json.dumps(job))
    log_info(f"Queued {songhash} ({len(mp3)} bytes) for separation")
    return jsonify({"hash": songhash, "reason": "Song enqueued for separation"})


@app.route("/apiv1/queue", methods=["GET"])
@app.route("/apiv1/queue/", methods=["GET"])
def queue():
    entries = []
    for raw in redisClient.lrange(WORK_KEY, 0, -1):
        try:
            entries.append(json.loads(raw)["hash"])
        except (ValueError, KeyError, TypeError):
            entries.append(raw.decode("utf-8", errors="replace"))
    # Jobs are lpush'd and the worker brpop's from the right, so reverse to
    # show the next job to run first.
    entries.reverse()
    return jsonify({"queue": entries})


@app.route("/apiv1/track/<songhash>/<track>", methods=["GET"])
def track(songhash, track):
    obj = track_object_name(songhash, track)
    if obj is None:
        return jsonify({"reason": f"track must be one of {', '.join(TRACKS)}"}), 400
    try:
        resp = minioClient.get_object(OUTPUT_BUCKET, obj)
        try:
            content = resp.read()
        finally:
            resp.close()
            resp.release_conn()
    except S3Error as exp:
        if exp.code in ("NoSuchKey", "NoSuchBucket"):
            return jsonify({"reason": f"{obj} not found (still processing?)"}), 404
        return jsonify({"reason": f"storage error: {exp}"}), 500
    log_debug(f"Serving {obj}")
    return send_file(io.BytesIO(content), mimetype="audio/mpeg",
                     as_attachment=True, download_name=obj)


@app.route("/apiv1/remove/<songhash>/<track>", methods=["GET", "DELETE"])
def remove(songhash, track):
    obj = track_object_name(songhash, track)
    if obj is None:
        return jsonify({"reason": f"track must be one of {', '.join(TRACKS)}"}), 400
    try:
        minioClient.stat_object(OUTPUT_BUCKET, obj)
        minioClient.remove_object(OUTPUT_BUCKET, obj)
    except S3Error as exp:
        if exp.code in ("NoSuchKey", "NoSuchBucket"):
            return jsonify({"reason": f"{obj} not found"}), 404
        return jsonify({"reason": f"storage error: {exp}"}), 500
    log_info(f"Removed {obj}")
    return jsonify({"hash": songhash, "track": track, "reason": "Track removed"})


if __name__ == "__main__":
    log_info(f"REST server starting (redis={redisHost}:{redisPort}, minio={minioHost})")
    app.run(host="0.0.0.0", port=int(os.getenv("PORT") or 5000))
