#!/usr/bin/env python3
#
# Push a job straight onto the worker queue, bypassing the REST server.
# The song must already be in the Min.io "queue" bucket as <songhash>.mp3
# (easiest way: upload it through the REST server once).
#
#   python3 worker/send-request.py <songhash> [model]
#
import json
import os
import sys

import redis

redisHost = os.getenv("REDIS_HOST") or "localhost"
redisPort = int(os.getenv("REDIS_PORT") or 6379)

if len(sys.argv) < 2:
    sys.exit(f"usage: {sys.argv[0]} <songhash> [model]")

job = {"hash": sys.argv[1], "model": sys.argv[2] if len(sys.argv) > 2 else None, "callback": None}
redis.StrictRedis(host=redisHost, port=redisPort, db=0).lpush("toWorker", json.dumps(job))
print(f"Queued {job}")
